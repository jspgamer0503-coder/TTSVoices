#!/bin/bash
# ============================================================
#  TTS Voices 2.5.1 - Installation Script
#  Supports: Ubuntu, Kali Linux, Debian, Linux Mint
#
#  Maintained by the opencode AI assistant — see README.md.
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/venv"
LAUNCHER="/usr/local/bin/ttsvoices"
DESKTOP_FILE="$HOME/.local/share/applications/ttsvoices.desktop"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

step()  { echo -e "\n${BLUE}▶ $1${NC}"; }
ok()    { echo -e "${GREEN}✓ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $1${NC}"; }
error() { echo -e "${RED}✗ $1${NC}"; exit 1; }

echo -e "${BLUE}"
echo "  ████████╗████████╗███████╗"
echo "  ╚══██╔══╝╚══██╔══╝██╔════╝"
echo "     ██║      ██║   ███████╗"
echo "     ██║      ██║   ╚════██║"
echo "     ██║      ██║   ███████║"
echo "     ╚═╝      ╚═╝   ╚══════╝"
echo -e "  TTS Voices 2.5.1 – Installer${NC}\n"

# ── 1. Check Python ──────────────────────────────────────────────────────
step "Checking Python 3.10+"
if ! command -v python3 &>/dev/null; then
    error "Python 3 not found. Install with: sudo apt install python3"
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    error "Python 3.10+ required, found $PY_VER"
fi
ok "Python $PY_VER found"

# ── 2. System dependencies ───────────────────────────────────────────────
step "Installing system dependencies"
if command -v apt-get &>/dev/null; then
    APT_LOCK_WAIT=0
    while sudo fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend \
                    /var/lib/dpkg/lock &>/dev/null 2>&1; do
        if [ $APT_LOCK_WAIT -eq 0 ]; then
            echo "    Waiting for apt lock to clear..."
        fi
        APT_LOCK_WAIT=$((APT_LOCK_WAIT + 1))
        if [ $APT_LOCK_WAIT -ge 60 ]; then
            warn "apt lock held for 60s — skipping system package install."
            break
        fi
        sleep 1
    done

    if [ $APT_LOCK_WAIT -lt 60 ]; then
        sudo apt-get update -qq 2>/dev/null || true
        sudo apt-get install -y -qq \
            python3-venv python3-tk python3-dev \
            espeak-ng ffmpeg alsa-utils \
            gcc build-essential \
            portaudio19-dev libportaudio2 \
            pipewire-audio pipewire pulseaudio-utils \
            2>/dev/null \
            && ok "System packages installed" \
            || warn "Some packages may not have installed — try manually: sudo apt-get install python3-venv python3-tk espeak-ng ffmpeg gcc portaudio19-dev pipewire-audio"
    fi
else
    warn "apt-get not found. Please manually install: python3-venv python3-tk espeak-ng ffmpeg gcc portaudio19-dev"
fi

# ── 3. Virtual environment ───────────────────────────────────────────────
step "Creating Python virtual environment"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created at $VENV_DIR"
else
    ok "Virtual environment already exists"
fi

# ── 4. Activate venv ────────────────────────────────────────────────────
source "$VENV_DIR/bin/activate"

# ── 5. Upgrade pip ──────────────────────────────────────────────────────
step "Upgrading pip"
pip install --upgrade pip wheel --quiet
ok "pip upgraded"

# ── 6. Core TTS engine ──────────────────────────────────────────────────
step "Installing Kokoro ONNX (primary TTS engine)"
pip install --quiet \
    "kokoro-onnx>=0.5.0" \
    "onnxruntime>=1.20.0" \
    "numpy>=2.2.0" \
    && ok "Kokoro ONNX installed" \
    || warn "Kokoro ONNX not available - will use espeak-ng fallback"

step "Installing Intel GPU support (OpenVINO) — optional"
pip install --quiet onnxruntime-openvino \
    && ok "OpenVINO installed — Intel iGPU acceleration available" \
    || warn "OpenVINO not available — CPU will be used (this is fine)"

# ── 7. File extraction libraries ─────────────────────────────────────────
step "Installing file extraction libraries"
pip install --quiet \
    "pdfplumber>=0.11.0" \
    "pypdf>=5.1.0" \
    "python-docx>=1.1.2" \
    "ebooklib>=0.18" \
    "beautifulsoup4>=4.12.3" \
    "lxml>=5.3.0" \
    "striprtf>=0.0.26" \
    "chardet>=5.2.0" \
    "requests>=2.32.3" \
    && ok "File extraction libs installed" \
    || warn "Some extraction libs may be missing"

# ── 8. Encrypted file support ─────────────────────────────────────────────
step "Installing password-protected file support"
pip install --quiet \
    "msoffcrypto-tool>=5.4.2" \
    "pikepdf>=9.4.0" \
    "pycryptodome>=3.21.0" \
    "argon2-cffi>=23.1.0" \
    && ok "Encrypted file support installed" \
    || warn "Some crypto libs failed — password-protected files may not open"

# ── 9. Speech-to-Text for Audio-to-Text converter ────────────────────────────
step "Installing Audio-to-Text transcription engines"
pip install --quiet faster-whisper \
    && ok "faster-whisper installed (best offline STT quality)" \
    || warn "faster-whisper failed — will be installed on first use"

pip install --quiet vosk \
    && ok "vosk installed (lightweight offline STT)" \
    || warn "vosk failed — will be installed on first use"

