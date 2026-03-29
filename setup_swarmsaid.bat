@echo off
REM ============================================================
REM  SwarmSaid Pipeline Setup
REM  Run this once to install everything generate_video.py needs
REM ============================================================

echo.
echo  SwarmSaid -- Setup
echo  ==========================================
echo.

REM ── 1. Activate MiroFish conda env ──
call conda activate MiroFish 2>nul || (
    echo [!] MiroFish conda env not found. Activate your env manually then re-run.
    pause & exit /b 1
)

echo [1/4] Installing Python dependencies...
pip install ^
    requests ^
    google-generativeai ^
    edge-tts ^
    numpy ^
    matplotlib ^
    ffmpeg-python ^
    Pillow ^
    --quiet

echo      Done.

echo [2/4] Setting up API keys in .env ...
echo.
echo  You need two free API keys:
echo.
echo    GEMINI_API_KEY  -- get free at https://aistudio.google.com/app/apikey
echo    PEXELS_API_KEY  -- get free at https://www.pexels.com/api/
echo.
echo  These will be added to your .env file in the MiroFish directory.
echo  (HF_TOKEN is optional -- only needed for HuggingFace model downloads)
echo.

set /p GEMINI_KEY="Paste your GEMINI_API_KEY (or press Enter to skip): "
set /p PEXELS_KEY="Paste your PEXELS_API_KEY (or press Enter to skip): "
set /p HF_TOK="Paste your HF_TOKEN (or press Enter to skip): "

REM Append keys to .env (create if missing)
if not exist ".env" echo. > .env

if not "%GEMINI_KEY%"=="" (
    echo GEMINI_API_KEY=%GEMINI_KEY%>> .env
    echo      GEMINI_API_KEY saved to .env
)
if not "%PEXELS_KEY%"=="" (
    echo PEXELS_API_KEY=%PEXELS_KEY%>> .env
    echo      PEXELS_API_KEY saved to .env
)
if not "%HF_TOK%"=="" (
    echo HF_TOKEN=%HF_TOK%>> .env
    echo      HF_TOKEN saved to .env
)

echo [3/4] Verifying ffmpeg...
where ffmpeg >nul 2>&1 && (
    echo      ffmpeg found -- OK
) || (
    echo  [!] ffmpeg not found.
    echo      Download: https://github.com/BtbN/FFmpeg-Builds/releases
    echo      Extract to C:\ffmpeg and add C:\ffmpeg\bin to PATH.
    echo      Then re-run this script.
    pause & exit /b 1
)

echo [4/4] Quick dependency check...
python -c "import google.generativeai; print('     google-generativeai -- OK')" 2>nul || echo  [!] google-generativeai not installed
python -c "import edge_tts; print('     edge-tts -- OK')" 2>nul || echo  [!] edge-tts not installed
python -c "import matplotlib; print('     matplotlib -- OK')" 2>nul || echo  [!] matplotlib not installed
python -c "import ffmpeg; print('     ffmpeg-python -- OK')" 2>nul || echo  [!] ffmpeg-python not installed

echo.
echo  ==========================================
echo  Setup complete! Try it out:
echo.
echo  Quick test (script only, uses existing sim):
echo    python generate_video.py --sim-id sim_80d86549391e --prompt "Strait of Hormuz closure" --script-only
echo.
echo  Full pipeline from a document:
echo    python generate_video.py --input path\to\doc.pdf --prompt "Your question here"
echo.
echo  With background music (Suno download):
echo    python generate_video.py --input doc.pdf --prompt "..." --music suno_track.mp3
echo.
echo  List available narrator voices:
echo    python generate_video.py --list-voices
echo  ==========================================
echo.
pause
