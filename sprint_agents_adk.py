"""
sprint_agents_adk.py — Sprint Capacity Multi-Agent System (Google ADK)
=======================================================================
A production-grade multi-agent system for sprint capacity planning built
with Google's Agent Development Kit (ADK) and the Model Context Protocol.

Architecture — SequentialAgent orchestration:

    SprintCapacityOrchestrator (SequentialAgent)
    ├── DataLoaderAgent     tools: [load_sprint_data]           via MCP
    ├── RiskAnalystAgent    tools: [get_team_overview,          via MCP
    │                               identify_capacity_risks,
    │                               get_employee_details]
    ├── RebalancingAgent    tools: [suggest_work_rebalancing]   via MCP
    └── ReportAgent         tools: (none — synthesises from state)

Design decisions:
  - SequentialAgent (not LLM orchestration): the pipeline steps are always
    the same and each step depends on the previous one's output. A
    deterministic workflow agent is more reliable and cheaper than asking an
    LLM to decide what to do next.
  - Separate agents per role: each agent sees only the tools it needs and
    has instructions scoped to one task. Narrower scope = fewer hallucinated
    tool calls and clearer per-agent output for debugging.
  - output_key + {state_key} injection: agents communicate via shared session
    state. DataLoaderAgent writes to "data_status"; RiskAnalystAgent reads
    {data_status} in its instruction and writes to "risk_analysis"; and so on.
    No custom message-passing code required.
  - MCP tools (not inline Python functions): all tool logic lives in
    mcp_server.py. This decouples the tool implementation from the agent
    framework — the same MCP server can be consumed by any framework that
    speaks MCP (LangGraph, LlamaIndex, Claude Desktop, custom integrations).
  - File-based MCP cache: mcp_server.py writes sprint_cache.json when
    load_sprint_data runs. Each agent opens a fresh MCP connection (new
    subprocess), but reads from the same cache file, so data computed by
    DataLoaderAgent is available to every subsequent agent.
  - Dual-provider model selection: prefers Gemini (GOOGLE_API_KEY) but
    falls back to Anthropic via LiteLLM (ANTHROPIC_API_KEY) automatically.

Credentials — provide ONE of the following (never hard-code in source):
  GOOGLE_API_KEY          — Google AI Studio  (recommended for local dev)
  GOOGLE_CLOUD_PROJECT    — Vertex AI         (set GOOGLE_GENAI_USE_VERTEXAI=True)
  ANTHROPIC_API_KEY       — Anthropic via LiteLLM fallback

Run:
    python sprint_data/sprint_agents_adk.py
"""

import asyncio
import os
import sys
import warnings
from pathlib import Path

# SequentialAgent is deprecated in ADK 2.x but remains functional —
# no replacement (Workflow) exists in the currently installed release.
# Suppress the deprecation warning unconditionally to keep output clean.
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="google.adk")

from google.adk.agents import Agent, SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.genai import types as genai_types
from mcp import StdioServerParameters


