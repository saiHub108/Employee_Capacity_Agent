"""
generate_video_slides.py
-------------------------
Produces five 1920x1080 PNG title-card / slide images for the YouTube demo
video described in sprint_data/video_script.md.

Design:
  - Visual language matches the PDF cover (generate_writeup_pdf.py): navy
    background (#1B2A4A), teal accent (#0D7E7E), dot-grid texture, rounded
    agent-pipeline boxes. Keeping both artifacts visually consistent means
    the video and the writeup PDF feel like one coherent submission.
  - Plain Pillow (PIL) drawing, not matplotlib/reportlab, because we only
    need flat shapes + text on a fixed canvas — no plotting or PDF features
    are required, and Pillow has no extra runtime dependencies here.
  - Each slide is a standalone function so individual slides can be
    regenerated or reordered without re-running the whole script.

Output (sprint_data/video_slides/):
  01_title.png        - opening title card
  02_problem.png       - "the problem" cold-open card
  03_architecture.png  - 4-agent pipeline + MCP server diagram
  04_setup.png         - terminal-style setup commands
  05_outro.png         - closing card

Run:
    python sprint_data/generate_video_slides.py
"""

import os
from PIL import Image, ImageDraw, ImageFont

# ── Output directory ─────────────────────────────────────────────────────────
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "video_slides")
os.makedirs(OUT_DIR, exist_ok=True)

# ── Canvas size — standard 1080p so slides drop directly into any editor ─────
W, H = 1920, 1080

# ── Palette — matches generate_writeup_pdf.py exactly for visual consistency ──
NAVY       = (27, 42, 74)
NAVY_DEEP  = (13, 32, 53)
TEAL       = (13, 126, 126)
DARK_TEAL  = (13, 82, 82)
BOX_DARK   = (10, 64, 64)
LIGHT_TEAL = (176, 212, 212)
MUTED_TEAL = (106, 152, 152)
DIM_TEAL   = (74, 122, 122)
WHITE      = (255, 255, 255)
ORANGE     = (230, 126, 34)
DOT_COLOR  = (31, 50, 96)

# ── Fonts — Segoe UI ships on all Windows installs; fall back to default ─────
FONT_DIR = r"C:\Windows\Fonts"


def _font(name, size):
    path = os.path.join(FONT_DIR, name)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


F_TITLE   = _font("segoeuib.ttf", 88)
F_SUB     = _font("segoeuib.ttf", 52)
F_BODY    = _font("segoeui.ttf", 34)
F_BODY_SM = _font("segoeui.ttf", 26)
F_TAG     = _font("segoeui.ttf", 24)
F_LABEL   = _font("segoeuib.ttf", 22)
F_MONO    = _font("consola.ttf", 30)
F_MONO_B  = _font("consolab.ttf", 30)
F_MONO_LG = _font("consolab.ttf", 38)


def _dot_grid(draw, spacing=32, radius=1.6):
    """Draw the same dot-grid texture used on the PDF cover, scaled for 1080p."""
    for gx in range(0, W + 1, spacing):
        for gy in range(0, H + 1, spacing):
            draw.ellipse([gx - radius, gy - radius, gx + radius, gy + radius], fill=DOT_COLOR)


def _centered_text(draw, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, y), text, font=font, fill=fill)


def _base_canvas():
    img = Image.new("RGB", (W, H), NAVY)
    draw = ImageDraw.Draw(img)
    _dot_grid(draw)
    # Top + bottom teal strips for brand consistency across every slide
    draw.rectangle([0, 0, W, 14], fill=TEAL)
    draw.rectangle([0, H - 14, W, H], fill=TEAL)
    return img, draw


