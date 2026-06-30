"""
mcp_server.py — Sprint Capacity MCP Server
==========================================
Exposes sprint capacity analysis tools via the Model Context Protocol (MCP).

Design:
  - Transport: stdio (stdin/stdout). The server runs as a subprocess launched by
    any MCP client (Google ADK, LangGraph, LlamaIndex, Claude Desktop, etc.).
    No network port or credentials are required to run this server.
  - State management: load_sprint_data computes metrics and writes them to a
    JSON cache file (sprint_cache.json) next to this script. All other tools
    read from that cache file. This means:
      a) Each agent in a multi-agent pipeline can open a fresh MCP connection
         without losing data computed by an earlier agent.
      b) The cache is human-readable and auditable between runs.
      c) The server process itself holds no in-memory state that could be lost
         if the process restarts.
  - Separation of concerns: each tool has exactly one responsibility and one
    docstring that acts as its LLM-facing description. Descriptions are written
    to guide the model on WHEN to call each tool, not just what it returns.

Security: No API keys, passwords, or user credentials anywhere in this file.

To run standalone for testing:
    python sprint_data/mcp_server.py
The server listens on stdin for JSON-RPC messages in MCP format.
"""

import asyncio
import json
import os
import pandas as pd
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ── Cache file ─────────────────────────────────────────────────────────────────
# Placed next to this script so the path is deterministic regardless of CWD.
# Written by load_sprint_data; read by every other tool.
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprint_cache.json")

