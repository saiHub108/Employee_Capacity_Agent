"""
create_sprint_excel.py
----------------------
Generates the seed Excel workbook consumed by sprint_capacity_agent.py.

Design decisions:
  - Two-sheet layout: 'Sprint Capacity' holds per-person data; 'Config' holds
    sprint-level constants.  Keeping them separate means the agent can change
    velocity or sprint length without touching employee rows.
  - Derived columns (Available_Days, Max_SP_Capacity, etc.) are pre-computed
    here and stored in the workbook so a human reviewer can audit the numbers
    directly in Excel without running the agent.
  - Holidays are stored per-row (not as a single global value) to allow future
    per-person overrides, e.g., contractors who observe different holidays.
  - clip(lower=0) guards against the edge case where someone has more days off
    than there are working days in the sprint.

Run this script once per sprint to refresh the workbook, then edit the values
directly in Excel (SP_Assigned, Unplanned_Leaves) as the sprint progresses.
"""

import pandas as pd
import os

# ── Sprint-level constants ─────────────────────────────────────────────────────
# Change these each sprint as needed; they are also written to the Config sheet
# so the agent picks them up at runtime without needing to edit any Python code.
SPRINT_WORKING_DAYS = 10    # Mon–Fri across two calendar weeks (excludes weekends)
SPRINT_HOLIDAYS     = 1     # Public/company holidays falling inside the sprint window
HOURS_PER_DAY       = 8     # Standard working hours; used for reporting only
POINTS_PER_DAY      = 1.5   # Team velocity assumption: story points completable per day.
                             # Adjust this based on the team's historical average.
                             # A lower value (e.g., 1.0) is more conservative for new teams.

# ── Seed employee data ─────────────────────────────────────────────────────────
# SP_Assigned      — story points already committed in sprint planning
# Planned_Leaves   — pre-approved leave known at planning time
# Unplanned_Leaves — sick/emergency leave; defaults to 0 at sprint start and is
#                    updated mid-sprint by the scrum master as events occur
# Holidays         — public holidays; replicated per-row for per-person flexibility
data = {
    "Employee":         ["Adam", "Brian", "Cary", "Donna", "Ebony"],
    "SP_Assigned":      [5, 3, 2, 1, 8],
    "Planned_Leaves":   [2, 2, 1, 1, 3],
    "Unplanned_Leaves": [0, 0, 0, 0, 0],
    "Holidays":         [SPRINT_HOLIDAYS] * 5,
}

df = pd.DataFrame(data)

# ── Derived columns ────────────────────────────────────────────────────────────
# These are computed here rather than only inside the agent so that the
# workbook is self-explanatory to anyone opening it in Excel.
# The agent re-derives them at runtime; these serve as a human-readable audit trail.
df["Total_Days_Off"]  = df["Planned_Leaves"] + df["Unplanned_Leaves"] + df["Holidays"]
df["Available_Days"]  = (SPRINT_WORKING_DAYS - df["Total_Days_Off"]).clip(lower=0)
df["Capacity_Pct"]    = (df["Available_Days"] / SPRINT_WORKING_DAYS * 100).round(1)
df["Max_SP_Capacity"] = (df["Available_Days"] * POINTS_PER_DAY).round(1)

# ── Write workbook ─────────────────────────────────────────────────────────────
# Use __file__ to resolve the output path relative to this script, so the
# workbook lands in the same directory regardless of where the script is called from.
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprint_data.xlsx")

with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
    # Primary sheet — one row per employee
    df.to_excel(writer, index=False, sheet_name="Sprint Capacity")

    # Config sheet — key/value pairs read by the agent to avoid hard-coding
    # sprint constants inside Python.  Adding a new config key here automatically
    # makes it available to any node that reads state["config"].
    meta = pd.DataFrame({
        "Key":   ["Sprint Working Days", "Holidays", "Hours per Day", "Points per Day"],
        "Value": [SPRINT_WORKING_DAYS, SPRINT_HOLIDAYS, HOURS_PER_DAY, POINTS_PER_DAY],
    })
    meta.to_excel(writer, index=False, sheet_name="Config")

print(f"Workbook written: {output_path}")
print(df.to_string(index=False))