# ── Environment guard and model selection ─────────────────────────────────────
# Prefer Gemini (Google AI Studio or Vertex AI). Fall back to Anthropic via
# LiteLLM if only ANTHROPIC_API_KEY is available.
# Fail fast before any agent is constructed if no credentials exist at all.
def _select_model() -> object:
    """
    Return the model identifier for all agents.

    Priority order:
      1. Gemini via Google AI Studio  (GOOGLE_API_KEY)
      2. Gemini via Vertex AI         (GOOGLE_CLOUD_PROJECT)
      3. Anthropic via LiteLLM        (ANTHROPIC_API_KEY)
      4. OpenAI-compatible proxy      (OPENAI_API_KEY + OPENAI_BASE_URL)

    Returns a string (Gemini model ID) or a LiteLlm object for non-Gemini providers.
    Never reads credentials from source code — only from environment variables.
    """
    # ADK accepts GOOGLE_API_KEY; GEMINI_API_KEY is also a valid key (same value)
    if (os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")):
        # Ensure GOOGLE_API_KEY is set since that's what ADK reads by default
        if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
        return "gemini-2.0-flash"

    from google.adk.models.lite_llm import LiteLlm

    if os.environ.get("ANTHROPIC_API_KEY"):
        print("[INFO] Using Anthropic via LiteLLM (ANTHROPIC_API_KEY found).")
        return LiteLlm(model="anthropic/claude-haiku-4-5-20251001")

    # OpenAI-compatible proxy (e.g. a local gateway or routing service)
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
        base_url = os.environ["OPENAI_BASE_URL"]
        # Use the openai provider so LiteLLM routes to the configured base URL.
        # Prefix with "openai/" so LiteLLM applies OpenAI-compatible chat completion.
        print(f"[INFO] Using OpenAI-compatible proxy at {base_url} via LiteLLM.")
        return LiteLlm(
            model="openai/claude-haiku-4-5-20251001",
            api_base=base_url,
            api_key=os.environ["OPENAI_API_KEY"],
        )

    sys.exit(
        "\n[ERROR] No AI credentials found. Provide one of:\n"
        "\n  Google AI Studio (recommended, free tier available):\n"
        "    Windows: $env:GOOGLE_API_KEY = 'your-key'\n"
        "    Linux:   export GOOGLE_API_KEY='your-key'\n"
        "    Get a key at: https://aistudio.google.com/app/apikey\n"
        "\n  Vertex AI:\n"
        "    $env:GOOGLE_CLOUD_PROJECT      = 'your-project-id'\n"
        "    $env:GOOGLE_GENAI_USE_VERTEXAI = 'True'\n"
        "\n  Anthropic (via LiteLLM):\n"
        "    $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n"
        "\nNever hard-code API keys in source files.\n"
    )

MODEL = _select_model()


# ── Paths ─────────────────────────────────────────────────────────────────────
# All paths resolved relative to this script for portability across CWDs.
THIS_DIR   = Path(__file__).parent.resolve()
MCP_SERVER = str(THIS_DIR / "mcp_server.py")    # MCP server subprocess
EXCEL_PATH = str(THIS_DIR / "sprint_data.xlsx") # Sprint data workbook

# ADK application namespace — scopes sessions so runs don't collide
APP_NAME = "sprint_capacity"


# ── MCP Toolset factory ────────────────────────────────────────────────────────
def _mcp(tool_filter: list[str]) -> McpToolset:
    """
    Build an McpToolset that connects to the local MCP server via stdio.

    Each call creates an independent connection — a fresh subprocess per agent.
    tool_filter restricts which MCP tools the agent can see, applying the
    principle of least privilege: DataLoaderAgent cannot accidentally call
    suggest_work_rebalancing, and RebalancingAgent cannot call load_sprint_data.

    The MCP server is stateless across connections because it reads/writes a
    JSON cache file. Data loaded by DataLoaderAgent's connection persists to
    RiskAnalystAgent's connection without any shared in-process state.

    sys.executable is used instead of "python" to guarantee the same Python
    interpreter that is running this script also runs the MCP server — critical
    on systems with multiple Python installations or virtual environments.

    timeout=30.0: the default 5s timeout can be too short on Windows where
    Python subprocess startup is slower. 30s gives ample headroom.
    """
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,  # same interpreter, handles venvs correctly
                args=[MCP_SERVER],       # absolute path resolved above
            ),
            timeout=30.0,  # subprocess startup can be slow on Windows; 5s default too short
        ),
        tool_filter=tool_filter,
    )


# ── Agent 1 — DataLoaderAgent ─────────────────────────────────────────────────
# Single responsibility: open the Excel workbook via MCP and confirm load.
# output_key writes the confirmation to session state key "data_status".
# Downstream agents reference {data_status} in their instructions.
data_loader_agent = Agent(
    name="DataLoaderAgent",
    model=MODEL,
    description="Loads sprint workforce data from Excel via the Sprint Capacity MCP server.",
    instruction=(
        f"You are a data loading agent for sprint capacity planning.\n\n"
        f"Call the load_sprint_data tool with this exact path:\n"
        f"  excel_path='{EXCEL_PATH}'\n\n"
        "After the tool returns, report:\n"
        "  - How many employees were loaded\n"
        "  - The sprint working days and velocity (SP/day) from the config\n"
        "Do not analyse the data — only load and confirm. Keep your response short."
    ),
    tools=[_mcp(tool_filter=["load_sprint_data"])],
    # Writes confirmation summary to session state for downstream agents
    output_key="data_status",
)


