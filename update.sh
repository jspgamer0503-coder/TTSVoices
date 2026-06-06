#!/bin/bash
# TTS Voices 2.5.0 — Update Script
# Maintained by the opencode AI assistant — see README.md.
GREEN='\033[0;32m'; RED='\033[0;31m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}TTS Voices 2.5.0 Update${NC}\n"

# ── Find app location ─────────────────────────────────────────────────────────
APP_DIR=""
APP_FILE=$(which ttsvoices 2>/dev/null)
if [ -n "$APP_FILE" ]; then
    APP_DIR=$(grep 'cd ' "$APP_FILE" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"')
fi
for D in "$HOME/tts_work" "$HOME/ttsvoices" "$HOME/Downloads/tts_work" \
         "$HOME/Documents/tts_work" "/opt/ttsvoices" \
         "$HOME/.local/share/ttsvoices"; do
    [ -f "$D/ttsvoices.py" ] && APP_DIR="$D" && break
done
if [ -z "$APP_DIR" ] || [ ! -f "$APP_DIR/ttsvoices.py" ]; then
    echo "Could not find ttsvoices.py automatically."
    echo -n "Enter full path to your TTS Voices folder: "
    read -r APP_DIR
fi
if [ ! -f "$APP_DIR/ttsvoices.py" ]; then
    echo -e "${RED}Error: ttsvoices.py not found in: $APP_DIR${NC}"; exit 1
fi

echo "Found: $APP_DIR"

# ── Back up existing files ────────────────────────────────────────────────────
echo -e "\n${BLUE}▶ Backing up existing files...${NC}"
for f in ttsvoices.py voice_library.py voices.py audio_handler.py \
          file_extractor.py bug_tracker.py dep_installer.py \
          save_point_manager.py exceptions.py odf_crypto.py; do
    [ -f "$APP_DIR/$f" ] && cp "$APP_DIR/$f" "$APP_DIR/${f%.py}.py.bak" && \
        echo -e "  ${GREEN}✓ backed up $f${NC}"
done

# ── Copy new files ────────────────────────────────────────────────────────────
echo -e "\n${BLUE}▶ Installing updated files...${NC}"
for f in ttsvoices.py voice_library.py voices.py audio_handler.py \
          file_extractor.py bug_tracker.py dep_installer.py \
          save_point_manager.py exceptions.py odf_crypto.py \
          audio_fast.c build_audio_fast.py VERSION; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        cp "$SCRIPT_DIR/$f" "$APP_DIR/$f" && echo -e "  ${GREEN}✓ $f${NC}"
    fi
done

# ── Recompile C extension ─────────────────────────────────────────────────────
echo -e "\n${BLUE}▶ Recompiling C extension...${NC}"
if command -v gcc &>/dev/null; then
    if gcc -O2 -shared -fPIC -o "$APP_DIR/audio_fast.so" "$APP_DIR/audio_fast.c" 2>/dev/null; then
        echo -e "  ${GREEN}✓ audio_fast.so compiled${NC}"
    else
        echo -e "  ${YELLOW}⚠ C compilation failed — Python fallback will be used${NC}"
    fi
fi

# ── Clear cache and dep stamp ─────────────────────────────────────────────────
rm -rf "$APP_DIR/__pycache__" 2>/dev/null && echo -e "  ${GREEN}✓ cache cleared${NC}"
# Remove old dep stamps so the installer re-checks on first launch
rm -f "$HOME/.ttsvoices/.deps_ok_2.2.0" 2>/dev/null
rm -f "$HOME/.ttsvoices/.deps_ok_2.4.1" 2>/dev/null
rm -f "$HOME/.ttsvoices/.deps_ok_2.5.0" 2>/dev/null

echo -e "\n${GREEN}╔══════════════════════════════════════════╗"
echo "║  Updated to TTS Voices v2.5.0  ✓         ║"
echo -e "╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  What's new in v2.5.0:"
echo "  • Auto-update checker (glowing icon when update available)"
echo "  • 'Update now' text shown next to icon when newer version exists"
echo "  • Settings toggle: auto-check on/off"
echo "  • Manual update check button"
echo "  • Dependency update checker (uses venv pip)"
echo "  • Fixed CHANGELOG version ordering"
echo "  • VERSION file for remote version checks"
echo ""
echo "  Run with: ttsvoices"
echo "        or: python3 $APP_DIR/ttsvoices.py"