def _rounded_box(draw, xy, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


# ── Slide 1 — Title card ──────────────────────────────────────────────────────
def slide_title():
    img, draw = _base_canvas()

    # Kaggle track badge, top-right
    bx, by, bw, bh = W - 480, 80, 380, 110
    _rounded_box(draw, [bx, by, bx + bw, by + bh], 10, fill=(10, 74, 74))
    _centered_badge_text(draw, bx, bw, by + 28, "KAGGLE  ·  2025", F_LABEL, TEAL)
    _centered_badge_text(draw, bx, bw, by + 62, "ENTERPRISE AI AGENTS TRACK", F_TAG, MUTED_TEAL)

    _centered_text(draw, 360, "Sprint Capacity Planning", F_TITLE, WHITE)
    _centered_text(draw, 470, "Multi-Agent AI System", F_SUB, TEAL)

    draw.line([(160, 575), (W - 160, 575)], fill=TEAL, width=3)

    _centered_text(draw, 610, "Autonomous risk detection and rebalancing for agile sprint teams",
                    F_BODY, LIGHT_TEAL)
    _centered_text(draw, 670, "Google ADK  ·  Model Context Protocol  ·  Gemini  ·  Python  ·  pandas",
                    F_BODY_SM, MUTED_TEAL)

    # Stat chips, matching the PDF cover's stats row
    stats = [("4", "AGENTS"), ("5", "MCP TOOLS"), ("1", "RISK REPORT"), ("~2h", "SAVED / SPRINT")]
    chip_w, chip_h, gap = 320, 130, 40
    total_w = len(stats) * chip_w + (len(stats) - 1) * gap
    x0 = (W - total_w) / 2
    y0 = 770
    for i, (num, lbl) in enumerate(stats):
        cx = x0 + i * (chip_w + gap)
        _rounded_box(draw, [cx, y0, cx + chip_w, y0 + chip_h], 8,
                     fill=(10, 56, 72), outline=DARK_TEAL, width=2)
        _centered_badge_text(draw, cx, chip_w, y0 + 28, num, F_MONO_LG, TEAL)
        _centered_badge_text(draw, cx, chip_w, y0 + 86, lbl, F_TAG, MUTED_TEAL)

    img.save(os.path.join(OUT_DIR, "01_title.png"))


def _centered_badge_text(draw, x, w, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((x + (w - tw) / 2, y), text, font=font, fill=fill)


# ── Slide 2 — The problem (cold open) ─────────────────────────────────────────
def slide_problem():
    img, draw = _base_canvas()

    _centered_text(draw, 130, "Every Sprint, the Same Question", F_TITLE, WHITE)
    _centered_text(draw, 250, '"How much can each person actually deliver this sprint?"',
                    F_SUB, TEAL)

    pain_points = [
        ("Manual & repetitive", "Leave days, holidays, and velocity calculated by hand, every two weeks"),
        ("Error-prone", "One miscount silently overloads someone before the sprint even starts"),
        ("Costly at scale", "2-4 hours of senior engineering time per team, per planning cycle"),
    ]
    y = 420
    for title, desc in pain_points:
        _rounded_box(draw, [220, y, W - 220, y + 150], 10, fill=(10, 56, 72), outline=DARK_TEAL, width=2)
        draw.rectangle([220, y, 232, y + 150], fill=ORANGE)
        draw.text((270, y + 28), title, font=F_SUB, fill=WHITE)
        draw.text((270, y + 90), desc, font=F_BODY_SM, fill=MUTED_TEAL)
        y += 190

    img.save(os.path.join(OUT_DIR, "02_problem.png"))


# ── Slide 3 — Architecture: 4-agent pipeline + MCP server ───────────────────
def slide_architecture():
    img, draw = _base_canvas()

    _centered_text(draw, 90, "Four Agents, One MCP Server", F_TITLE, WHITE)
    _centered_text(draw, 200, "Google ADK SequentialAgent  +  Model Context Protocol", F_BODY, MUTED_TEAL)

    # Agent pipeline boxes
    agents = [("DataLoader", "Agent", "load_sprint_data"),
              ("RiskAnalyst", "Agent", "get_team_overview\nidentify_capacity_risks"),
              ("Rebalancing", "Agent", "suggest_work_\nrebalancing"),
              ("Report", "Agent", "synthesises\nshared state")]
    box_w, box_h, gap = 380, 220, 70
    total_w = len(agents) * box_w + (len(agents) - 1) * gap
    x0 = (W - total_w) / 2
    y0 = 330

    centers = []
    for i, (name, role, tool) in enumerate(agents):
        bx = x0 + i * (box_w + gap)
        centers.append(bx + box_w / 2)
        _rounded_box(draw, [bx, y0, bx + box_w, y0 + box_h], 14, fill=BOX_DARK, outline=TEAL, width=3)
        _centered_badge_text(draw, bx, box_w, y0 + 30, name, F_SUB, WHITE)
        _centered_badge_text(draw, bx, box_w, y0 + 90, role, F_BODY_SM, TEAL)
        # Tool name(s), small, wrapped manually on \n
        ty = y0 + 140
        for line in tool.split("\n"):
            _centered_badge_text(draw, bx, box_w, ty, line, F_TAG, MUTED_TEAL)
            ty += 30

        if i < len(agents) - 1:
            ax1 = bx + box_w + 8
            ax2 = bx + box_w + gap - 8
            ay = y0 + box_h / 2
            draw.line([(ax1, ay), (ax2, ay)], fill=TEAL, width=5)
            draw.polygon([(ax2, ay), (ax2 - 22, ay - 14), (ax2 - 22, ay + 14)], fill=TEAL)

    # MCP server bar with dashed connectors
    mcp_y = y0 + box_h + 110
    mcp_x0, mcp_x1 = x0 + 20, x0 + total_w - 20
    for cx in centers:
        y_top = y0 + box_h
        seg = 14
        yy = y_top
        while yy < mcp_y:
            draw.line([(cx, yy), (cx, min(yy + seg, mcp_y))], fill=DARK_TEAL, width=3)
            yy += seg * 2

    _rounded_box(draw, [mcp_x0, mcp_y, mcp_x1, mcp_y + 110], 10, fill=(10, 56, 72), outline=DARK_TEAL, width=2)
    _centered_text(draw, mcp_y + 38, "Sprint Capacity MCP Server  (stdio transport, 5 tools)", F_SUB, TEAL)

    img.save(os.path.join(OUT_DIR, "03_architecture.png"))


# ── Slide 4 — Setup commands (terminal style) ─────────────────────────────────
def slide_setup():
    img, draw = _base_canvas()

    _centered_text(draw, 90, "Run It Yourself", F_TITLE, WHITE)
    _centered_text(draw, 200, "Four commands, zero hard-coded credentials", F_BODY, MUTED_TEAL)

    term_x0, term_y0, term_x1, term_y1 = 260, 320, W - 260, 920
    _rounded_box(draw, [term_x0, term_y0, term_x1, term_y1], 14, fill=NAVY_DEEP, outline=DARK_TEAL, width=2)

    # Fake terminal title bar with traffic-light dots
    draw.rectangle([term_x0, term_y0, term_x1, term_y0 + 50], fill=(8, 24, 40))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = term_x0 + 30 + i * 34
        draw.ellipse([cx, term_y0 + 17, cx + 16, term_y0 + 33], fill=c)
    _centered_badge_text(draw, term_x0, term_x1 - term_x0, term_y0 + 14,
                          "sprint_data  —  PowerShell", F_BODY_SM, MUTED_TEAL)

    commands = [
        ("$ ", "pip install -r requirements_sprint.txt", ""),
        ("$ ", '$env:GOOGLE_API_KEY = "your-key-here"', ""),
        ("$ ", "python create_sprint_excel.py", "# generates sprint_data.xlsx"),
        ("$ ", "python sprint_agents_adk.py", "# runs the 4-agent pipeline"),
    ]
    cy = term_y0 + 100
    for prompt, cmd, note in commands:
        draw.text((term_x0 + 50, cy), prompt, font=F_MONO_B, fill=TEAL)
        prompt_w = draw.textbbox((0, 0), prompt, font=F_MONO_B)[2]
        draw.text((term_x0 + 50 + prompt_w, cy), cmd, font=F_MONO_B, fill=WHITE)
        if note:
            cmd_w = draw.textbbox((0, 0), cmd, font=F_MONO_B)[2]
            draw.text((term_x0 + 50 + prompt_w + cmd_w + 30, cy), note, font=F_MONO, fill=MUTED_TEAL)
        cy += 110

    img.save(os.path.join(OUT_DIR, "04_setup.png"))


# ── Slide 5 — Outro ───────────────────────────────────────────────────────────
def slide_outro():
    img, draw = _base_canvas()

    _centered_text(draw, 380, "Sprint Capacity Planning Agent", F_TITLE, WHITE)
    _centered_text(draw, 500, "Built with Google ADK  ·  MCP  ·  Python", F_SUB, TEAL)

    draw.line([(560, 600), (W - 560, 600)], fill=DARK_TEAL, width=2)

    _centered_text(draw, 660, "github.com/<your-username>/<your-repo>", F_BODY, LIGHT_TEAL)
    _centered_text(draw, 720, "Thanks for watching", F_BODY_SM, MUTED_TEAL)

    img.save(os.path.join(OUT_DIR, "05_outro.png"))


if __name__ == "__main__":
    slide_title()
    slide_problem()
    slide_architecture()
    slide_setup()
    slide_outro()
    print(f"5 slides written to: {OUT_DIR}")
