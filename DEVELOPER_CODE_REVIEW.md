# TTS Voices 2.3.0 — Developer Code Review & Architecture Guide

**Prepared by:** Claude Sonnet 4.6 (Anthropic)
**Status:** Living document — updated each session
**Last updated:** May 2026

---

## 1. Project Overview

TTS Voices is a fully offline Linux desktop text-to-speech application.

**Stack:** Python 3.10+, Tkinter, ONNX Runtime, C extension, bash installer
**Platform:** Linux only (Kali, Ubuntu, Debian). Audio backend probes pw-play → aplay → paplay → ffplay.
**Target hardware:** i7-1065G7, Intel Iris Plus, 8GB RAM (informs every performance decision)

**Core capabilities:**
- Read text aloud: Kokoro ONNX → Piper TTS → espeak-ng fallback chain
- Load: PDF, DOCX, ODT (AES-256-GCM encrypted), EPUB, HTML, RTF, CSV, TXT
- Word-level highlight sync (real-time timing estimation)
- WAV / MP3 export with C-accelerated concat
- Audio-to-Text transcription (faster-whisper)
- 12+ named themes including AMOLED
- Bookmark/resume for long documents
- Voice Library (download/manage engine models)
- Plugin system (drop .py files into ~/.ttsvoices/plugins/)
- Auto-update checker (urllib, stdlib, 1.5s timeout)
- Single-instance enforcement via abstract Unix socket

---

## 2. Module Map

```
TTSVoices_v2.3.0/
├── ttsvoices.py           6,120 lines  Main app + all GUI + plugins + update
├── voices.py              1,217 lines  TTS engine abstraction layer
├── voice_library.py       1,210 lines  Voice Library dialog
├── audio_handler.py         465 lines  Playback, export, C-extension bridge
├── file_extractor.py        985 lines  Multi-format document extraction
├── bug_tracker.py           741 lines  Structured logging (ring buffer + file)
├── odf_crypto.py            336 lines  AES-256-GCM ODT/ODS decryption
├── save_point_manager.py     91 lines  Bookmark persistence (JSON per file hash)
├── exceptions.py             54 lines  Custom exception hierarchy
├── dep_installer.py         275 lines  First-run dependency check/install UI
├── build_audio_fast.py       47 lines  gcc driver for audio_fast.so
├── audio_fast.c             163 lines  C: WAV concat + PCM volume scaling
├── audio_fast.so                       Compiled x86_64 Linux shared library
├── install.sh               279 lines  Bash installer (venv, apt, pip, launcher)
├── update.sh                 80 lines  Update script (backs up, copies, recompiles)
├── requirements.txt          48 lines  Pip dependencies
├── VERSION                             Single-line version string: "2.3.0"
├── CHANGELOG.md                        Release history (newest first)
├── DEVELOPER_CODE_REVIEW.md           This file
└── DEVELOPMENT_PLAN.md                Roadmap and constraints
```