pip install --quiet SpeechRecognition \
    && ok "SpeechRecognition installed (Google online STT)" \
    || warn "SpeechRecognition failed — will be installed on first use"

# ── 10. Compile C audio extension ────────────────────────────────────────
step "Compiling C audio extension (audio_fast.so)"
if command -v gcc &>/dev/null; then
    if gcc -O2 -shared -fPIC -o "$APP_DIR/audio_fast.so" "$APP_DIR/audio_fast.c" 2>/dev/null; then
        ok "audio_fast.so compiled — C-accelerated WAV export active (~10x faster)"
    else
        warn "C compilation failed — pure Python fallback will be used"
    fi
else
    warn "gcc not found — install with: sudo apt install gcc"
fi

# ── 11. Config directory ─────────────────────────────────────────────────
step "Creating config directories"
mkdir -p "$HOME/.ttsvoices/models"
mkdir -p "$HOME/.ttsvoices/logs"
# Clear first-run stamp so dep_installer re-checks after a clean install
rm -f "$HOME/.ttsvoices/.deps_ok_2.5.1" "$HOME/.ttsvoices/.deps_ok_2.5.0" "$HOME/.ttsvoices/.deps_ok_2.4.1" "$HOME/.ttsvoices/.deps_ok_2.4.0"
ok "Config directories created at ~/.ttsvoices/"

# ── 12. Launcher script ──────────────────────────────────────────────────
step "Creating system launcher"
cat > /tmp/ttsvoices_launcher << EOF
#!/bin/bash
source "$VENV_DIR/bin/activate"
cd "$APP_DIR"
exec python3 "$APP_DIR/ttsvoices.py" "\$@"
EOF

if sudo cp /tmp/ttsvoices_launcher "$LAUNCHER" 2>/dev/null && \
   sudo chmod +x "$LAUNCHER"; then
    ok "Launcher installed at $LAUNCHER"
else
    mkdir -p "$HOME/.local/bin"
    cp /tmp/ttsvoices_launcher "$HOME/.local/bin/ttsvoices"
    chmod +x "$HOME/.local/bin/ttsvoices"
    ok "Launcher installed at ~/.local/bin/ttsvoices"
fi

# ── 13. Desktop file ─────────────────────────────────────────────────────
step "Installing desktop entry"
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=TTS Voices
GenericName=Text to Speech
Comment=Unlimited Text-to-Speech Engine
Exec=$VENV_DIR/bin/python3 $APP_DIR/ttsvoices.py
Terminal=false
Type=Application
Categories=Accessibility;Audio;
Keywords=tts;speech;voice;text;transcribe;
StartupNotify=true
EOF
ok "Desktop entry created"

# ── 14. Self-test ────────────────────────────────────────────────────────
step "Running self-test"
python3 - << 'PYEOF'
import sys, subprocess
errors = []
try:
    import tkinter
    print("  ✓ Tkinter available")
except ImportError:
    errors.append("tkinter missing: sudo apt install python3-tk")

r = subprocess.run(["which", "espeak-ng"], capture_output=True)
if r.returncode == 0: print("  ✓ espeak-ng available")
else: errors.append("espeak-ng missing: sudo apt install espeak-ng")

r = subprocess.run(["which", "ffmpeg"], capture_output=True)
if r.returncode == 0: print("  ✓ ffmpeg available")
else: print("  ⚠ ffmpeg missing (needed for MP3 export): sudo apt install ffmpeg")

try:
    import kokoro_onnx
    print("  ✓ Kokoro ONNX available")
except ImportError:
    print("  ⚠ Kokoro ONNX not installed (espeak-ng will be used)")

try:
    import faster_whisper
    print("  ✓ faster-whisper available (Audio-to-Text: best quality)")
except ImportError:
    print("  ⚠ faster-whisper not installed")

try:
    import vosk
    print("  ✓ vosk available (Audio-to-Text: lightweight offline)")
except ImportError:
    print("  ⚠ vosk not installed")

try:
    import speech_recognition
    print("  ✓ SpeechRecognition available (Audio-to-Text: Google online)")
except ImportError:
    print("  ⚠ SpeechRecognition not installed")

from pathlib import Path
so = Path(__file__).parent / "audio_fast.so" if False else Path(sys.argv[0]).parent / "audio_fast.so"
# Check in app dir
import os
app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
if os.path.exists(os.path.join(app_dir, "audio_fast.so")):
    print("  ✓ audio_fast.so compiled (C-accelerated WAV export)")
else:
    print("  ⚠ audio_fast.so not found (Python fallback for WAV export)")

if errors:
    print("\nWarnings:")
    for e in errors:
        print(f"  ⚠ {e}")
else:
    print("  ✓ All critical checks passed!")
PYEOF

# ── Done ─────────────────────────────────────────────────────────────────
echo -e "\n${GREEN}╔═════════════════════════════════════════╗"
echo "║  TTS Voices 2.5.1 installed successfully  ║"
echo -e "╚═════════════════════════════════════════╝${NC}"
echo ""
echo "  Run with:  ttsvoices"
echo "         or: python3 $APP_DIR/ttsvoices.py"
echo ""
echo "  VERSION file is used for auto-update checks (Settings)
  For Kokoro ONNX voices, open the app and"
echo "  use Voice Library → Download All Required"
echo ""

exit 0
