#!/usr/bin/env python3
"""
SwarmSaid Video Generation Pipeline
=====================================
One command: document in, finished video out.

Free stack:
  - Script generation : Google Gemini API (free via Google AI Studio)
  - Voiceover         : edge-tts (Microsoft Edge neural TTS — no key, no GPU)
  - Visuals           : matplotlib
  - Assembly          : FFmpeg

Usage:
    # Full pipeline from a new document:
    python generate_video.py --input jinsa_report.pdf --prompt "Strait of Hormuz 90 day closure economic impact"

    # Skip simulation, reuse an existing completed sim:
    python generate_video.py --sim-id sim_80d86549391e --prompt "Strait of Hormuz 90 day closure"

    # Load a pre-saved report JSON directly:
    python generate_video.py --report-file report.json --prompt "..."

    # Script only (no audio/video assembly):
    python generate_video.py --sim-id sim_80d86549391e --prompt "..." --script-only

    # Add background music (Suno download):
    python generate_video.py --input doc.pdf --prompt "..." --music suno_track.mp3

    # Choose narrator voice:
    python generate_video.py --input doc.pdf --prompt "..." --voice en-US-GuyNeural

    # List available voices:
    python generate_video.py --list-voices

Requirements:
    pip install requests google-generativeai edge-tts numpy matplotlib ffmpeg-python Pillow
    ffmpeg must be installed and on PATH
    GEMINI_API_KEY must be set in .env or environment
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import requests

# ─────────────────────────────────────────────────────────────────────────────
# LOAD .env
# ─────────────────────────────────────────────────────────────────────────────

def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

_load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

MIROFISH_BASE  = os.getenv("MIROFISH_BASE", "http://localhost:5001")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL",   "gemini-2.0-flash")   # free tier
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")                   # free at pexels.com/api
HF_TOKEN       = os.getenv("HF_TOKEN", "")                         # optional

OUTPUT_DIR     = Path("swarm_said_output")

# edge-tts voice — cinematic narrator options:
#   en-US-GuyNeural      → male, deep, authoritative  (default)
#   en-US-AriaNeural     → female, warm, documentary feel
#   en-US-DavisNeural    → male, confident
#   en-US-JennyNeural    → female, clear, professional
DEFAULT_VOICE  = os.getenv("SWARMSAID_VOICE", "en-US-GuyNeural")

# Brand colors
COLOR_BG       = "#0a0a0f"
COLOR_ACCENT   = "#00ff88"
COLOR_MAGENTA  = "#ff00cc"
COLOR_CYAN     = "#00ccff"
COLOR_TEXT     = "#e8e8e8"

SWARM_SYSTEM_PROMPT = """\
You are the script writer for SwarmSaid — a faceless TikTok and YouTube channel.
SwarmSaid feeds real intelligence documents into a million-agent AI swarm (MiroFish),
then reveals what the swarm predicts. Tagline: "The swarm already knows."

Tone: Dark, cinematic, confident. Like a documentary narrator who already knows how it ends.
Voice: Third person. The SWARM speaks. Never use "I" or personal opinions.

