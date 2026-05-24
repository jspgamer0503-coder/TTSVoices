# TTS Voices — Development Plan

**Current version:** 2.3.0
**Last updated:** May 2026
**Status:** Active development

---

## 1. What Was Built vs What Was Planned

### 1.1 Delivered

| Feature | Version |
|---------|---------|
| Kokoro ONNX offline TTS | 1.0 |
| espeak-ng / Piper fallback chain | 1.0 |
| PDF, DOCX, EPUB, ODT, HTML, RTF extraction | 1.0 |
| Word-level highlight sync | 1.0 |
| WAV + MP3 export (C extension) | 1.0 |
| 12+ themes including AMOLED | 2.1–2.2 |
| AES-256-GCM ODT decryption | 2.0 |
| Bookmark / resume system | 2.0 |
| Voice Library (install/manage models) | 2.0 |
| Single-instance enforcement | 1.0 |
| AMD GPU / ROCm + Intel OpenVINO | 2.1 |
| Plugin system (⊕ Plugins nav button) | 2.3 |
| Auto-update checker (urllib, silent) | 2.3 |
| Dependency update checker + Install All | 2.3 |
| Dark-themed file picker | 2.3 |
| Dark confirm / error dialogs | 2.3 |
| Scrollable Settings window | 2.3 |
| TNotebook tabs themed | 2.3 |
| VERSION file (remote version check) | 2.3 |

### 1.2 Planned but Cut

| Feature | Reason |
|---------|--------|
| ElevenLabs API | Contradicts offline-first principle — permanently excluded |
| Voice cloning (Chatterbox, OmniVoice, F5-TTS) | Requires GPU not on target hardware — removed v2.2.2 |
| Google STT | Privacy concern, online-only — removed v2.2.5 |
| Coqui XTTS-v2 | Too large, unstable on CPU |
| SSML input editor | Not yet built — v2.5 candidate |
| Electron / web port | Rejected — ~200MB Chromium, breaks Linux audio backends |
| Docker / Flatpak | Deferred — PipeWire passthrough in Docker needs design work |

### 1.3 Added That Wasn't Planned

| Feature | Version | Origin |
|---------|---------|--------|
| SmoothScroller (physics scroll) | 2.1 | User reported choppy scrolling |
| ResourceMonitor (adaptive UI) | 2.2 | UI lag during synthesis |
| PillToggle widget | 2.2 | Replaced plain checkbox buttons |
| WaveformExportBtn | 2.2 | Canvas-based export cards |
| Virtual file dialog | 1.5 | Tkinter filedialog can't be themed |
| C extension audio_fast.c | 1.5 | Python WAV concat too slow |
| `_dark_confirm` / `_dark_error` | 2.3 | System dialogs broke the theme |
| `_pick_plugin_file` dark picker | 2.3 | System filedialog broke the theme |

---

## 2. Active Bugs (v2.3.0)

| # | Bug | Impact | Notes |
|---|-----|--------|-------|
| 1 | Voice dropdown not refreshed after engine install | High | Needs restart to see new voices |
| 2 | Highlight resume off-by-one on edited documents | Medium | Falls back to position 0 silently |
| 3 | VERSION file not on GitHub | High | Push it — update checker needs it |
| 4 | No automated tests | Medium | Any refactor risks silent regression |

---

## 3. Roadmap

### v2.4.0 — Voice Polish
**Goal:** Fix the voice dropdown bug. Add voice preview.

