#!/bin/bash
# TTS Voices 2.2.5 — Update Script
GREEN='\033[0;32m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}TTS Voices 2.2.5 Update${NC}\n"

# Find app location
APP_DIR=""
APP_FILE=$(which ttsvoices 2>/dev/null)
if [ -n "$APP_FILE" ]; then
    APP_DIR=$(grep 'cd ' "$APP_FILE" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"')
fi
for D in "$HOME/tts_work" "$HOME/ttsvoices" "$HOME/Downloads/tts_work" \
         "$HOME/Documents/tts_work" "/opt/ttsvoices" \
         "$HOME/Downloads/AI WRITTEN GAME CODE AND GAME/Ai Created Apps/ttsvoices_fixed"; do
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
cp "$APP_DIR/ttsvoices.py"     "$APP_DIR/ttsvoices.py.bak"     2>/dev/null
cp "$APP_DIR/voice_library.py" "$APP_DIR/voice_library.py.bak" 2>/dev/null
cp "$APP_DIR/voices.py"        "$APP_DIR/voices.py.bak"        2>/dev/null
cp "$SCRIPT_DIR/ttsvoices.py"     "$APP_DIR/ttsvoices.py"     && echo -e "  ${GREEN}✓ ttsvoices.py${NC}"
cp "$SCRIPT_DIR/voice_library.py" "$APP_DIR/voice_library.py" && echo -e "  ${GREEN}✓ voice_library.py${NC}"
cp "$SCRIPT_DIR/voices.py"        "$APP_DIR/voices.py"        && echo -e "  ${GREEN}✓ voices.py${NC}"
rm -rf "$APP_DIR/__pycache__" 2>/dev/null && echo -e "  ${GREEN}✓ cache cleared${NC}"

echo -e "\n${GREEN}Updated to v2.2.5!${NC}"
echo "  • Fixed crash: export status TypeError"
echo "  • Export bar no longer glows during speech"
echo "  • File search: selected file stays visible on typo"
echo "  • Folders always visible while searching"
echo "  • File dialog: no more blank gap at top"
echo "  • Bug log Refresh button now works"
echo "  • Theme picker: Cancel button removed"
echo "  • Voice Library: ℹ info button removed"
echo "  • Save point: shows 'Saved point cleared' immediately"
echo "  • Speed/pitch: restarts faster (200ms debounce)"