**example_plugins/**
```
├── word_counter.py   Nav button showing live word/char count
└── speak_log.py      Timestamped start/stop log to ~/.ttsvoices/speak_log.txt
```

---

## 3. Startup Sequence

```
main()
  ├─ _ensure_single_instance()      Abstract Unix socket \0ttsvoices_instance_lock
  ├─ sys.excepthook = handler        Catches all unhandled exceptions → bug_tracker
  ├─ load_config()                   ~/.ttsvoices/config.json (creates on first run)
  └─ TTSVoicesApp(cfg)
       ├─ __init__()                 State vars, Events, StringVars — no heavy imports
       ├─ _build_ui()                Window visible at ~200ms
       └─ root.after(50, _post_map_init)
            ├─ _style_ttk()          TProgressbar, TCombobox, TNotebook themed
            └─ daemon thread: _load_engines_background()
                 ├─ imports: bug_tracker, voices, audio_handler, file_extractor
                 ├─ compiles audio_fast.so if missing
                 └─ _finish_init() when ready:
                      ├─ load voices, set provider, show bookmark indicator
                      ├─ _load_plugins()               ← scan ~/.ttsvoices/plugins/
                      └─ root.after(800, _check_for_update_bg)  ← if auto_update_check
```

**Key design:** Window draws before any engine loads. User sees UI in ~200ms.
`os._exit(0)` on close — kills daemon threads and audio subprocesses hard. Intentional.

---

## 4. New in v2.3.0

### 4.1 Plugin System

```
~/.ttsvoices/plugins/
    my_plugin.py   → imported at startup → register(app) called
    another.py     → imported, no register() → loaded passively
```

**Plugin API** (available inside `register(app)`):

| Method | Effect |
|--------|--------|
| `app.add_nav_button(label, callback, accent=False)` | Adds button to header nav |
| `app.on_speak_start(callback)` | callback() fired when speech starts |
| `app.on_speak_stop(callback)` | callback() fired when speech stops |
| `app.get_current_text() → str` | Returns full textarea content |
| `app.set_status(text, color="")` | Sets status pill |

**Plugin Manager** (`⊕ Plugins` nav button):
- Card list: green strip = active, red strip = load error
- `+ Add Plugin` opens `_pick_plugin_file()` dark-themed picker
- Remove with `_dark_confirm()` dialog
- "Reload All" re-scans without restart
- Hot-reload: install triggers `_load_plugins()` immediately

**Error isolation:** Each plugin import is wrapped in try/except. One broken plugin never stops others or crashes the app. Errors logged to bug_tracker.

### 4.2 Auto-Update Checker

```
_finish_init()
    └─ root.after(800, _check_for_update_bg)  if auto_update_check=True
           └─ daemon thread:
                urllib.request.urlopen(VERSION_URL, timeout=1.5)
                    ├─ status != 200  → return silently (404 = not pushed yet)
                    ├─ not \d+\.\d+   → return silently (HTML error page guard)
                    ├─ latest != __version__ → _show_update_available(latest)
                    └─ latest == __version__ → _show_update_current()
```

**Why urllib not requests:** stdlib, zero import overhead, timeout covers full operation including DNS. requests timeout doesn't cover DNS resolution which was causing hangs.

**Auto-check:** Completely silent. Button only changes when result arrives.
**Manual check:** Click `⟳ Updates` → shows "Checking…" → updates on result.

**Update button states:**

| State | Text | Colour | Border |
|-------|------|--------|--------|
| Idle | `⟳ Updates` | muted | none |
| Checking (manual only) | `⟳ Checking…` | muted | none |
| Up to date (manual) | `⟳ Up to date` | muted | resets 4s |
| **Update available** | `⬆ Update now (x.y.z)` | amber | amber pulse |

**VERSION file:** Push `VERSION` (containing `2.3.0`) to the repo root. The app reads it remotely. To release: bump `__version__` in ttsvoices.py, bump VERSION file, push both.

### 4.3 Dependency Checker

Settings → Updates section → "Check now":
- Runs `pip list --outdated --format=json` using `sys.prefix + "/bin/pip"` (venv pip only)
- Lists outdated packages inline
- "Install All" button appears → runs `pip install --upgrade pkg1 pkg2…` in background
- All UI updates guarded with `winfo_exists()` — safe if Settings closed mid-check

### 4.4 Themed Dialogs

All system dialogs replaced with themed versions:

| Old | New | Notes |
|-----|-----|-------|
| `messagebox.askyesno` | `_dark_confirm(parent, title, msg)` | Enter=confirm, Escape=cancel |
| `messagebox.showerror` | `_dark_error(parent, title, msg)` | Matches active theme |
| `filedialog.askopenfilename` | `_pick_plugin_file(parent)` | Full dark file browser |

`_pick_plugin_file` features: sidebar shortcuts (Home, Downloads, Documents, Plugins), editable path bar, shows only folders + .py files, Open/Cancel buttons.

---

## 5. Theme System

### 5.1 Structure

```python
THEMES: dict[str, dict[str, str]]   # 12+ named palettes
C: dict[str, str]                    # live palette, updated in-place by _apply_theme()
```

All widget creation reads from `C`. Theme switch calls `C.update(THEMES[key])` then walks widget tree via `_recolor()`.

### 5.2 TNotebook Theming (v2.3.0)

Voice Library uses `ttk.Notebook`. Styled in `_style_ttk()`:
```python
s.configure("TNotebook", background=C["bg"], bordercolor=C["border"])
s.configure("TNotebook.Tab", background=C["surface"], foreground=C["text2"], ...)
s.map("TNotebook.Tab",
      background=[("selected", C["surface2"]), ("active", C["border"])],
      foreground=[("selected", C["accent2"]), ("active", C["text"])])
```
`_style_ttk()` is called on startup AND on every theme switch — tabs always match the active theme.

### 5.3 Theme Application: Critical Rules

- `_recolor()` walks the widget tree using a hex→slot reverse map
- GlowButton `_nbg`/`_hbg` are Python instance attrs (not Tkinter config) — must be updated explicitly via `set_colors()`
- Hash collision: some themes share hex values between slots → explicit `set_colors()` calls are load-bearing, do not remove them
- Update button: theme switch preserves amber glow if update is pending

---

## 6. Settings Window

Settings is a scrollable Toplevel:
```
_toplevel (tk.Toplevel)          ← destroyed by _save() / X button
    ├─ "⚙ Settings" header label  ← fixed, not scrollable
    └─ _scroll_outer (Frame)
         ├─ _sb (Scrollbar)
         └─ _sc (Canvas)
              └─ win_inner (Frame)  ← all content packs here
                   ├─ frm   Config paths
                   ├─ frm2  Chunk size slider
                   ├─ frm3  Highlight sync slider
                   ├─ frm4  Updates (auto-check toggle + dep checker)
                   └─ plg_outer  Plugins status (read-only, points to ⊕ Plugins)
    └─ GlowButton "Save"  ← packs onto _toplevel, not win_inner
```

**Critical:** `win = win_inner` rebinds the local variable so all `frm.pack()` calls target the scrollable frame. But `GlowButton` and `_save()` must reference `_toplevel` (saved before the rebind) — otherwise Save destroys only the inner frame, leaving a black empty Toplevel.

---

## 7. Known Remaining Issues

| # | Issue | File | Priority |
|---|-------|------|----------|
| 1 | Voice dropdown not refreshed after engine install | ttsvoices.py | High |
| 2 | Highlight resume off-by-one on edited docs | ttsvoices.py | Medium |
| 3 | VERSION file not yet on GitHub | — | High (push it) |
| 4 | ttsvoices.py is 6,120 lines — needs splitting | ttsvoices.py | Medium |
| 5 | No automated tests | all | Medium |

---

## 8. Session Log

| Session | Change |
|---------|--------|
| 1–10 | See v2.2.x entries in CHANGELOG |
| 11a | Auto-update checker (urllib, 1.5s timeout, silent auto) |
| 11a | Glowing update button + "Update now" text + pulse animation |
| 11a | Settings: auto_update_check toggle + dep checker |
| 11a | `_on_update_toggle` method |
| 11a | `_load_plugins()` + full plugin system |
| 11a | Plugin API: add_nav_button, on_speak_start, on_speak_stop, get_current_text, set_status |
| 11a | `⊕ Plugins` nav button + `_open_plugins_manager` window |
| 11a | `_pick_plugin_file()` dark-themed file picker |
| 11a | `_dark_confirm()` + `_dark_error()` themed dialogs |
| 11a | Settings window made scrollable (`win = win_inner` pattern) |
| 11a | Settings plugins section simplified to read-only status |
| 11a | TNotebook tabs themed in `_style_ttk()` |
| 11a | `_install_updates()` — Install All button for dep checker |
| 11b | Fixed: `_on_update_toggle` not found crash (method was not injected) |
| 11b | Fixed: Settings Save blackout (`_toplevel` vs `win` destroy) |
| 11b | Fixed: white system file dialog → `_pick_plugin_file()` |
| 11b | Fixed: dep checker crash after window close (`winfo_exists()` guard) |
| 11b | Fixed: 404 response treated as version string (status + regex check) |
| 11b | Fixed: update button glowing on 404 — now silent until real update |
| 11b | Fixed: `requests` replaced with `urllib` (faster, no DNS hang) |
| 11b | Fixed: auto-check completely silent (no "Checking…" text) |
| 11b | Fixed: Settings plugins section had Add/Remove buttons (removed) |
| 11b | Fixed: Remove plugin used `messagebox.askyesno` (now `_dark_confirm`) |

---

## 9. Files Not to Touch Without Reading This

| File / Function | Why sensitive |
|-----------------|---------------|
| `_apply_theme` explicit `set_colors()` calls | Hash collision — not redundant |
| `_on_close` `os._exit(0)` | Intentional — kills Vosk C++ threads |
| `voices.py` `_preprocess_for_kokoro` `_is_mostly_ascii()` | Prevents silent non-English fallback |
| `audio_handler.py` `_LATENCY_COMP` 150ms | Empirical for PipeWire — don't set to 0 |
| Settings `_toplevel` vs `win` | See Section 6 — critical pattern |
| `_style_ttk()` TNotebook section | Must stay or Voice Library tabs go white |
| `dep_installer.py` STAMP | Must match version — triggers re-check on upgrade |