# ── MCP server instance ────────────────────────────────────────────────────────
server = Server("sprint-capacity-server")


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry
# The list returned here is what the MCP client sees when it connects.
# Descriptions are read by the calling LLM to decide which tool to invoke.
# ─────────────────────────────────────────────────────────────────────────────
@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return all tools exposed by this MCP server."""
    return [
        Tool(
            name="load_sprint_data",
            description=(
                "Load sprint workforce data from an Excel workbook and compute "
                "capacity metrics for every employee. MUST be called first — all "
                "other tools fail if this has not been called. Writes results to a "
                "local cache file so subsequent agents can read it without reloading."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "excel_path": {
                        "type": "string",
                        "description": "Absolute path to sprint_data.xlsx"
                    }
                },
                "required": ["excel_path"]
            },
        ),
        Tool(
            name="get_team_overview",
            description=(
                "Return high-level team capacity metrics: team size, total SP assigned "
                "vs total max SP, team utilisation %, and employees already at 85%+ "
                "capacity. Call this first to understand the overall landscape before "
                "drilling into individual employees."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="identify_capacity_risks",
            description=(
                "Find employees who are OVER capacity (SP assigned > max SP) or have "
                "LESS than 2 SP of headroom (near-capacity). Returns a list of risk "
                "records with issue type, SP overage, and exact headroom. Use after "
                "get_team_overview to narrow in on problem areas."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="get_employee_details",
            description=(
                "Return the complete capacity breakdown for one named employee: "
                "available days, max SP, SP assigned, remaining SP, and status. "
                "Use after identify_capacity_risks to get exact numbers for employees "
                "flagged as at-risk."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "employee_name": {
                        "type": "string",
                        "description": "Employee name (case-insensitive)"
                    }
                },
                "required": ["employee_name"],
            },
        ),
        Tool(
            name="suggest_work_rebalancing",
            description=(
                "Find the best team member to absorb story points from an overloaded "
                "employee. Returns the top candidate ranked by available headroom, "
                "their capacity after the transfer, and up to two alternatives. "
                "Only call this when identify_capacity_risks found over_capacity employees."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "from_employee": {
                        "type": "string",
                        "description": "Name of the overloaded employee to move work FROM"
                    },
                    "points_to_move": {
                        "type": "number",
                        "description": "Story points to redistribute to another team member"
                    }
                },
                "required": ["from_employee", "points_to_move"],
            },
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# Routes incoming tool calls to handler functions.
# ─────────────────────────────────────────────────────────────────────────────
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch tool calls to their handler and return results as TextContent."""

    if name == "load_sprint_data":
        return await _load_sprint_data(arguments)

    # All tools below require cached data — guard and return helpful error
    if not os.path.exists(CACHE_FILE):
        return [TextContent(type="text", text=json.dumps({
            "error": "Sprint data not loaded. Call load_sprint_data first.",
            "hint":  "Pass the absolute path to sprint_data.xlsx as excel_path."
        }))]

    with open(CACHE_FILE) as f:
        cache = json.load(f)
    rows, config = cache["rows"], cache["config"]

    if name == "get_team_overview":
        return _get_team_overview(rows, config)
    elif name == "identify_capacity_risks":
        return _identify_capacity_risks(rows)
    elif name == "get_employee_details":
        return _get_employee_details(rows, arguments.get("employee_name", ""))
    elif name == "suggest_work_rebalancing":
        return _suggest_work_rebalancing(
            rows,
            arguments.get("from_employee", ""),
            float(arguments.get("points_to_move", 0)),
        )

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _load_sprint_data(arguments: dict) -> list[TextContent]:
    """
    Read Excel workbook, compute capacity metrics per employee, write cache.

    Capacity formula:
        available_days = sprint_days - planned_leave - unplanned_leave - holidays
        max_sp         = available_days * points_per_day
        remaining_sp   = max_sp - sp_assigned
    """
    excel_path = arguments.get("excel_path", "")
    if not os.path.exists(excel_path):
        return [TextContent(type="text", text=json.dumps({
            "error": f"File not found: {excel_path}",
            "hint":  "Run create_sprint_excel.py to generate the file first."
        }))]

    # Load both sheets from the workbook
    df  = pd.read_excel(excel_path, sheet_name="Sprint Capacity")
    cfg = pd.read_excel(excel_path, sheet_name="Config")

    # Config sheet: key-value pairs drive sprint constants
    config = dict(zip(cfg["Key"], cfg["Value"]))
    sprint_days    = int(config.get("Sprint Working Days", 10))
    points_per_day = float(config.get("Points per Day", 1.5))

    rows = []
    for _, row in df.iterrows():
        planned   = int(row.get("Planned_Leaves",   0))
        unplanned = int(row.get("Unplanned_Leaves", 0))
        holidays  = int(row.get("Holidays",         0))

        # Core capacity arithmetic — clip at 0 to handle edge cases
        available_days = max(sprint_days - planned - unplanned - holidays, 0)
        capacity_pct   = round(available_days / sprint_days * 100, 1)
        max_sp         = round(available_days * points_per_day, 1)
        sp_assigned    = float(row.get("SP_Assigned", 0))
        remaining_sp   = round(max_sp - sp_assigned, 1)

        # Status label for quick human and LLM interpretation
        if sp_assigned > max_sp:
            status = "Over capacity"
        elif sp_assigned == max_sp:
            status = "At capacity"
        else:
            status = "Under capacity"

        rows.append({
            "Employee":        str(row["Employee"]),
            "Sprint Days":     sprint_days,
            "Planned Leave":   planned,
            "Unplanned Leave": unplanned,
            "Holidays":        holidays,
            "Available Days":  available_days,
            "Capacity %":      f"{capacity_pct}%",
            "Max SP":          max_sp,
            "SP Assigned":     sp_assigned,
            "Remaining SP":    remaining_sp,
            "Status":          status,
        })

    # Write to file-based cache so future MCP connections from other agents
    # can read the data without re-parsing the Excel file
    with open(CACHE_FILE, "w") as f:
        json.dump(
            {"rows": rows, "config": {k: str(v) for k, v in config.items()}},
            f,
            indent=2,
        )

    return [TextContent(type="text", text=json.dumps({
        "status":              "success",
        "employees_loaded":    len(rows),
        "sprint_days":         sprint_days,
        "velocity_sp_per_day": points_per_day,
        "cache_written":       CACHE_FILE,
    }, indent=2))]