Output ONLY valid JSON with these exact keys (no markdown fences, no explanation):
{
  "hook": "Opening 1-2 sentences. Shocking stat or question. Grabs attention in under 3 seconds.",
  "build": "2-3 sentences. What document was fed in, what MiroFish did. Mention agent count, rounds, graph stats.",
  "reveal": "The 3 most shocking predictions the swarm generated. Each one specific — numbers, timelines, named entities.",
  "context": "1-2 sentences. Why this prediction matters RIGHT NOW. Connect to current events.",
  "cta": "One sentence. Subscribe CTA. Reference the swarm.",
  "title_youtube": "YouTube title under 70 chars. SEO-friendly. Include the simulation scenario.",
  "title_tiktok": "TikTok title under 100 chars. Hook-first. Lead with numbers.",
  "description": "YouTube description 2-3 paragraphs. Include sim stats. End with #SwarmSaid #AI #Predictions",
  "on_screen_stats": ["4 to 6 short stat strings to flash on screen, e.g. '69 AGENTS', '72 ROUNDS'"]
}"""


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimulationResult:
    sim_id: str
    report: dict
    stats: dict = field(default_factory=dict)

@dataclass
class VideoScript:
    hook: str
    build: str
    reveal: str
    context: str
    cta: str
    title_youtube: str
    title_tiktok: str
    description: str
    on_screen_stats: list

    @classmethod
    def from_dict(cls, d: dict) -> "VideoScript":
        return cls(
            hook=d.get("hook", ""),
            build=d.get("build", ""),
            reveal=d.get("reveal", ""),
            context=d.get("context", ""),
            cta=d.get("cta", ""),
            title_youtube=d.get("title_youtube", "SwarmSaid Prediction"),
            title_tiktok=d.get("title_tiktok", "The swarm already knows."),
            description=d.get("description", ""),
            on_screen_stats=d.get("on_screen_stats", []),
        )

    def full_narration(self) -> str:
        """Full narration text for TTS, with natural pauses between sections."""
        parts = [self.hook, self.build, self.reveal, self.context, self.cta]
        return "  ".join(p.strip() for p in parts if p.strip())


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1 — SIMULATION (MiroFish API)
# ─────────────────────────────────────────────────────────────────────────────

def run_simulation(pdf_path: str, prompt: str, project_id: str = "swarm_said") -> SimulationResult:
    print(f"\n[Stage 1] Running MiroFish simulation...")
    print(f"  Document : {pdf_path}")
    print(f"  Prompt   : {prompt}")

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Input document not found: {pdf_path}")

    # Upload document
    print("  Uploading document...")
    with open(pdf_path, "rb") as f:
        resp = requests.post(
            f"{MIROFISH_BASE}/api/project/upload",
            files={"file": (pdf_path.name, f, "application/pdf")},
            data={"project_id": project_id},
            timeout=60,
        )
    resp.raise_for_status()
    upload_data = resp.json()
    document_text = upload_data.get("text", "")
    print(f"  Extracted {len(document_text):,} chars of text")

    # Create simulation
    print("  Creating simulation...")
    resp = requests.post(f"{MIROFISH_BASE}/api/simulation/create", json={
        "project_id": project_id,
        "graph_id": upload_data.get("graph_id", "default"),
        "enable_twitter": True,
        "enable_reddit": True,
    }, timeout=30)
    resp.raise_for_status()
    sim_id = resp.json()["simulation_id"]
    print(f"  Simulation ID: {sim_id}")

    # Prepare agents
    print("  Preparing agents (this takes a minute)...")
    resp = requests.post(f"{MIROFISH_BASE}/api/simulation/{sim_id}/prepare", json={
        "simulation_requirement": prompt,
        "document_text": document_text,
    }, timeout=300)
    resp.raise_for_status()

    # Start simulation
    print("  Starting simulation run...")
    resp = requests.post(f"{MIROFISH_BASE}/api/simulation/{sim_id}/run", timeout=30)
    resp.raise_for_status()

    # Poll until done
    _poll_simulation(sim_id)

    return _fetch_report(sim_id)


def _poll_simulation(sim_id: str, poll_interval: int = 10, timeout: int = 3600):
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{MIROFISH_BASE}/api/simulation/{sim_id}/status", timeout=10)
        resp.raise_for_status()
        data   = resp.json()
        status = data.get("status", "unknown")
        round_n = data.get("current_round", 0)
        print(f"  Status: {status} | Round: {round_n}     ", end="\r")
        if status == "completed":
            print(f"\n  Simulation complete — {round_n} rounds.")
            return
        if status == "failed":
            raise RuntimeError(f"Simulation failed: {data.get('error')}")
        time.sleep(poll_interval)
    raise TimeoutError(f"Simulation timed out after {timeout}s")


def _fetch_report(sim_id: str) -> SimulationResult:
    print(f"\n[Stage 2] Generating report for {sim_id}...")
    resp = requests.post(
        f"{MIROFISH_BASE}/api/report/generate",
        json={"simulation_id": sim_id},
        timeout=600,
    )
    resp.raise_for_status()
    report_data = resp.json()

    stats_resp = requests.get(f"{MIROFISH_BASE}/api/simulation/{sim_id}/status", timeout=10)
    stats = stats_resp.json() if stats_resp.ok else {}

    # Save report to disk
    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"report_{sim_id}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)
    print(f"  Report saved → {report_path}")

    return SimulationResult(sim_id=sim_id, report=report_data, stats=stats)


def load_existing_sim(sim_id: str) -> SimulationResult:
    print(f"\n[Stage 2] Loading existing simulation {sim_id}...")
    return _fetch_report(sim_id)


def load_report_file(report_path: str) -> SimulationResult:
    print(f"\n[Stage 2] Loading report from {report_path}...")
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    return SimulationResult(sim_id="local", report=report)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 3 — SCRIPT GENERATION (Gemini API — free via Google AI Studio)
# ─────────────────────────────────────────────────────────────────────────────

def generate_script(result: SimulationResult, prompt: str) -> VideoScript:
    print(f"\n[Stage 3] Generating video script via Gemini ({GEMINI_MODEL})...")

    if not GEMINI_API_KEY:
        raise EnvironmentError(
            "\n  GEMINI_API_KEY not set.\n"
            "  1. Go to https://aistudio.google.com/app/apikey\n"
            "  2. Create a free API key\n"
            "  3. Add this line to your .env file:\n"
            "     GEMINI_API_KEY=your_key_here\n"
        )

    try:
        import google.generativeai as genai
    except ImportError:
        raise ImportError("Run: pip install google-generativeai")

    report_summary = _summarize_report(result.report)
    stats_block    = _format_stats(result.stats)

    user_message = (
        f"Simulation prompt: {prompt}\n\n"
        f"Simulation stats:\n{stats_block}\n\n"
        f"Report summary:\n{report_summary}\n\n"
        f"Write the SwarmSaid video script as JSON."
    )

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SWARM_SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            temperature=0.7,
            max_output_tokens=1024,
            response_mime_type="application/json",
        ),
    )

    response = model.generate_content(user_message)
    raw = response.text.strip()

    # Strip any accidental markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()

    try:
        script_dict = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            script_dict = json.loads(match.group())
        else:
            raise ValueError(f"Could not parse JSON from Gemini response:\n{raw[:500]}")

    script = VideoScript.from_dict(script_dict)
    print(f"  Hook     : {script.hook[:80]}...")
    print(f"  YT title : {script.title_youtube}")
    print(f"  TT title : {script.title_tiktok}")
    return script


def _summarize_report(report: dict) -> str:
    if isinstance(report, str):
        return report[:4000]
    sections = report.get("sections", [])
    if sections:
        return "\n\n".join(
            f"## {s.get('title', '')}\n{s.get('content', '')[:1000]}"
            for s in sections[:5]
        )
    return json.dumps(report, ensure_ascii=False)[:4000]


def _format_stats(stats: dict) -> str:
    lines = []
    fields = [
        ("entities_count", "agents/entities generated"),
        ("profiles_count", "agent profiles created"),
        ("current_round",  "simulation rounds completed"),
        ("nodes_count",    "knowledge graph nodes"),
        ("edges_count",    "knowledge graph edges"),
        ("facts_count",    "facts extracted"),
    ]
    for key, label in fields:
        val = stats.get(key)
        if val is not None:
            lines.append(f"- {val} {label}")
    return "\n".join(lines) if lines else "(stats not available)"


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 4 — VOICEOVER (edge-tts — free, no API key, no GPU required)
# ─────────────────────────────────────────────────────────────────────────────

def generate_voiceover(script: VideoScript, output_dir: Path, voice: str) -> Path:
    print(f"\n[Stage 4] Generating voiceover via edge-tts ({voice})...")

    try:
        import edge_tts
    except ImportError:
        raise ImportError("Run: pip install edge-tts")

    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "narration.mp3"

    narration = script.full_narration()
    print(f"  Narration: {len(narration):,} chars")

    async def _synthesize():
        communicate = edge_tts.Communicate(narration, voice)
        await communicate.save(str(audio_path))

    asyncio.run(_synthesize())
    size_kb = audio_path.stat().st_size // 1024
    print(f"  Audio saved → {audio_path}  ({size_kb} KB)")
    return audio_path


def list_voices():
    """Print available English edge-tts voices."""
    try:
        import edge_tts
    except ImportError:
        print("Run: pip install edge-tts")
        return

    async def _list():
        voices = await edge_tts.list_voices()
        en_voices = [v for v in voices if v["Locale"].startswith("en-")]
        print("\nAvailable English voices for edge-tts:")
        print(f"  {'ShortName':<38} Gender")
        print(f"  {'-'*38} ------")
        for v in sorted(en_voices, key=lambda x: x["ShortName"]):
            print(f"  {v['ShortName']:<38} {v['Gender']}")

    asyncio.run(_list())


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 5a — PEXELS FOOTAGE (real stock video — free API)
# ─────────────────────────────────────────────────────────────────────────────

# Keywords to search for each section of the video
# These are derived from common SwarmSaid topics — Gemini also fills on_screen_stats
# which we use to extract search terms
_PEXELS_FALLBACK_QUERIES = [
    "intelligence network data",
    "war military geopolitics",
    "economic crisis financial",
    "government documents classified",
    "artificial intelligence technology",
]

def fetch_pexels_clips(prompt: str, output_dir: Path,
                       n_clips: int = 3, duration_each: int = 6) -> list:
    """
    Search Pexels for stock video clips related to the simulation prompt.
    Returns list of downloaded video file paths.
    Falls back to matplotlib frames if API key not set or quota exceeded.
    """
    if not PEXELS_API_KEY:
        print("  (PEXELS_API_KEY not set — using matplotlib frames instead)")
        return []

    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Extract 3 search terms from the prompt
    search_terms = _extract_search_terms(prompt, n_clips)
    downloaded = []

    headers = {"Authorization": PEXELS_API_KEY}

    for i, query in enumerate(search_terms):
        print(f"  Searching Pexels: '{query}'...")
        try:
            url = (
                f"https://api.pexels.com/videos/search"
                f"?query={requests.utils.quote(query)}"
                f"&per_page=5&orientation=landscape&size=medium"
            )
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            videos = data.get("videos", [])
            if not videos:
                print(f"    No results for '{query}', skipping.")
                continue

            # Pick the first video that has an HD file
            video_url = None
            for video in videos:
                for vf in video.get("video_files", []):
                    if vf.get("quality") in ("hd", "sd") and vf.get("link"):
                        video_url = vf["link"]
                        break
                if video_url:
                    break

            if not video_url:
                continue

            clip_path = clips_dir / f"clip_{i:02d}.mp4"
            print(f"    Downloading clip {i+1}/{n_clips}...")
            req = urllib.request.Request(video_url, headers={"User-Agent": "SwarmSaid/1.0"})
            with urllib.request.urlopen(req, timeout=60) as response:
                with open(clip_path, "wb") as f:
                    f.write(response.read())

            downloaded.append(clip_path)
            print(f"    ✓ {clip_path.name}")

        except Exception as e:
            print(f"    Pexels error for '{query}': {e}")
            continue

    return downloaded


def _extract_search_terms(prompt: str, n: int) -> list:
    """Generate n simple search queries from the simulation prompt."""
    # Simple keyword extraction — pull meaningful noun phrases
    stopwords = {"the", "a", "an", "of", "in", "on", "at", "to", "for",
                 "is", "are", "was", "were", "be", "been", "and", "or",
                 "if", "what", "how", "why", "when", "with", "by", "from",
                 "following", "most", "catastrophic", "effects", "impact"}
    words = [w.strip(".,?!:;\"'()") for w in prompt.split()]
    keywords = [w for w in words if w.lower() not in stopwords and len(w) > 3]

    # Build overlapping 2-word queries
    queries = []
    for i in range(0, len(keywords) - 1, 2):
        q = " ".join(keywords[i:i+2])
        queries.append(q)
        if len(queries) >= n:
            break

    # Pad with fallbacks if needed
    for fb in _PEXELS_FALLBACK_QUERIES:
        if len(queries) >= n:
            break
        if fb not in queries:
            queries.append(fb)

    return queries[:n]


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 5b — VISUAL FRAMES (matplotlib overlays for stats/titles)
# ─────────────────────────────────────────────────────────────────────────────

def generate_frames(script: VideoScript, result: SimulationResult,
                    output_dir: Path) -> list:
    print(f"\n[Stage 5] Generating overlay frames (matplotlib)...")

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames = []

    f1 = _make_stats_frame(script, result, frames_dir)
    frames.append(f1)
    print(f"  Stats frame   → {f1.name}")

    f2 = _make_network_frame(result, frames_dir)
    frames.append(f2)
    print(f"  Network frame → {f2.name}")

    f3 = _make_title_frame(script, frames_dir)
    frames.append(f3)
    print(f"  Title frame   → {f3.name}")

    return frames


def _dark_fig(w: int = 16, h: int = 9):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)
    ax.axis("off")
    return fig, ax


def _make_stats_frame(script: VideoScript, result: SimulationResult,
                      frames_dir: Path) -> Path:
    fig, ax = _dark_fig()

    stats = script.on_screen_stats or _auto_stats(result.stats)
    n    = len(stats)
    cols = min(3, n)
    rows = (n + cols - 1) // cols

    cell_w  = 0.85 / cols
    cell_h  = 0.50 / rows
    x_start = 0.075
    y_start = 0.65

    for i, stat in enumerate(stats):
        col = i % cols
        row = i // cols
        x = x_start + col * (cell_w + 0.02)
        y = y_start - row * (cell_h + 0.04)

        rect = patches.FancyBboxPatch(
            (x, y - cell_h), cell_w, cell_h,
            boxstyle="round,pad=0.01",
            linewidth=1.5, edgecolor=COLOR_ACCENT,
            facecolor="#0d1a14", transform=ax.transAxes,
        )
        ax.add_patch(rect)
        ax.text(x + cell_w / 2, y - cell_h / 2, stat,
                transform=ax.transAxes, ha="center", va="center",
                fontsize=22, fontweight="bold", color=COLOR_ACCENT,
                fontfamily="monospace")

    ax.text(0.5, 0.92, "THE SWARM HAS SPOKEN",
            transform=ax.transAxes, ha="center", va="top",
            fontsize=32, fontweight="bold", color=COLOR_TEXT)
    ax.text(0.5, 0.86, script.title_youtube,
            transform=ax.transAxes, ha="center", va="top",
            fontsize=17, color=COLOR_CYAN, style="italic")
    ax.text(0.5, 0.06, "The swarm already knows.",
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=20, color=COLOR_MAGENTA, style="italic")

    path = frames_dir / "frame_stats.png"
    plt.tight_layout(pad=0)
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close(fig)
    return path


def _make_network_frame(result: SimulationResult, frames_dir: Path) -> Path:
    fig, ax = _dark_fig()

    n_agents = result.stats.get("entities_count", 69)
    n_nodes  = result.stats.get("nodes_count", 226)
    rng = np.random.default_rng(42)

    n_points = min(n_agents * 3, 300)
    theta = rng.uniform(0, 2 * np.pi, n_points)
    r     = rng.beta(2, 5, n_points) * 0.42 + 0.03
    x     = 0.5 + r * np.cos(theta)
    y     = 0.5 + r * np.sin(theta) * 0.85

    sizes  = rng.uniform(10, 80, n_points)
    alphas = rng.uniform(0.3, 0.9, n_points)

    for xi, yi, s, a in zip(x, y, sizes, alphas):
        c = COLOR_ACCENT if rng.random() > 0.4 else COLOR_CYAN
        ax.scatter(xi, yi, s=s, c=c, alpha=a, transform=ax.transAxes, zorder=3)

    n_edges = min(n_nodes, 80)
    idx = rng.choice(n_points, (n_edges, 2), replace=True)
    for i, j in idx:
        if abs(x[i] - x[j]) + abs(y[i] - y[j]) < 0.25:
            ax.plot([x[i], x[j]], [y[i], y[j]],
                    color=COLOR_ACCENT, alpha=0.08, linewidth=0.6,
                    transform=ax.transAxes, zorder=2)

    for radius, alpha in [(0.18, 0.06), (0.12, 0.10), (0.06, 0.18)]:
        circle = plt.Circle((0.5, 0.5), radius, color=COLOR_ACCENT,
                             alpha=alpha, transform=ax.transAxes, zorder=1)
        ax.add_patch(circle)

    ax.text(0.5, 0.94, "SWARM INTELLIGENCE NETWORK",
            transform=ax.transAxes, ha="center", va="top",
            fontsize=26, fontweight="bold", color=COLOR_TEXT)
    ax.text(0.5, 0.07, f"{n_agents} AGENTS  ·  {n_nodes} NODES",
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=16, color=COLOR_ACCENT, fontfamily="monospace")

    path = frames_dir / "frame_network.png"
    plt.tight_layout(pad=0)
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close(fig)
    return path


def _make_title_frame(script: VideoScript, frames_dir: Path) -> Path:
    fig, ax = _dark_fig()

    for y_line in np.linspace(0, 1, 120):
        ax.axhline(y_line, color="#ffffff", alpha=0.015,
                   linewidth=0.5, transform=ax.transAxes)

    ax.text(0.5, 0.72, "SWARM",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=90, fontweight="black", color=COLOR_ACCENT,
            fontfamily="monospace", alpha=0.15)
    ax.text(0.5, 0.62, "SAID",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=90, fontweight="black", color=COLOR_ACCENT,
            fontfamily="monospace")
    ax.text(0.5, 0.40, _wrap_text(script.hook, 55),
            transform=ax.transAxes, ha="center", va="center",
            fontsize=20, color=COLOR_TEXT, style="italic", linespacing=1.6)
    ax.text(0.5, 0.14, "The swarm already knows.",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=22, color=COLOR_MAGENTA, fontweight="bold", style="italic")

    path = frames_dir / "frame_reveal.png"
    plt.tight_layout(pad=0)
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor=COLOR_BG)
    plt.close(fig)
    return path


def _auto_stats(stats: dict) -> list:
    result = []
    mapping = [
        ("entities_count", "{v} AGENTS"),
        ("profiles_count", "{v} PROFILES"),
        ("current_round",  "{v} ROUNDS"),
        ("nodes_count",    "{v} NODES"),
        ("edges_count",    "{v} EDGES"),
        ("facts_count",    "{v} FACTS"),
    ]
    for key, template in mapping:
        val = stats.get(key)
        if val is not None:
            result.append(template.format(v=val))
    return result or ["SWARM ACTIVE", "PREDICTION READY"]


def _wrap_text(text: str, width: int) -> str:
    words = text.split()
    lines, line = [], []
    for word in words:
        if sum(len(w) + 1 for w in line) + len(word) <= width:
            line.append(word)
        else:
            if line:
                lines.append(" ".join(line))
            line = [word]
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 6 — VIDEO ASSEMBLY (FFmpeg)
# ─────────────────────────────────────────────────────────────────────────────

def assemble_video(script: VideoScript, audio_path: Path, frames: list,
                   output_dir: Path, pexels_clips: list = None,
                   music_path: Optional[str] = None) -> dict:
    print(f"\n[Stage 6] Assembling video with FFmpeg...")

    try:
        import ffmpeg
    except ImportError:
        raise ImportError("Run: pip install ffmpeg-python")

    output_dir.mkdir(exist_ok=True)

    probe    = ffmpeg.probe(str(audio_path))
    duration = float(probe["format"]["duration"])
    print(f"  Audio duration: {duration:.1f}s")

    use_pexels = bool(pexels_clips)
    if use_pexels:
        print(f"  Mode: Pexels stock footage ({len(pexels_clips)} clips) + overlay frames")
    else:
        print(f"  Mode: matplotlib frames only")

    outputs = {}
    bg_hex  = COLOR_BG.lstrip("#")

    for fmt, w, h, suffix in [
        ("youtube", 1920, 1080, "youtube"),
        ("tiktok",  1080, 1920, "tiktok"),
    ]:
        out_path = output_dir / f"swarmsaid_{suffix}.mp4"
        print(f"  Rendering {fmt} ({w}×{h})...")

        if use_pexels:
            video_in = _build_pexels_video(
                pexels_clips, frames, duration, w, h, bg_hex, output_dir
            )
        else:
            video_in = _build_frames_video(frames, duration, w, h, bg_hex, output_dir)

        audio_in = ffmpeg.input(str(audio_path))

        if music_path and Path(music_path).exists():
            music_in = (
                ffmpeg.input(music_path)
                .filter("volume", 0.15)
                .filter("atrim", duration=duration)
            )
            audio_final = ffmpeg.filter(
                [audio_in, music_in], "amix", inputs=2, duration="first"
            )
        else:
            audio_final = audio_in

        (
            ffmpeg.output(
                video_in, audio_final, str(out_path),
                vcodec="libx264", acodec="aac",
                preset="fast", crf=20,
                movflags="+faststart",
                t=duration,
            )
            .overwrite_output()
            .run(quiet=True)
        )
        print(f"  ✓ {out_path.name}")
        outputs[fmt] = out_path

    # Clean up temp files
    for tmp in (output_dir / "framelist.txt", output_dir / "cliplist.txt"):
        tmp.unlink(missing_ok=True)

    return outputs


def _build_frames_video(frames: list, duration: float, w: int, h: int,
                        bg_hex: str, output_dir: Path):
    """Build video from matplotlib frames (fallback when no Pexels)."""
    import ffmpeg
    n   = len(frames)
    seg = duration / n
    frame_list_path = output_dir / "framelist.txt"
    with open(frame_list_path, "w") as f:
        for frame in frames:
            f.write(f"file '{frame.resolve()}'\n")
            f.write(f"duration {seg:.2f}\n")
        f.write(f"file '{frames[-1].resolve()}'\n")

    return (
        ffmpeg
        .input(str(frame_list_path), format="concat", safe=0, r=24)
        .filter("scale", w, h, force_original_aspect_ratio="decrease")
        .filter("pad", w, h, "(ow-iw)/2", "(oh-ih)/2", color=bg_hex)
    )


def _build_pexels_video(clips: list, overlay_frames: list, duration: float,
                        w: int, h: int, bg_hex: str, output_dir: Path):
    """
    Build video from Pexels stock clips looped/concatenated to fill duration,
    with matplotlib overlay frames composited on top at 35% opacity.
    """
    import ffmpeg

    seg_per_clip = duration / len(clips)

    # Build clip concat list
    clip_list_path = output_dir / "cliplist.txt"
    with open(clip_list_path, "w") as f:
        for clip in clips:
            f.write(f"file '{clip.resolve()}'\n")
        # Repeat clips if needed to fill duration
        while len(clips) * seg_per_clip < duration:
            for clip in clips:
                f.write(f"file '{clip.resolve()}'\n")

    # Base: Pexels footage scaled + cropped to target size, darkened for readability
    base = (
        ffmpeg.input(str(clip_list_path), format="concat", safe=0, r=24)
        .filter("scale", w, h, force_original_aspect_ratio="increase")
        .filter("crop", w, h)
        .filter("colorchannelmixer",
                rr=0.35, gg=0.35, bb=0.35)        # Darken to ~35% brightness
        .filter("vignette", angle="PI/4")           # Cinematic vignette
        .trim(duration=duration)
        .filter("setpts", "PTS-STARTPTS")
    )

    # Overlay: cycle through matplotlib frames
    n_overlays = len(overlay_frames)
    seg_overlay = duration / n_overlays
    frame_list_path = output_dir / "framelist.txt"
    with open(frame_list_path, "w") as f:
        for frame in overlay_frames:
            f.write(f"file '{frame.resolve()}'\n")
            f.write(f"duration {seg_overlay:.2f}\n")
        f.write(f"file '{overlay_frames[-1].resolve()}'\n")

    overlay = (
        ffmpeg.input(str(frame_list_path), format="concat", safe=0, r=24)
        .filter("scale", w, h)
        .filter("format", "rgba")
        .filter("colorchannelmixer", aa=0.55)       # 55% opacity overlay
    )

    # Composite: base + overlay
    return ffmpeg.overlay(base, overlay, x=0, y=0)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 7 — METADATA
# ─────────────────────────────────────────────────────────────────────────────

def save_metadata(script: VideoScript, output_dir: Path, outputs: dict) -> Path:
    meta = {
        "title_youtube":   script.title_youtube,
        "title_tiktok":    script.title_tiktok,
        "description":     script.description,
        "hook":            script.hook,
        "build":           script.build,
        "reveal":          script.reveal,
        "context":         script.context,
        "cta":             script.cta,
        "on_screen_stats": script.on_screen_stats,
        "files": {k: str(v) for k, v in outputs.items()},
    }
    meta_path = output_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  Metadata → {meta_path}")
    return meta_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SwarmSaid — document in, finished video out.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--input",       metavar="FILE",
                             help="PDF or document to run through MiroFish")
    input_group.add_argument("--sim-id",      metavar="ID",
                             help="Reuse a completed simulation by ID")
    input_group.add_argument("--report-file", metavar="FILE",
                             help="Load report directly from a JSON file")

    parser.add_argument("--prompt",      required=True,
                        help="The simulation question / prediction focus")
    parser.add_argument("--output",      default="swarm_said_output",
                        help="Output directory (default: swarm_said_output)")
    parser.add_argument("--voice",       default=DEFAULT_VOICE,
                        help=f"edge-tts narrator voice (default: {DEFAULT_VOICE})")
    parser.add_argument("--music",       metavar="FILE",
                        help="Path to background music file (Suno mp3/wav, mixed at 15%%)")
    parser.add_argument("--script-only", action="store_true",
                        help="Generate and print script only — skip audio and video")
    parser.add_argument("--list-voices", action="store_true",
                        help="List available English edge-tts voices and exit")

    args = parser.parse_args()

    if args.list_voices:
        list_voices()
        return

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True)

    print("\n" + "═" * 60)
    print("  SWARMSAID — The swarm already knows.")
    print("═" * 60)

    # Stage 1-2: Simulation / report
    if args.input:
        result = run_simulation(args.input, args.prompt)
    elif args.sim_id:
        result = load_existing_sim(args.sim_id)
    elif args.report_file:
        result = load_report_file(args.report_file)
    else:
        parser.error("Provide one of: --input, --sim-id, or --report-file")

    # Stage 3: Script (Gemini)
    script = generate_script(result, args.prompt)

    script_path = output_dir / "script.json"
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script.__dict__, f, indent=2, ensure_ascii=False)
    print(f"\n  Script saved → {script_path}")

    if args.script_only:
        print("\n  --script-only flag set. Done.\n")
        _print_script(script)
        return

    # Stage 4: Voiceover (edge-tts)
    audio_path = generate_voiceover(script, output_dir, args.voice)

    # Stage 5a: Pexels stock footage (if API key set)
    pexels_clips = fetch_pexels_clips(args.prompt, output_dir, n_clips=3)

    # Stage 5b: Matplotlib overlay frames (stats / title cards)
    frames = generate_frames(script, result, output_dir)

    # Stage 6: Video assembly
    outputs = assemble_video(script, audio_path, frames, output_dir,
                             pexels_clips=pexels_clips,
                             music_path=args.music)

    # Stage 7: Metadata
    save_metadata(script, output_dir, outputs)

    print("\n" + "═" * 60)
    print("  DONE")
    print("═" * 60)
    _print_script(script)
    print(f"\n  YouTube → {outputs.get('youtube', 'N/A')}")
    print(f"  TikTok  → {outputs.get('tiktok',  'N/A')}")
    print()


def _print_script(script: VideoScript):
    print(f"\n  ── SCRIPT ──────────────────────────────────────────")
    print(f"  HOOK    : {script.hook}")
    print(f"  BUILD   : {script.build}")
    print(f"  REVEAL  : {script.reveal}")
    print(f"  CONTEXT : {script.context}")
    print(f"  CTA     : {script.cta}")
    print(f"  YT TITLE: {script.title_youtube}")
    print(f"  TT TITLE: {script.title_tiktok}")


if __name__ == "__main__":
    main()