# ── Agent 2 — RiskAnalystAgent ────────────────────────────────────────────────
# Single responsibility: investigate capacity risks using three MCP tools.
# Reads {data_status} from state to confirm data is ready before proceeding.
# output_key writes findings to "risk_analysis" for the RebalancingAgent.
risk_analyst_agent = Agent(
    name="RiskAnalystAgent",
    model=MODEL,
    description="Identifies sprint capacity risks for all employees using MCP tools.",
    instruction=(
        "You are a capacity risk analyst. Data load status: {data_status}\n\n"
        "Perform a systematic three-step capacity investigation:\n"
        "  1. Call get_team_overview — understand overall utilisation and who is near the edge.\n"
        "  2. Call identify_capacity_risks — find employees over capacity or with < 2 SP headroom.\n"
        "  3. For each employee flagged as at-risk, call get_employee_details to get exact numbers.\n\n"
        "Report your findings concisely:\n"
        "  - Overall team utilisation %\n"
        "  - Each at-risk employee, their issue (over_capacity / near_capacity), and their headroom in SP\n"
        "  - If no risks: state 'sprint is healthy' and move on"
    ),
    tools=[_mcp(tool_filter=[
        "get_team_overview",
        "identify_capacity_risks",
        "get_employee_details",
    ])],
    # Writes risk findings to session state for the RebalancingAgent
    output_key="risk_analysis",
)


# ── Agent 3 — RebalancingAgent ────────────────────────────────────────────────
# Single responsibility: for each over-capacity employee, find who can absorb work.
# Only calls suggest_work_rebalancing if the risk analysis found over-capacity cases.
# Near-capacity employees are noted but rebalancing is only proposed for hard violations.
rebalancing_agent = Agent(
    name="RebalancingAgent",
    model=MODEL,
    description="Suggests story-point redistribution for any over-capacity sprint employees.",
    instruction=(
        "You are a work rebalancing specialist. Risk analysis:\n{risk_analysis}\n\n"
        "For each employee identified as 'over_capacity':\n"
        "  - Call suggest_work_rebalancing with from_employee=their name and\n"
        "    points_to_move=the number of SP they are over their max.\n"
        "  - Report the recommended recipient and their capacity after the transfer.\n\n"
        "If there are no over_capacity employees, reply: 'No rebalancing required.'\n"
        "Do not call suggest_work_rebalancing for near_capacity employees — "
        "near-capacity is a watch item, not an action item."
    ),
    tools=[_mcp(tool_filter=["suggest_work_rebalancing"])],
    # Writes rebalancing plan to session state for the ReportAgent
    output_key="rebalancing_plan",
)


# ── Agent 4 — ReportAgent ─────────────────────────────────────────────────────
# Single responsibility: synthesise all prior findings into a final sprint report.
# No MCP tools needed — this agent reasons entirely from state injected via
# {risk_analysis} and {rebalancing_plan} placeholders. This keeps the synthesis
# step decoupled from any external data source.
report_agent = Agent(
    name="ReportAgent",
    model=MODEL,
    description="Synthesises risk analysis and rebalancing findings into a sprint risk report.",
    instruction=(
        "You are a senior scrum master writing the final sprint capacity report "
        "that will be presented at sprint planning.\n\n"
        "Risk analysis:\n{risk_analysis}\n\n"
        "Rebalancing plan:\n{rebalancing_plan}\n\n"
        "Write a concise, actionable report with exactly these four sections:\n"
        "1. SPRINT RISK RATING: Low / Medium / High  (one sentence reason)\n"
        "2. CAPACITY RISKS: For each at-risk employee — name, issue, exact SP headroom\n"
        "3. REBALANCING ACTION: Specific action with employee names and SP numbers "
        "(or 'None required')\n"
        "4. SLACK CAPACITY: Who has the most headroom for late-arriving stories\n\n"
        "Keep it under 150 words — this is read aloud at stand-up."
    ),
    # Writes the final report to session state (readable after run completes)
    output_key="final_report",
)