def _get_team_overview(rows: list, config: dict) -> list[TextContent]:
    """
    Compute team-level capacity snapshot.
    Flags employees at >= 85% utilisation — not just those already over.
    This early-warning threshold catches near-capacity cases before they become problems.
    """
    total_assigned = sum(r["SP Assigned"] for r in rows)
    total_max      = sum(r["Max SP"]      for r in rows)
    utilisation    = round(total_assigned / total_max * 100, 1) if total_max else 0
    # 85% threshold: flags employees close to the edge so they don't become over-capacity
    # after inevitable mid-sprint scope changes
    at_risk = [r["Employee"] for r in rows if r["SP Assigned"] >= r["Max SP"] * 0.85]

    # Config values are stored as strings (e.g. "10.0") because pandas reads
    # Excel integers as floats; convert via float() before int() to avoid ValueError.
    return [TextContent(type="text", text=json.dumps({
        "sprint_working_days":             int(float(config.get("Sprint Working Days", 10))),
        "team_size":                       len(rows),
        "total_sp_assigned":               total_assigned,
        "total_max_sp":                    total_max,
        "team_utilisation_pct":            utilisation,
        "employees_near_or_over_capacity": at_risk,
    }, indent=2))]


def _identify_capacity_risks(rows: list) -> list[TextContent]:
    """
    Find employees over or near their capacity limit.
    Near-capacity threshold: < 2 SP remaining — not enough buffer for any scope creep.
    """
    risks = []
    for r in rows:
        headroom = r["Remaining SP"]
        if r["SP Assigned"] > r["Max SP"]:
            # Over capacity: hard violation — must be addressed before sprint starts
            risks.append({
                "employee": r["Employee"],
                "issue":    "over_capacity",
                "sp_over":  round(r["SP Assigned"] - r["Max SP"], 1),
                "headroom": headroom,
            })
        elif headroom < 2.0:
            # Near-capacity: soft risk — vulnerable to unplanned leave or late additions
            risks.append({
                "employee": r["Employee"],
                "issue":    "near_capacity",
                "headroom": headroom,
            })

    if not risks:
        return [TextContent(type="text", text=json.dumps({
            "status": "all_clear",
            "detail": "No employees over or near capacity. Sprint looks healthy."
        }))]
    return [TextContent(type="text", text=json.dumps(risks, indent=2))]


def _get_employee_details(rows: list, employee_name: str) -> list[TextContent]:
    """Return the full capacity record for one employee by name (case-insensitive match)."""
    row = next(
        (r for r in rows if r["Employee"].lower() == employee_name.strip().lower()),
        None,
    )
    if not row:
        return [TextContent(type="text", text=json.dumps({
            "error":     f"Employee '{employee_name}' not found.",
            "available": [r["Employee"] for r in rows],
        }))]
    return [TextContent(type="text", text=json.dumps(row, indent=2))]


def _suggest_work_rebalancing(rows: list, from_employee: str, points: float) -> list[TextContent]:
    """
    Find the best recipient for redistributed story points.
    Ranks candidates by remaining SP descending — most headroom goes first.
    Excludes the source employee from candidates.
    """
    candidates = [
        r for r in rows
        if r["Employee"].lower() != from_employee.strip().lower()
        and r["Remaining SP"] >= points  # only suggest if they can absorb all the points
    ]
    candidates.sort(key=lambda r: r["Remaining SP"], reverse=True)

    if not candidates:
        return [TextContent(type="text", text=json.dumps({
            "status": "no_candidate",
            "detail": f"No team member has {points} SP of free headroom for rebalancing.",
            "hint":   "Consider breaking the work across two people or deferring a story."
        }))]

    best = candidates[0]
    return [TextContent(type="text", text=json.dumps({
        "recommended_recipient":    best["Employee"],
        "current_remaining_sp":     best["Remaining SP"],
        "remaining_after_transfer": round(best["Remaining SP"] - points, 1),
        "alternative_candidates":   [
            {"employee": c["Employee"], "available_sp": c["Remaining SP"]}
            for c in candidates[1:3]      # up to two alternatives
        ],
    }, indent=2))]


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
async def main() -> None:
    """Start the MCP server on stdio transport and block until the client disconnects."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
