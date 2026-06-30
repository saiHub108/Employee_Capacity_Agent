# Sprint Capacity Planning — Multi-Agent System

### Google ADK + Model Context Protocol (MCP) | Enterprise AI Agents

Automates the sprint capacity calculation every scrum team runs manually before each sprint. Powered by a four-agent pipeline that loads your Excel data, identifies delivery risks, suggests work redistribution, and writes a scrum-ready risk report — all via coordinated AI agents and standardised MCP tools.

---

## The Problem

Every two weeks, agile teams answer the same question:

> **How much can each person actually do this sprint?**

The manual process — subtracting leave days and holidays, estimating story-point headroom, flagging over-commitment — is repetitive, error-prone, and eats planning time. This system does it automatically, with explainable reasoning at every step.

---

## The Solution

A **multi-agent system** built with Google ADK where four specialised agents coordinate through shared state, each calling tools exposed by a local **MCP server**:

1. **DataLoaderAgent** — loads the Excel workbook and confirms sprint configuration
2. **RiskAnalystAgent** — queries team overview, identifies over-capacity and near-capacity employees, drills into details
3. **RebalancingAgent** — finds the best recipient for redistributed story points when an employee is over capacity
4. **ReportAgent** — synthesises all findings into an actionable sprint risk report

The key enterprise value: **agents decide what to investigate**, not a fixed script. If all employees are healthy, agents short-circuit early. If one is over capacity, the rebalancing agent automatically finds who can absorb the work and by how much. That is agent behaviour on a real business problem.

---

## Architecture

```
User trigger
    |
    v
SprintCapacityOrchestrator (SequentialAgent)
    |
    |-- [1] DataLoaderAgent --------> MCP Server: load_sprint_data
    |        output_key: data_status              (Excel -> cache file)
    |
    |-- [2] RiskAnalystAgent -------> MCP Server: get_team_overview
    |        reads: {data_status}                 identify_capacity_risks
    |        output_key: risk_analysis            get_employee_details
    |
    |-- [3] RebalancingAgent -------> MCP Server: suggest_work_rebalancing
    |        reads: {risk_analysis}
    |        output_key: rebalancing_plan
    |
    |-- [4] ReportAgent
             reads: {risk_analysis}, {rebalancing_plan}
             output_key: final_report
             (no MCP tools — synthesises from session state)
    |
    v
Session state -> final_report -> printed to console
```

### Why SequentialAgent (not LLM orchestration)?

The pipeline steps always run in the same order and each depends on the previous output. A deterministic `SequentialAgent` is more reliable, cheaper, and easier to debug than asking an LLM to decide what to do next.

### Agent communication via session state

Each agent writes its output to a named `output_key`. The next agent reads it via `{state_key}` placeholder injection in its instruction. No custom message-passing code is required.

| Agent | Writes to | Next agent reads via |
|---|---|---|
| DataLoaderAgent | `data_status` | `{data_status}` |
| RiskAnalystAgent | `risk_analysis` | `{risk_analysis}` |
| RebalancingAgent | `rebalancing_plan` | `{rebalancing_plan}` |
| ReportAgent | `final_report` | read directly from session after run |

### MCP Server — tool definitions

All business logic lives in `mcp_server.py`. The agents consume it via `McpToolset` with `StdioConnectionParams` (local subprocess). Each agent only sees the tools it needs (`tool_filter`).

| MCP Tool | Agent that calls it | Returns |
|---|---|---|
| `load_sprint_data(excel_path)` | DataLoaderAgent | Load confirmation + cache write |
| `get_team_overview()` | RiskAnalystAgent | Team size, total SP, utilisation %, at-risk list |
| `identify_capacity_risks()` | RiskAnalystAgent | Employees over or within 2 SP of limit |
| `get_employee_details(name)` | RiskAnalystAgent | Full capacity breakdown per employee |
| `suggest_work_rebalancing(from, points)` | RebalancingAgent | Best recipient + headroom after transfer |