# ── Orchestrator — SequentialAgent ────────────────────────────────────────────
# SequentialAgent executes sub-agents in order and propagates session state
# between them. No LLM is involved in orchestration — execution order is
# deterministic by design. This is the right choice when:
#   a) Pipeline steps always run in the same sequence
#   b) Each step clearly depends on the previous step's output
#   c) You want to avoid the cost and latency of an LLM deciding what's next
orchestrator = SequentialAgent(
    name="SprintCapacityOrchestrator",
    description=(
        "Enterprise sprint capacity planning: loads workforce data, identifies "
        "delivery risks, suggests work rebalancing, and produces a scrum-ready "
        "risk report."
    ),
    sub_agents=[
        data_loader_agent,
        risk_analyst_agent,
        rebalancing_agent,
        report_agent,
    ],
)


# ── Execution ─────────────────────────────────────────────────────────────────
async def run_sprint_analysis() -> None:
    """
    Execute the multi-agent sprint capacity pipeline.

    ADK execution model:
      - InMemorySessionService: holds session state in memory. Suitable for
        single-run scripts. Swap for VertexAiSessionService in production to
        persist state across invocations.
      - Runner: the ADK execution engine. Manages agent invocations and the
        event stream. Connects to agents, dispatches messages, collects results.
      - Session state pre-seeded with excel_path so any agent that needs the
        path can read it via {excel_path} without it being hard-coded in
        instructions.
      - Events stream from runner.run_async — is_final_response() marks the
        last content event per agent turn.
    """
    session_service = InMemorySessionService()

    # Pre-seed session state with the Excel path.
    # InMemorySessionService.create_session is async in ADK 2.x.
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id="scrum_master",
        state={"excel_path": EXCEL_PATH},
    )

    runner = Runner(
        agent=orchestrator,
        app_name=APP_NAME,
        session_service=session_service,
    )

    _print_banner()

    # Stream events from the multi-agent pipeline
    # Each agent's final output appears as a separate is_final_response event
    async for event in runner.run_async(
        user_id="scrum_master",
        session_id=session.id,
        new_message=genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=(
                "Run a full sprint capacity analysis. Load the sprint data, "
                "identify all capacity risks, suggest work rebalancing where "
                "needed, and produce a final risk report."
            ))],
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            author = getattr(event, "author", "Agent")
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    print(f"\n[{author}]")
                    print(part.text)
                    print("-" * 60)

    # Read final report directly from session state.
    # get_session is also async in ADK 2.x.
    final_session = await session_service.get_session(
        app_name=APP_NAME,
        user_id="scrum_master",
        session_id=session.id,
    )
    final_report = final_session.state.get("final_report", "")
    if final_report:
        print("\n" + "=" * 70)
        print("  FINAL SPRINT RISK REPORT")
        print("=" * 70)
        print(final_report)
        print("=" * 70)


def _print_banner() -> None:
    """Print startup info so the user knows what is running."""
    agent_names = " -> ".join(a.name for a in orchestrator.sub_agents)
    # Print a clean model label regardless of whether MODEL is a string or LiteLlm object
    model_label = MODEL if isinstance(MODEL, str) else getattr(MODEL, "model", str(MODEL))
    print()
    print("=" * 70)
    print("  SPRINT CAPACITY MULTI-AGENT SYSTEM")
    print("  Google ADK + Model Context Protocol")
    print("=" * 70)
    print(f"  Excel  : {EXCEL_PATH}")
    print(f"  Model  : {model_label}")
    print(f"  Agents : {agent_names}")
    print(f"  MCP    : {MCP_SERVER}")
    print("=" * 70)


# ── Main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Validate the Excel workbook exists before starting any agent
    if not Path(EXCEL_PATH).exists():
        print(f"[ERROR] Sprint data not found: {EXCEL_PATH}")
        print("Run create_sprint_excel.py first to generate it.")
        sys.exit(1)

    try:
        asyncio.run(run_sprint_analysis())
    except Exception as exc:
        msg = str(exc)
        # Provide a clear user-facing message for common failure modes
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            print("\n[ERROR] Google AI quota exceeded.")
            print("Your GOOGLE_API_KEY has hit its rate or daily limit.")
            print("Options:")
            print("  1. Wait a minute and retry (per-minute quota resets).")
            print("  2. Get a free key at: https://aistudio.google.com/app/apikey")
            print("  3. Enable billing in your Google Cloud project for higher quotas.")
        elif "AuthenticationError" in msg or "401" in msg:
            print("\n[ERROR] Authentication failed.")
            print("Check that your API key is valid and correctly set.")
        else:
            raise  # re-raise unexpected errors with full traceback
