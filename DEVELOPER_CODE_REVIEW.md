# TTS Voices 2.5.1 — Developer Code Review & Architecture Guide

**Originally prepared by:** Claude Sonnet 4.6 (Anthropic) — v2.3.1 baseline
**Current version:** 2.5.1
**Last updated:** June 2026
**Maintained by:** opencode AI assistant — see README.md "Development & Maintenance"

> Note: The architecture described below applies to the v2.3.1 baseline.
> The current 2.5.1 release adds the missing hover color key fix,
> updates kokoro-onnx to 0.5.0, and improves overall stability.
> See `CHANGELOG.md` for the full diff.

---

## 1. Module Map

```
TTSVoices_v2.3.0/
├── ttsvoices.py           6,122 lines  Main app + GUI + plugins + update checker
├── voices.py              1,218 lines  TTS engine abstraction (Kokoro/Piper/espeak)
├── voice_library.py       1,210 lines  Voice Library dialog (download/manage)
├── audio_handler.py         465 lines  Playback, export, C-extension bridge
├── file_extractor.py        985 lines  PDF/DOCX/EPUB/ODT/HTML/RTF/CSV/TXT extraction
├── bug_tracker.py           742 lines  Structured logging (ring buffer + session files)
├── odf_crypto.py            336 lines  AES-256-GCM + legacy CBC ODT decryption
├── save_point_manager.py     91 lines  Bookmark persistence (JSON per file MD5 hash)
├── exceptions.py             54 lines  Typed exception hierarchy
├── dep_installer.py         275 lines  First-run dependency check/install UI
├── build_audio_fast.py       47 lines  gcc driver for audio_fast.so
├── audio_fast.c             163 lines  C: WAV concat + PCM volume scaling
├── audio_fast.so                       Compiled x86_64 Linux shared library
├── install.sh               279 lines  Bash installer
├── update.sh                 80 lines  Update script
├── requirements.txt                    Pip dependencies
├── VERSION                             "2.3.1" — read remotely for update checks
├── CHANGELOG.md                        Release history (newest first)
├── DEVELOPER_CODE_REVIEW.md           This file
└── DEVELOPMENT_PLAN.md                Roadmap

example_plugins/
├── word_counter.py           Live word/char count nav button
└── speak_log.py              Start/stop log to ~/.ttsvoices/speak_log.txt
```

---

## 2. Startup Sequence

```
main()
  ├─ os.setpgrp()  ← optional: call here if terminal kill is still a concern
  ├─ _ensure_single_instance()   Abstract Unix socket \0ttsvoices_instance_lock
  ├─ sys.excepthook = handler    All unhandled exceptions → bug_tracker
  ├─ load_config()               ~/.ttsvoices/config.json
  └─ TTSVoicesApp(cfg)
       ├─ _build_ui()            Window visible ~200ms
       └─ root.after(50, _post_map_init)
            ├─ _style_ttk()      TProgressbar + TCombobox + TNotebook themed
            └─ daemon thread: _load_engines_background()
                 └─ _finish_init():
                      ├─ load voices, set provider, show bookmark
                      ├─ _load_plugins()         ~/.ttsvoices/plugins/
                      └─ root.after(800, _check_for_update_bg)
```

---

## 3. Key Subsystems

### 3.1 Plugin System
- Scans `PLUGINS_DIR = CONFIG_DIR / "plugins"` at startup
- Each `.py` file imported; `register(app)` called if present
- Plugin API: `add_nav_button`, `on_speak_start`, `on_speak_stop`, `get_current_text`, `set_status`
- Hot-reload: `_load_plugins()` callable at runtime from Plugin Manager
- Errors isolated per-plugin — one broken plugin never crashes others

### 3.2 Auto-Update Checker
- Uses `urllib.request` (stdlib) — not `requests` (DNS hang bug, removed in v2.3.0)
- `timeout=1.5` covers full operation including DNS and TLS
- Checks `resp.status == 200` and validates `^\d+\.\d+` before acting
- Auto-check: completely silent until result (no "Checking…" text)
- Manual: click `⟳ Updates` nav button → shows "Checking…" → updates on result
- Update button glows amber with pulse animation when update confirmed

### 3.3 Theme System
- `THEMES` dict → `C` dict updated in-place by `_apply_theme()`
- `_recolor()` walks widget tree using hex→slot reverse map
- **Hash collision fix:** explicit `set_colors()` calls on named buttons are load-bearing — do not remove
- `_style_ttk()` called on startup and every theme switch — covers TNotebook tabs
- Update button: theme switch preserves amber glow if update is pending