### Why a separate MCP Server?

- **Decoupled**: tool logic lives in one place regardless of which agent framework calls it
- **Reusable**: the same server works with LangGraph, LlamaIndex, Claude Desktop, or any MCP client
- **File-based state**: `mcp_server.py` writes `sprint_cache.json` after loading Excel, so each agent's fresh MCP subprocess connection can read the data without re-parsing the workbook
- **Principle of least privilege**: `tool_filter` gives each agent only the tools it needs

### Capacity formula

```
available_days = sprint_days - planned_leave - unplanned_leave - holidays
max_sp         = available_days x 1.5   (configurable in Excel Config sheet)
remaining_sp   = max_sp - sp_assigned

Status:
  sp_assigned > max_sp  ->  Over capacity   (hard violation — must be addressed)
  sp_assigned == max_sp ->  At capacity
  sp_assigned < max_sp  ->  Under capacity

Near-capacity threshold: < 2 SP remaining (soft risk — flagged as watch item)
85% utilisation threshold: flagged in team overview as early warning
```

---

## Sample Output

```
====================================================================
  SPRINT CAPACITY MULTI-AGENT SYSTEM
  Google ADK + Model Context Protocol
====================================================================
  Excel  : sprint_data/sprint_data.xlsx
  Model  : gemini-2.0-flash
  Agents : DataLoaderAgent -> RiskAnalystAgent -> RebalancingAgent -> ReportAgent
  MCP    : sprint_data/mcp_server.py
====================================================================

[DataLoaderAgent]
Loaded 5 employees. Sprint: 10 working days, velocity 1.5 SP/day.
------------------------------------------------------------

[RiskAnalystAgent]
Team utilisation: 74%. Ebony has only 1.0 SP of headroom (near_capacity).
All other employees are comfortably under capacity.
------------------------------------------------------------

[RebalancingAgent]
No rebalancing required — no employees are over capacity.
------------------------------------------------------------

[ReportAgent]
1. SPRINT RISK RATING: Low — team is at 74% utilisation with one near-capacity member.
2. CAPACITY RISKS: Ebony — near_capacity, 1.0 SP headroom. Watch for scope additions.
3. REBALANCING ACTION: None required.
4. SLACK CAPACITY: Donna has 11.0 SP remaining — best candidate for late-arriving stories.
------------------------------------------------------------

======================================================================
  FINAL SPRINT RISK REPORT
======================================================================
1. SPRINT RISK RATING: Low ...
======================================================================
```

---

## Project Structure

```
sprint_data/
├── README.md                    <- you are here
├── requirements_sprint.txt      <- Python dependencies (google-adk, mcp, pandas, ...)
├── create_sprint_excel.py       <- Step 1: generate the input Excel file
├── mcp_server.py                <- MCP server: exposes 5 capacity tools via stdio
├── sprint_agents_adk.py         <- Step 2: Google ADK multi-agent system
├── generate_writeup_pdf.py      <- Optional: generate project writeup PDF
├── sprint_data.xlsx             <- Generated by create_sprint_excel.py
└── sprint_cache.json            <- Generated at runtime by mcp_server.py (cache)
```

---

## Setup & Execution

### Prerequisites