- [ ] Voice dropdown refresh after install — call `_load_voices(preserve_selection=True)` from `VoiceLibraryWindow._on_install_complete()`
- [ ] Voice preview button — small ▶ next to dropdown, synthesises "Hello, this is a voice preview."
- [ ] Voice renaming — friendly display names stored in `~/.ttsvoices/voice_aliases.json`
- [ ] Fix highlight resume off-by-one (#2)
- [ ] Push VERSION file to GitHub (#3)

---

### v2.5.0 — Structure
**Goal:** Split ttsvoices.py (6,120 lines). Do this AFTER v2.4 bugs are fixed.

Proposed split:
```
ttsvoices.py       ~2,200 lines   Main window, event wiring, entry point
app_dialogs.py     ~1,400 lines   Settings, Theme, Update, Export dialogs
highlight.py         ~600 lines   SmoothScroller, highlight sync, word timing
update_checker.py    ~350 lines   _check_for_update_bg, _show_update_dialog, dep check
plugin_manager.py    ~400 lines   _load_plugins, _open_plugins_manager, plugin API
```

Extract order:
1. `update_checker.py` — fewest cross-dependencies, added last so cleanest seams
2. `plugin_manager.py` — self-contained, well-defined interface
3. `highlight.py` — well-defined interface (root, textarea, cfg)
4. `app_dialogs.py` — mostly self-contained Toplevels
5. What remains in `ttsvoices.py` is the main class + entry point

---

### v2.6.0 — Desktop Integration
**Goal:** Make the app available from the desktop without launching a terminal.

- [ ] Nautilus/Nemo right-click — `.desktop` service menu entry via `install.sh`; right-click PDF/DOCX → "Read with TTS Voices"
- [ ] `ttsvoices.desktop` — proper application launcher entry (GNOME, KDE)
- [ ] Clipboard watcher — optional daemon: when clipboard text exceeds N chars, auto-reads. Toggle in Settings.
- [ ] D-Bus interface — expose Play/Pause/Stop for keyboard daemon integration (sxhkd, ydotool)

---

### v3.0.0 — Plugin Ecosystem
**Goal:** First-party plugins ship with the app. Community plugin format stabilised.

Planned first-party plugins:
- `ssml_editor.py` — panel for inserting SSML pause/emphasis/prosody tags
- `reading_stats.py` — words/min, estimated time, progress percentage
- `sentence_highlight.py` — alternative to word-level: highlights full sentence
- `bookmarks_panel.py` — sidebar showing all saved bookmarks across all files

Plugin registry format (future):
```json
{
  "name": "Word Counter",
  "author": "...",
  "version": "1.0",
  "entry": "word_counter.py",
  "api_version": "2.3"
}
```

---

### v3.1.0 — Browser Extension
**Goal:** "Read selected text from any webpage."

Architecture:
```
Browser extension (Chrome/Firefox)
    → right-click selected text → "Read with TTS Voices"
    → POST localhost:7823  (optional local server mode)
    → TTSVoicesApp receives text → starts speaking
```

- Server binds `127.0.0.1` only — not accessible from network
- Optional mode: `ttsvoices --server` or toggle in Settings
- Extension shows error if app isn't running
- No browser engine required — pure HTTP between extension and app

---

## 4. Design Constraints (Non-Negotiable)

| Constraint | Reason |
|------------|--------|
| **Offline-first** | No internet required for core function. Update/dep checker uses network only with user consent. |
| **No Electron** | ~200MB Chromium, broken Linux audio, JS rewrite. Rejected permanently. |
| **No paid APIs in core path** | ElevenLabs, Google TTS, AWS Polly permanently excluded from synthesis pipeline. |
| **Linux native** | Targets Kali/Ubuntu/Debian. Cross-platform is v3.x, not current. |
| **Python/Tkinter** | Qt and GTK add large dependencies. Tkinter is stdlib. |
| **Venv pip only** | All package operations use `sys.prefix + "/bin/pip"` — never system pip. |
| **os._exit(0) on close** | Non-negotiable until Vosk C++ threads are joinable. Do not change to sys.exit(). |
| **All dialogs themed** | No system dialogs. Use `_dark_confirm`, `_dark_error`, `_pick_plugin_file`. |

---

## 5. Release Process

```
1. Make changes in working directory
2. Bump __version__ in ttsvoices.py  (e.g. "2.4.0")
3. Bump VERSION file to match
4. Add CHANGELOG.md entry at the top
5. Test: python3 ttsvoices.py
6. Run: python3 -c "import ast; ast.parse(open('ttsvoices.py').read()); print('OK')"
7. git add -A
8. git commit -m "v2.4.0 — description"
9. git push origin main
   → VERSION file on GitHub now updated
   → existing installs detect update on next startup (within 1.5s)
```

**Pre-push checklist:**
- [ ] `VERSION` and `__version__` match
- [ ] CHANGELOG has entry at top dated today
- [ ] `dep_installer.py` STAMP updated if deps changed
- [ ] DEVELOPER_CODE_REVIEW.md Session Log updated
- [ ] DEVELOPMENT_PLAN.md roadmap updated
- [ ] `update.sh` updated if new files were added
- [ ] `python3 -c "import ast; ast.parse(...)"` passes

---

## 6. Files Not to Touch Without Reading DEVELOPER_CODE_REVIEW.md First

| File / Location | Risk |
|-----------------|------|
| `_apply_theme` explicit `set_colors()` calls | Hash collision — looks redundant, is not |
| `_on_close` `os._exit(0)` | Do not change to sys.exit() |
| Settings `_toplevel` vs `win` pattern | Changing breaks Save (black screen) |
| `voices.py` `_is_mostly_ascii()` | Removing breaks non-English documents silently |
| `_style_ttk()` TNotebook section | Removing makes Voice Library tabs white |
| `dep_installer.py` STAMP string | Must match version number exactly |
| `audio_handler.py` `_LATENCY_COMP` | 150ms is empirical — do not zero it |

---

## 7. AI Context Markers (Qwen 3.7 suggestion — worth implementing)

For a 6,120-line monolith, add structured comments so any AI assistant can navigate
the file without reading every line:

```python
# <AI_CONTEXT: START plugin_system>
#   Handles: plugin loading, plugin API, Plugin Manager window, file picker
#   Key methods: _load_plugins, add_nav_button, on_speak_start, _open_plugins_manager
#   Entry: _finish_init() calls _load_plugins() after engines ready
# </AI_CONTEXT>
```

Suggested blocks:
- `plugin_system` (lines ~5350–5720)
- `update_checker` (lines ~5380–5670)
- `highlight_sync` (lines ~3800–3870 in voices.py)
- `theme_system` (lines ~4700–4800)
- `settings_window` (lines ~4909–5170)
- `file_dialog` (lines ~1500–1650)

Low effort. High payoff for future AI-assisted sessions.

---

## 8. What the AI Audit Found (May 2026)

11 AI systems analysed this codebase. Summary:

**Most accurate:** Claude (read repo directly), Kimi, Gemini/GML, Ernie
**Best product vision:** DeepSeek (v3.0 roadmap)
**Best code fixes:** Qwen 3.7 (AI Context Markers, State Dictionary pattern)
**Do not implement:** Mimo (described a completely different app — web/React SPA)

**Corrections from audit:**
- Kokoro voice names (Heart, Bella, Sarah, etc.) — all confirmed in README, not hallucinations
- GML's 4,447 line count — was reading the stale Module Map section of the dev doc, not wrong
- ChatGPT's ElevenLabs suggestion — contradicts offline-first constraint, do not add
- Copilot's "emotion controls" — not supported by Kokoro ONNX

**One idea worth keeping:** Qwen 3.7's AI Context Markers (see Section 7 above).
