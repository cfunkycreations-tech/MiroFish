# SwarmSaid Video Pipeline

> One command. Document in. Finished video out.
> *The swarm already knows.*

---

## Free Stack

| Stage | Tool | Cost |
|-------|------|------|
| Script generation | Google Gemini API (Gemini 2.0 Flash) | Free — 1,500 req/day |
| Voiceover | edge-tts (Microsoft Edge neural TTS) | Free — no key needed |
| Stock footage | Pexels API | Free — 200 req/hour |
| Visuals / overlays | matplotlib | Free |
| Video assembly | FFmpeg | Free |
| Background music | Your Suno download (optional) | Free |

---

## Quick Start

```bash
# 1. Run setup (once) — installs deps and saves API keys to .env
setup_swarmsaid.bat

# 2. Test with existing simulation (no re-run needed)
python generate_video.py \
  --sim-id sim_80d86549391e \
  --prompt "Strait of Hormuz 90 day closure economic cascade effects" \
  --script-only

# 3. Full pipeline from a new document
python generate_video.py \
  --input "C:\path\to\document.pdf" \
  --prompt "What are the most catastrophic cascade effects?"

# 4. With background music
python generate_video.py \
  --input document.pdf \
  --prompt "..." \
  --music suno_track.mp3

# 5. Change narrator voice
python generate_video.py --list-voices
python generate_video.py --input doc.pdf --prompt "..." --voice en-US-AriaNeural
```

---

## Pipeline Stages

```
[1] PDF/Doc  →  MiroFish API  →  Simulation runs
[2] Sim result  →  Report agent  →  Structured report JSON
[3] Report JSON  →  Gemini 2.0 Flash  →  SwarmSaid video script (JSON)
[4] Script  →  edge-tts (Microsoft neural TTS)  →  narration.mp3
[5] Prompt  →  Pexels API  →  stock footage clips downloaded
[5b] Sim stats  →  matplotlib  →  overlay frames (stats cards, network viz, title)
[6] footage + overlays + narration + music  →  FFmpeg  →  YouTube 16:9 + TikTok 9:16 MP4
[7] Titles + description  →  metadata.json  →  ready to upload
```

---

## API Keys Required

Get these free — takes 2 minutes each:

| Key | Where to get it | Add to |
|-----|----------------|--------|
| `GEMINI_API_KEY` | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | `.env` |
| `PEXELS_API_KEY` | [pexels.com/api/](https://www.pexels.com/api/) | `.env` |

Your `.env` file (in the MiroFish directory) should look like:
```
GEMINI_API_KEY=your_key_here
PEXELS_API_KEY=your_key_here
HF_TOKEN=your_hf_token_here   # optional
```

---

## Narrator Voices

`edge-tts` uses Microsoft's neural voices — no account, no cost, high quality.

| Voice | Character |
|-------|-----------|
| `en-US-GuyNeural` (default) | Male, deep, authoritative narrator |
| `en-US-AriaNeural` | Female, warm, documentary feel |
| `en-US-DavisNeural` | Male, confident, modern |
| `en-US-JennyNeural` | Female, clear, professional |

Switch with `--voice en-US-AriaNeural`

List all available English voices:
```bash
python generate_video.py --list-voices
```

---

## CLI Reference

```
python generate_video.py [OPTIONS]

Input (pick one):
  --input FILE        PDF or document to run through MiroFish
  --sim-id ID         Reuse an existing completed simulation
  --report-file FILE  Load report directly from a JSON file

Required:
  --prompt TEXT       The simulation question / prediction focus

Options:
  --voice VOICE       edge-tts narrator voice (default: en-US-GuyNeural)
  --music FILE        Path to background music file (Suno mp3/wav, mixed at 15%)
  --output DIR        Output directory (default: swarm_said_output)
  --script-only       Generate script only, skip audio/video assembly
  --list-voices       List available English narrator voices and exit
```

---

## Output Files

```
swarm_said_output/
├── script.json                   ← generated script (hook, reveal, titles, description)
├── metadata.json                 ← upload-ready titles, description, file paths
├── report_sim_XXXX.json          ← raw MiroFish report
├── audio/
│   └── narration.mp3             ← edge-tts narration
├── clips/
│   ├── clip_00.mp4               ← Pexels stock footage clip 1
│   ├── clip_01.mp4               ← Pexels stock footage clip 2
│   └── clip_02.mp4               ← Pexels stock footage clip 3
├── frames/
│   ├── frame_stats.png           ← stats overlay card
│   ├── frame_network.png         ← swarm network visualization
│   └── frame_reveal.png          ← SwarmSaid title card
├── swarmsaid_youtube.mp4         ← 1920×1080 16:9 (YouTube)
└── swarmsaid_tiktok.mp4          ← 1080×1920 9:16 (TikTok/Shorts)
```

---

## Cost Per Video

| Item | Cost |
|------|------|
| MiroFish simulation | ~$0.05–0.20 (OpenRouter) |
| Gemini script gen | $0.00 (free tier) |
| edge-tts narration | $0.00 |
| Pexels footage | $0.00 |
| Total | **~$0.05–0.20** |

---

*SwarmSaid — The swarm already knows.*
*CFunk_Creations LLC*