- Python 3.10 or later (required by google-adk)
- A Google AI Studio API key — get one free at [aistudio.google.com](https://aistudio.google.com/app/apikey)

### Step 1 — Create and activate a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### Step 2 — Install dependencies

```bash
pip install -r sprint_data/requirements_sprint.txt
```

### Step 3 — Set your Google API key

```bash
# Windows PowerShell
$env:GOOGLE_API_KEY = "your-key-here"

# macOS / Linux
export GOOGLE_API_KEY="your-key-here"
```

For Vertex AI (production):
```bash
$env:GOOGLE_CLOUD_PROJECT      = "your-project-id"
$env:GOOGLE_GENAI_USE_VERTEXAI = "True"
```

> **Security**: Never hard-code API keys in source files. Use environment variables only.

### Step 4 — Generate the Excel file

```bash
python sprint_data/create_sprint_excel.py
```

This creates `sprint_data/sprint_data.xlsx` with two sheets:

| Sheet | Contents |
|---|---|
| **Sprint Capacity** | One row per employee — SP assigned, planned leave, unplanned leave, holidays |
| **Config** | Sprint constants: working days, velocity (SP/day) |

### Step 5 — Run the multi-agent system

```bash
python sprint_data/sprint_agents_adk.py
```

The four agents run in sequence. Each agent's output appears as it completes. The final sprint risk report is printed at the end.

### (Optional) Test the MCP server directly

The MCP server can be invoked standalone using any MCP client:
```bash
python sprint_data/mcp_server.py
```
Or inspect the cache file written after a run:
```bash
cat sprint_data/sprint_cache.json
```

### (Optional) Generate the project writeup PDF

```bash
pip install reportlab
python sprint_data/generate_writeup_pdf.py
```

Opens `sprint_data/sprint_capacity_writeup.pdf`. Fill in **Author Name** and **LinkedIn URL** directly in any PDF viewer (Adobe Acrobat Reader, Chrome, Foxit).

---

## Customising the Data

Open `sprint_data/sprint_data.xlsx` and edit any values, then re-run the agent:

| Column | What to change |
|---|---|
| `SP_Assigned` | Story points committed for each person this sprint |
| `Planned_Leaves` | Pre-approved leave days in the sprint window |
| `Unplanned_Leaves` | Update mid-sprint when a sick day occurs |
| `Holidays` | Public/company holidays in the sprint window |
| Config -> `Points per Day` | Team velocity assumption (default: 1.5 SP/day) |
| Config -> `Sprint Working Days` | Change from 10 for non-standard sprint lengths |

---

## Why Google ADK and MCP?

| Feature | Benefit in this project |
|---|---|
| **SequentialAgent** | Deterministic pipeline — correct execution order without LLM overhead |
| **output_key + {state_key}** | Clean inter-agent communication via shared session state, no glue code |
| **McpToolset + tool_filter** | Least-privilege tool access — each agent sees only what it needs |
| **MCP server (stdio)** | Tool logic decoupled from agent framework; reusable by any MCP client |
| **File-based MCP cache** | Stateless MCP connections still share data across the pipeline |
| **Agent specialisation** | Narrow scope per agent = fewer hallucinated tool calls, clearer debugging |

---

## Extending the System

| Extension | What to build |
|---|---|
| Pull from Jira | Add a `load_jira_sprint` tool to mcp_server.py; DataLoaderAgent calls it |
| Post to Slack | Add a fifth ADK agent — SlackReporterAgent — that posts `{final_report}` |
| Track history | Add a `write_sprint_history` MCP tool that appends each run to a master sheet |
| Web UI | Wrap `run_sprint_analysis()` in FastAPI; expose as an API endpoint |
| Persistent state | Swap `InMemorySessionService` for `VertexAiSessionService` in production |
| Remote MCP server | Switch `StdioConnectionParams` to `SseConnectionParams` with a hosted URL |

---

## Tech Stack

| Tool | Role |
|---|---|
| [Google ADK](https://adk.dev) | Agent, SequentialAgent, Runner, McpToolset, InMemorySessionService |
| [MCP Python SDK](https://pypi.org/project/mcp/) | MCP Server, stdio transport, Tool definitions |
| [Google Gemini](https://aistudio.google.com) | LLM powering all four agents (gemini-2.0-flash) |
| [pandas](https://pandas.pydata.org/) | Excel reading and capacity metric computation |
| [openpyxl](https://openpyxl.readthedocs.io/) | Excel engine for pandas |
| [tabulate](https://pypi.org/project/tabulate/) | ASCII table rendering |
| [reportlab](https://www.reportlab.com/) | PDF writeup with AcroForm editable fields |

---

## License

MIT — free to use, modify, and share.
