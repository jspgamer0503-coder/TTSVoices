# TTS Voices 2.5.0
**Unlimited Text-to-Speech Engine for Linux**

A professional-grade Linux desktop TTS application with neural voice synthesis,
multi-format document support, and a modern dark UI.

---

## Development & Maintenance

> **Active development and ongoing maintenance is performed by the
> [opencode](https://opencode.ai) AI coding assistant**, working under
> the direction of the project owner.
>
> All releases from **v2.3.0 onward** have been developed, audited, and
> shipped with AI assistance. The current 2.5.0 release added the Edge TTS
> (Cloud) engine, redesigned the system resources display, and incorporates
> 29 cumulative bug fixes across the 2.4.x line.
>
> **Project owner:** overseer (this repository)
> **Active maintainer:** opencode AI assistant (v2.3.0 – present)
> **Original implementation:** v1.x – v2.0 (see git history for prior authorship)

When reporting issues, please include the version number from the
header subtitle (`vX.Y.Z  ·  ...`) and a Bug Log export from
`🐞 Bug Log` → `Save to file`. Both are essential for triage.

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
- **Three TTS engines** – Edge TTS (cloud, 7-9x faster) → Kokoro ONNX (offline) → espeak-ng (always works)
- **17 Edge TTS voices** – en-US/GB/AU, male/female/child/multilingual
- **Privacy toggle** – Settings → Cloud TTS to disable sending text to Microsoft servers
- **Audio export** – WAV and MP3 via ffmpeg
- **Voice Library** – download and manage voice models in-app
- **Save points** – resume long transcriptions from any chunk
- **Dark UI** – modern navy/blue theme with glow accents, 8 themes available
- **Plugin system** – `~/.ttsvoices/plugins/` for user extensions
- **Per-core resource monitor** – live CPU/RAM/Disk/Net display in the header

---

## Engines & Voices

| Engine | Type | Quality | Offline | Network | Size |
|--------|------|---------|---------|---------|------|
| **Edge TTS (Cloud)** | Neural | ★★★★★ | ✗ | Required | 0 MB |
| Kokoro ONNX | Neural | ★★★★☆ | ✓ | No | ~326 MB |
| espeak-ng | Formant | ★★☆☆☆ | ✓ | No | <5 MB |

**Fallback chain:** Edge TTS → Kokoro → espeak-ng. If the preferred engine
fails, the next one is tried automatically without crashing.

**Edge TTS voices (cloud, no model download):**
Aria, Jenny, Sara, Ana, Michelle (US Female) · Guy, Davis, Tony (US Male) ·
Andrew, Emma, Brian (US Multilingual) · Sonia, Ryan, Libby, William (UK) ·
Natasha, William (AU)

**Kokoro voices** (after model download):
Heart, Bella, Sarah, Nicole, Sky (US Female) · Adam, Michael (US Male) ·
Emma, Isabella (UK Female) · George, Lewis (UK Male)

Disable cloud TTS entirely via Settings → "Use Edge TTS (Cloud)" toggle.

---

## System Requirements

- **OS:** Ubuntu 20.04+, Kali Linux, Debian 11+, Linux Mint 20+
- **Python:** 3.10 or newer
- **RAM:** 4 GB minimum, 8 GB recommended (for Kokoro)
- **Disk:** ~500 MB app + 326 MB Kokoro models (if using local engine)

### System packages
```bash
sudo apt install python3-venv python3-tk espeak-ng ffmpeg alsa-utils
```

### Optional Python packages (for full feature set)
```bash
pip install kokoro-onnx onnxruntime pdfplumber pypdf python-docx \
            ebooklib beautifulsoup4 lxml striprtf chardet pikepdf \
            msoffcrypto-tool pycryptodome argon2-cffi requests numpy
pip install edge-tts     # optional: cloud TTS engine (recommended)
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
├── ttsvoices.py            # Main GUI application (~6700 lines)
├── voices.py               # TTS engine abstraction & fallback chain
├── voice_library.py        # Voice model download & management UI
├── file_extractor.py       # Multi-format document text extraction
├── audio_handler.py        # Audio playback (aplay/paplay/ffplay)
├── save_point_manager.py   # Resume long transcriptions
├── bug_tracker.py          # Error logging & crash recovery
├── dep_installer.py        # First-run dependency installer
├── exceptions.py           # Custom exception hierarchy
├── odf_crypto.py           # ODT AES-256 decryption
├── health_check.py         # Static analysis & self-tests
├── build_audio_fast.py     # Compiles audio_fast.c extension
├── install.sh              # System installer
├── requirements.txt        # Python dependencies
├── CHANGELOG.md            # Release history
├── DEVELOPMENT_PLAN.md     # Roadmap & future plans
├── DEVELOPER_CODE_REVIEW.md # Code review notes
└── README.md               # This file
```

Config & logs: `~/.ttsvoices/`  
Models: `~/.ttsvoices/models/`  
Logs: `~/.ttsvoices/logs/`  
Plugins: `~/.ttsvoices/plugins/`

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

**Edge TTS not in voice list:**
```bash
pip install edge-tts
```
Also check: Settings → "Use Edge TTS (Cloud)" must be ON, and you need
network access to `speech.platform.bing.com:443`.

**PDF extraction fails:**
```bash
pip install pdfplumber pypdf pikepdf
# or for CLI fallback:
sudo apt install poppler-utils
```

**MP3 export fails / Edge TTS silent:**
```bash
sudo apt install ffmpeg
```

**System resources display shows nothing:**
The monitor is no-op if `psutil` is not installed. Either install
`pip install psutil` or use the `/proc`-based fallback (built-in on Linux).

---

## License

Application code: MIT  
Kokoro ONNX model: Apache 2.0  
Edge TTS service: Microsoft Online Services Terms (no API key required)  
espeak-ng: GPL v3

---

## Auto-Update Feature (v2.3.0)

When an update is available, a glowing **⬆ Update now (x.y.z)** button appears in the header.

| Feature | Detail |
|---|---|
| Auto-check | Toggle in Settings or the right-panel switch |
| Manual check | Click ⟳ Updates in the header |
| Update action | Click the glowing button → choose Run update.sh or View on GitHub |
| Dep check | Settings → "Check now" lists outdated pip packages (venv only) |

The check reads `VERSION` from the GitHub repo once at startup (3-second delay so the UI is fully loaded first). It never blocks the app and fails silently when offline.

---

## System Resources Display (v2.5.0)

The header subtitle shows live system metrics in a compact, custom format:

```
v2.5.0 · ▶0 · CPU ▁▃▅▂▁▃▅▂ 26% · RAM 2.3/7.5G · DSK 69% · ▲35B ▼309B
```

- **Per-core block characters** (▁▂▃▄▅) – one per logical core
- **RAM in GB** (used/total) instead of just %
- **Disk usage** of root partition
- **Network I/O** (▲up ▼down) in B/K/M/G per second
- Colour shifts cyan → amber → red at load transitions

---

## See also

- `CHANGELOG.md` – complete release history with all bug fixes
- `DEVELOPMENT_PLAN.md` – roadmap, deferred items, and known limitations
- `DEVELOPER_CODE_REVIEW.md` – audit notes from the v2.4.x reliability pass
- `health_check.py` – run `python3 health_check.py` for a 65-test self-audit