### 3.4 Settings Window (Scrollable)
```
_toplevel (tk.Toplevel)          ← _save() and X button destroy this
    ├─ Header label (fixed)
    └─ _scroll_outer → _sc (Canvas) → win_inner (Frame)
         ├─ Config paths
         ├─ Chunk size slider
         ├─ Highlight sync slider
         ├─ Updates (auto-check toggle + dep checker + Install All)
         └─ Plugins (read-only status — manage via ⊕ Plugins nav button)
    └─ GlowButton "Save" → packs onto _toplevel
```
**Critical:** `win = win_inner` rebinds the local variable. `_save()` and `GlowButton` must reference `_toplevel` (saved before the rebind) — otherwise only the inner frame is destroyed, leaving a black empty Toplevel.

### 3.5 Synthesis Pipeline (voices.py)
- Engine hierarchy: `Kokoro ONNX → Piper TTS → espeak-ng`
- `SENTENCE_SPLIT_PATTERN`: uses `\s*` (not `\s+`) — works for CJK text without spaces
- `estimate_phonemes()`: detects low ASCII ratio (<50%) → uses `len(text) * 2.5` directly
- `_count_syllables()`: returns `len(word)` for non-Latin chars (each CJK char ≈ 1 syllable)
- `check_espeak()`: uses `shutil.which()` — no `which` binary dependency
- `_synth_espeak()`: text via `stdin` + `--stdin` flag — no CLI arg injection, no ARG_MAX

### 3.6 Audio Playback (audio_handler.py)
- Backend probe: `pw-play → aplay → paplay → ffplay`
- C extension (`audio_fast.so`): WAV concat via `concat_wavs()`, volume via `apply_volume()`
- Falls back to pure Python if `.so` missing
- `_LATENCY_COMP` default 150ms — empirical for PipeWire, configurable 0–500ms in Settings

---

## 4. Known Issues

| # | Issue | File | Priority |
|---|-------|------|----------|
| 1 | `ttsvoices.py` is 6,869 lines — monolithic structure, planned split deferred | ttsvoices.py | Medium |
| 2 | No unit/integration test suite — `health_check.py` provides 65 static checks but not real unit tests | all | Low |

---

## 5. Session Log

| Session | Version | Change |
|---------|---------|--------|
| 1–10 | 2.2.x | See CHANGELOG |
| 11a | 2.3.0 | Plugin system, update checker, dark dialogs, scrollable settings |
| 11b | 2.3.0 | Fix: Save blackout, 404 glow, dep checker crash, requests→urllib |
| **12** | **2.3.1** | **Fix: bug_tracker NameError (P0)** |
| **12** | **2.3.1** | **Fix: highlight_word tautology infinite loop (P0)** |
| **12** | **2.3.1** | **Fix: CJK sentence splitting — `\s*` not `\s+` (P0)** |
| **12** | **2.3.1** | **Fix: CJK syllable estimator — character count fallback (P0)** |
| **12** | **2.3.1** | **Fix: killpg kills user terminal — removed (P0)** |
| **12** | **2.3.1** | **Fix: has_sr undefined in _transcribe_worker (P0)** |
| **12** | **2.3.1** | **Fix: espeak text via stdin not CLI arg (P1)** |
| **12** | **2.3.1** | **Fix: negative geometry on small screens (P1)** |
| **12** | **2.3.1** | **Fix: `which` → `shutil.which` in voices.py (P2)** |
| **12** | **2.3.1** | **Fix: pipewire-audio → pipewire-utils in dep_installer (P2)** |

---

## 6. Files Not to Touch Without Reading This

| File / Location | Risk |
|-----------------|------|
| `_apply_theme` explicit `set_colors()` calls | Hash collision — not redundant |
| `_on_close` — no killpg | Intentional removal — killpg kills terminal |
| Settings `_toplevel` vs `win` pattern | Changing breaks Save (black screen) |
| `voices.py` `_is_mostly_ascii()` | Removing breaks non-English documents silently |
| `voices.py` `\s*` in SENTENCE_SPLIT_PATTERN | Must stay `\s*` not `\s+` for CJK |
| `_style_ttk()` TNotebook section | Removing makes Voice Library tabs white |
| `audio_handler.py` `_LATENCY_COMP` 150ms | Empirical — do not zero |
| `dep_installer.py` STAMP | Must match version number |
