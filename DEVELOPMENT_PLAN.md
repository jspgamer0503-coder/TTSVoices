# TTS Voices — Development Plan

**Current version:** 2.5.1
**Last updated:** June 2026
**Maintained by:** opencode AI assistant — see README.md "Development & Maintenance"

---

## 1. Version History Summary

| Version | Date | Type | Key Change |
|---------|------|------|------------|
| 2.1.0 | 2026-03-29 | Feature | GPU support, export progress, new themes |
| 2.2.2 | 2026-04-08 | Fix + Feature | AMOLED theme, remove voice cloning |
| 2.2.3 | 2026-04-08 | Fix | Placeholder erase, hover flash, scroll |
| 2.2.4 | 2026-04-09 | Fix | Scroll bugs, export progress, bug tracker |
| 2.2.5 | 2026-04-09 | Fix | Export crash, search bugs, speed debounce |
| 2.3.0 | 2026-05-23 | Feature | Plugins, update checker, dark dialogs |
| 2.3.1 | 2026-05-24 | Fix | 10 critical/high bugs from static analysis |
| **2.4.0** | **2026-05-25** | **Feature + Security** | **Voice polish, security hardening, resume** |

---

## 2. What shipped in v2.4.0

### Features
- ✅ Voice dropdown refresh after engine install (`_load_voices(preserve_selection=True)`)
- ✅ Voice preview button (▶ next to dropdown) — `_preview_voice()`
- ✅ Voice renaming / aliases — `_rename_voice()` + `voice_aliases` config dict
- ✅ Highlight resume off-by-one fix — `_find_chunk_start()` 3-tier regex fallback
- ✅ **SavePointManager fully wired** — stop saves position, load detects it, speak resumes from it
- ✅ **Hover tooltips on all buttons** — `Tooltip` class + `attach_tooltip()` helper

### Security (from external audit)
- ✅ `audio_fast.c` OOB bounds check — prevents heap OOB read on crafted WAV
- ✅ SHA-256 model verification in `voice_library.py` (hashes pending confirmation)
- ✅ `~/.ttsvoices/plugins/` locked to `0700` — prevents local privilege escalation
- ✅ PKCS7 strict padding validation in `odf_crypto.py`
- ✅ Developer hardcoded path removed from `update.sh`

### Bug fixes
- ✅ Stop-button race condition — `begin_session()` in `audio_handler.py` prevents
  `_stop_event` from being wiped mid-loop (caused repeated/unstoppable voice)
- ✅ espeak-ng data path resolution — `_find_espeak_data_dir()` finds correct path
  at import time, fixing the espeakng-loader CI-path error on all real machines

---

## 3. Active Bugs / Known Issues (v2.5.0)

| # | Issue | File | Priority |
|---|-------|------|----------|
| 1 | `ttsvoices.py` is now 6,869 lines — monolithic structure still in place | — | Medium — split deferred to a future major release |
| 2 | No unit/integration test suite | all | Low — `health_check.py` provides 65 static checks, but not real unit tests |

---

## 4. Roadmap

### v2.5.0 — Structure (split ttsvoices.py)
Split ttsvoices.py into:
```
ttsvoices.py       ~2,200 lines   Main window + entry
app_dialogs.py     ~1,400 lines   Settings, Theme, Export, Update dialogs
highlight.py         ~600 lines   SmoothScroller, highlight sync
update_checker.py    ~350 lines   Update check + dep checker
plugin_manager.py    ~400 lines   Plugin loader + Plugin Manager window
```
Extract order: update_checker → plugin_manager → highlight → app_dialogs → remainder

### v2.6.0 — Desktop Integration
- [ ] Nautilus/Nemo right-click service menu entry
- [ ] `ttsvoices.desktop` launcher file
- [ ] Clipboard watcher (optional daemon mode)
- [ ] D-Bus interface for keyboard shortcut daemons

### v3.0.0 — Plugin Ecosystem
- [ ] First-party plugins: SSML editor, reading stats, sentence highlighter
- [ ] Plugin registry format with metadata JSON
- [ ] Plugin version compatibility checks

### v3.1.0 — Browser Extension
- [ ] Chrome/Firefox extension → local HTTP server → app speaks selected text
- [ ] Server binds 127.0.0.1 only, optional mode

---

## 5. Design Constraints (Non-Negotiable)

| Constraint | Reason |
|------------|--------|
| Offline-first | No internet for core function |
| No Electron | ~200MB Chromium, broken Linux audio |
| No paid APIs in synthesis path | ElevenLabs/Google/AWS permanently excluded |
| Linux native | Cross-platform is v3.x |
| Venv pip only | Never touch system pip |
| `os._exit(0)` on close | Vosk C++ threads not joinable — do not change |
| No `killpg` | Kills user terminal — removed in v2.3.1 |
| All dialogs themed | Use `_dark_confirm`, `_dark_error`, `_pick_plugin_file` |
| `~/.ttsvoices/plugins/` is `0700` | Prevent local privilege escalation — do not relax |

---

## 6. Release Checklist

```
1. Bump __version__ in ttsvoices.py
2. Bump VERSION file
3. Update dep_installer.py STAMP if deps changed
4. Add CHANGELOG.md entry at top
5. python3 -c "import ast; ast.parse(open('ttsvoices.py').read())"
6. python3 health_check.py   (must show 65 passed, 0 failed)
7. python3 ttsvoices.py  (smoke test)
8. git add -A
9. git commit -m "v2.x.x — description"
10. git push origin main
```

---

## 7. AI Audit Summary (May 2026)

Four external static analyses across v2.3.1 → v2.4.0 found and fixed 17 real bugs.

**v2.3.1 (Qwen 3.7 + Gemini):** 10 bugs fixed — see v2.3.1 entry above.

**v2.4.0 (external security audit + follow-up review):**
- P0: `audio_fast.c` OOB read on crafted WAV chunk size
- P1: No model integrity check (supply chain / MITM risk)
- P1: Plugin dir world-writable (local privilege escalation)
- P1: Stop-button race (voice repeats despite Stop press)
- P2: Fake SHA-256 hashes break all model downloads for new users
- P2: `SavePointManager` imported but never instantiated or used
- P2: PKCS7 padding not strictly validated (padding oracle — theoretical)
- P2: Hardcoded developer path in `update.sh` (info leak)
- P3: espeak-ng data path hardcoded to CI runner path (espeakng-loader bug)

**AI Context Markers** (recommended for future sessions):
Add `# <AI_CONTEXT: START x>` / `# <AI_CONTEXT: END x>` blocks around major
subsystems in ttsvoices.py so AI assistants can navigate the 6,100+ line file
without reading every line. Suggested blocks: plugin_system, update_checker,
highlight_sync, theme_system, settings_window, file_dialog, save_point.
