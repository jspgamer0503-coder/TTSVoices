# TTS Voices 2.0
**Unlimited Text-to-Speech Engine for Linux**

A professional-grade Linux desktop TTS application with neural voice synthesis,
multi-format document support, and a modern dark UI.

---

## Quick Start

```bash
# 1. Clone / extract the project
cd tts_voices/

# 2. Run the installer (handles everything automatically)
chmod +x install.sh
./install.sh

# 3. Launch
ttsvoices
# or
python3 ttsvoices.py
```

---

## Features

- **Unlimited text processing** – handles documents of any size via smart chunking
- **Multi-format file support** – PDF, DOCX, DOC, EPUB, HTML, RTF, ODT, TXT, MD, CSV
- **Neural voices** – Kokoro ONNX (82M params, offline, Apache 2.0)
- **Fallback chain** – Kokoro → Piper TTS → espeak-ng (always works offline)
- **Audio export** – WAV and MP3 via ffmpeg
- **Voice Library** – download and manage Kokoro voice models in-app
- **Dark UI** – modern navy/blue theme with glow accents
- **Bug tracker** – session log and crash capture

---

## Engines & Voices

| Engine | Type | Quality | Offline | Size |
|--------|------|---------|---------|------|
| Kokoro ONNX | Neural | Excellent | ✓ | ~326 MB |
| Piper TTS | Neural | Good | ✓ | ~50 MB |
| espeak-ng | Formant | Basic | ✓ | <5 MB |

**Kokoro voices** (after model download):
`Heart`, `Bella`, `Sarah`, `Nicole`, `Sky` (US Female)  
`Adam`, `Michael` (US Male)  
`Emma`, `Isabella` (UK Female)  
`George`, `Lewis` (UK Male)

---

## System Requirements

- **OS:** Ubuntu 20.04+, Kali Linux, Debian 11+, Linux Mint 20+
- **Python:** 3.10 or newer
- **RAM:** 4 GB minimum, 8 GB recommended (for Kokoro)
- **Disk:** ~500 MB app + 326 MB Kokoro models

### System packages
```bash
sudo apt install python3-venv python3-tk espeak-ng ffmpeg alsa-utils
```

---

## Manual Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 ttsvoices.py
```

---

## File Structure

```
tts_voices/
├── ttsvoices.py       # Main GUI application
├── voices.py          # TTS engine abstraction & fallback chain
├── audio_handler.py   # Audio playback (pygame → aplay → paplay → ffplay)
├── file_extractor.py  # Multi-format document text extraction
├── voice_library.py   # Voice model download & management UI
├── bug_tracker.py     # Error logging & crash recovery
├── install.sh         # System installer
├── requirements.txt   # Python dependencies
└── README.md
```

Config & logs: `~/.ttsvoices/`  
Models: `~/.ttsvoices/models/`  
Logs: `~/.ttsvoices/logs/`

---

## Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Load file | Click ⬆ Load File |
| Clear text | Click ✕ Clear |
| Speak | Click ▶ SPEAK |
| Stop | Click ■ STOP |

---

## Troubleshooting

**No sound:**  
```bash
sudo apt install alsa-utils pulseaudio
pulseaudio --start
```

**Kokoro not available:**  
```bash
pip install kokoro-onnx onnxruntime
```
Then open Voice Library and download the required models.

**PDF extraction fails:**  
```bash
pip install pdfplumber pypdf
# or for CLI fallback:
sudo apt install poppler-utils
```

**MP3 export fails:**  
```bash
sudo apt install ffmpeg
```

---

## License

Application code: MIT  
Kokoro ONNX model: Apache 2.0  
espeak-ng: GPL v3
