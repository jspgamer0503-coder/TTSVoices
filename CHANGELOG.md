# TTS Voices — Changelog

## [2.3.0] — 2026-05-23

### New Features

**Auto-Update Checker**
- Checks `VERSION` file on GitHub at startup (800ms delay, 1.5s timeout, urllib stdlib — no requests dependency)
- Glowing amber `⬆ Update now (x.y.z)` button appears in header when update is available
- Pulsing border animation on update button
- Update dialog: Run update.sh / View on GitHub / Later
- Auto-check is completely silent — button only changes when result arrives
- `⟳ Checking…` text shown only on manual check (click the button)
- Settings toggle: "Auto-check for updates on startup" (saved to config.json)
- Manual check: click `⟳ Updates` in header nav bar

**Dependency Update Checker**
- Settings → "Check now" lists outdated pip packages using venv pip only (never system pip)
- "Install All" button appears after check finds outdated packages
- Runs `pip install --upgrade ...` in background thread, shows live status
- Window-close safety: all callbacks guard with `winfo_exists()` before touching widgets

**Plugin System**
- Scans `~/.ttsvoices/plugins/` at startup, imports each `.py` file
- Calls `register(app)` if present — errors caught and logged, never crash the app
- Plugin API: `add_nav_button()`, `on_speak_start()`, `on_speak_stop()`, `get_current_text()`, `set_status()`
- Hot-reload: installing a plugin via the manager reloads without restart
- Two example plugins included: `word_counter.py`, `speak_log.py`

**Plugin Manager Window** (`⊕ Plugins` in nav bar)
- Full-screen plugin manager: install, remove, reload
- Card-style list with green/red status strip per plugin
- `+ Add Plugin` button opens dark-themed file picker (no system dialog)
- Remove button with themed confirmation dialog
- "Reload All" button re-scans plugins folder
- Clickable plugins folder path (opens in file manager)

**Themed Dialogs**
- `_dark_confirm()` — replaces `messagebox.askyesno` everywhere
- `_dark_error()` — replaces `messagebox.showerror` everywhere
- `_pick_plugin_file()` — dark-themed file picker with sidebar shortcuts, replaces system `filedialog`
- All dialogs follow the active theme

**Settings Window**
- Now scrollable (canvas + scrollbar + mousewheel)
- Plugins section shows read-only status summary ("N plugins active · ✓ all loaded")
- Points to `⊕ Plugins` nav button for management

**Voice Library Tabs**
- `TNotebook.Tab` fully themed via `ttk.Style` — no more white/grey system-default tabs
- Selected tab uses `accent2` foreground, `surface2` background
- Matches active theme on every theme switch

### Bug Fixes
- **Settings Save blackout** — `win.destroy()` was destroying the inner scrollable frame instead of the Toplevel. Fixed by saving `_toplevel` before `win = win_inner` rebind.
- **Dep checker crash** — `dep_status_lbl.configure()` called after Settings window closed. Fixed with `winfo_exists()` guard.
- **404 glow bug** — urllib returning "404: Not Found" HTML was treated as a version string, triggering amber glow. Fixed: check `resp.status == 200` and regex-validate version format (`^\d+\.\d+`).
- **`_on_update_toggle` missing** — method referenced in `_build_right` but injection failed silently. Now properly defined after `_on_as_toggle`.
- **CHANGELOG order** — v2.2.4 was at bottom of file (after v2.1.0). All entries now newest-first. v2.2.5 entry added (was only in DEVELOPER_CODE_REVIEW.md).

### Technical
- `PLUGINS_DIR = CONFIG_DIR / "plugins"` constant added
- `VERSION` file added to repo root — single source of truth for remote version check
- Update check uses `urllib.request` (stdlib) not `requests` — faster, no import overhead
- `dep_installer.py` stamp updated to `.deps_ok_2.3.0`
- `ttsvoices.py`: 5,151 → 6,120 lines (+969 lines for all new features)

---

## [2.2.5] — 2026-04-09

### Bug Fixes
- Fixed crash: export status TypeError on some file names
- Export bar no longer glows during speech synthesis
- File search: selected file stays visible on typo
- Folders always visible while searching in file dialog
- File dialog: no more blank gap at top on first open
- Bug log Refresh button now correctly reloads entries
- Theme picker: Cancel button removed (was a no-op)
- Voice Library: ℹ info button removed from model rows
- Save point: shows 'Saved point cleared' immediately in UI
- Speed/pitch: restarts faster (200ms debounce, was 800ms)

---

## [2.2.4] — 2026-04-09

### Bug Fixes

**Scroll / Layout**
- Fixed app scrolling by itself without user input
- Fixed "parent directory keeps moving" in file dialog
- Fixed placeholder FocusOut causing scroll jump

**Export**
- WAV/MP3 export progress bars now show real chunk-by-chunk progress
- Export temp WAV file leak fixed (NamedTemporaryFile + try/finally)
- Export status label shows "Export ready" at idle

**WAV/MP3 Button Hover Flash**
- WaveformExportBtn snapshots theme palette at draw time — no more theme-flash

**Voice Library Scroll**
- Mousewheel works over all engine card children

**Audio-to-Text**
- Transcript insert forces view to top on long transcripts

**Bug Tracker Window**
- Rebuilt with scrollbar, Refresh button, mousewheel, bottom-check

### Removed
- Voice cloning engines (Chatterbox, OmniVoice) fully excluded

---

## [2.2.3] — 2026-04-08

### Bug Fixes
- Cannot erase typed text — `_remove_placeholder` reads actual content before deleting
- WAV/MP3 hover color flash — `_redraw` calls `configure(bg=bg)` before `delete("all")`
- Export progress bar not syncing — mirror traces added in `_finish_init`
- App scrolls without user input — scroll lock extended to 10s

---

## [2.2.2] — 2026-04-08

### Bug Fixes
- AMOLED theme — pure `#000000` backgrounds
- File dialog parent directory jumping fixed
- Voice cloning removed (Chatterbox, OmniVoice, F5-TTS)
- Voice Library scroll fixed
- Kinetic scroll disabled

### New Features
- AMOLED theme (pure black for OLED displays)
- Improved export section layout

---

## [2.1.0] — 2026-03-29

### Bug Fixes
- Stop on first click
- Highlight sync timing
- Voice Library `_kokoro_singleton.clear()` fix
- Tkinter crash: 8-digit hex colours

### New Features
- Export progress bar and verification
- AMD GPU / ROCm support
- TensorRT support
- Yellow and Golden themes
- `exceptions.py` typed exception hierarchy
