# TTS Voices — Changelog

## v2.2.2 (2026-04-08)

### Bug Fixes
- **Cannot erase typed text** — `_remove_placeholder` now reads the actual textarea
  content before deciding to delete it. Previously it would blindly wipe the textarea
  whenever `_placeholder_active` was True, which `_get_text` could silently re-arm by
  backspacing to empty. `_get_text` no longer sets `_placeholder_active = True` as a
  side effect — removing the trap entirely.
- **WAV/MP3 hover color flash** — `WaveformExportBtn._redraw` now calls
  `self.configure(bg=bg)` *before* `self.delete("all")`, so the canvas widget
  background matches the drawn color from the very first frame of the redraw.
- **Export progress bar not syncing** — Added mirror traces in `_finish_init` so
  both `progress_var` (synthesis) and `_export_progress_var` (export) write into
  `_unified_progress_var`, which drives the single visible bar between the toggles.
  The bar now reflects whichever operation is active in real time.
- **App scrolls without user input** — Scroll lock extended 4 s → 10 s. Added a
  `<KeyPress>` stamp on the textarea so editing also holds the scroll position
  against the highlight-sync auto-scroll during playback.

## v2.2.2 (2026-04-08)

### Bug Fixes
- **AMOLED theme** — replaced `dark` with pure `#000000` backgrounds.
- **File dialog parent directory jumping** — `_update_scrollregion` no longer resets
  scroll on canvas resize; only resets on directory navigation.
- **WAV/MP3 hover flash (partial)** — `_apply_theme` now calls `_redraw()` on export
  buttons after every theme switch so drawn rectangles update immediately.
- **Voice cloning removed** — Chatterbox, OmniVoice, F5-TTS removed from dropdown
  and Voice Library (require GPU not available on this system).
- **Voice Library scroll** — mouse wheel now works over all engine card children.
- **Kinetic scroll disabled** — `KINETIC_THRESHOLD = 9999` stops coasting immediately.

### Bug Fixes
- **Auto-scroll** — App no longer scrolls by itself. Fixed: placeholder re-insertion
  now forces `yview_moveto(0.0)`. SmoothScroller kinetic friction reduced (0.88→0.78)
  and threshold raised (0.5→1.0) so coasting stops much faster.
- **File dialog parent directory jumps** — `_update_scrollregion` no longer resets
  scroll to top on canvas resize; only resets when actually navigating directories.
- **WAV/MP3 hover color flash** — WaveformExportBtn now sets canvas `configure(bg=...)`
  in `_redraw` to prevent the old background color flashing through on hover/theme switch.
- **Voice Library scroll** — Mouse wheel now works over engine cards; scroll events are
  propagated from all child widgets to the scrollable canvas.
- **Export progress bar** — The bar between Auto-Scroll and EXPORT now shows synthesis
  AND export progress (dual-purpose). Export section brought closer to toggles.
- **Voice cloning engines** — Chatterbox, OmniVoice, F5-TTS removed. These models
  require GPU hardware and did not produce accurate voice clones on CPU. Engine list
  now shows only Kokoro ONNX and espeak-ng.

### New Features
- **AMOLED theme** — Replaces "Dark" with a true AMOLED theme (pure `#000000`
  backgrounds) for OLED display power savings and maximum contrast.
- **Improved export section** — Tighter layout, prominent labeled progress bar
  showing live export percentage and status.

## v2.1.0 (2026-03-29)

### Bug Fixes
- **Stop on first click** — voice now stops immediately on first click. Previously
  the stop flag was cleared inside the audio loop, requiring two clicks.
- **Highlight sync** — word highlights now sync to actual audio playback time via
  a 20ms real-time polling loop triggered by the audio start callback.
- **Voice Library** — fixed `_kokoro_cache` → `_kokoro_singleton.clear()` so newly
  downloaded models are picked up without restarting the app.
- **Tkinter crash** — fixed 8-digit hex colours (`#ffa50033`) that crashed on startup.

### New Features
- **Export progress bar** — progress bar and status label in the EXPORT panel
  showing time remaining, file size, and duration after save.
- **Export verification** — WAV exports verified by frame count; MP3 by file size.
  Distinct dialogs for success, empty file, and failure.
- **AMD GPU support** — ROCm execution provider added to GPU priority chain.
- **TensorRT support** — NVIDIA TensorRT provider added.
- **GPU button labels** — shows exact hardware: NVIDIA GPU / AMD GPU / Intel iGPU.
- **Yellow theme** — pure black background with electric yellow accents.
- **Golden theme** — pure black background making gold colours pop.
- **exceptions.py** — typed exception hierarchy for cleaner error handling.

### Removed Dependencies
- `soundfile` — unused; playback uses system `aplay`/`paplay`/`ffplay`
- `urllib3` — pulled in transitively by `requests`
- `charset-normalizer` — same; `chardet` used directly
- `odfpy` — XML fallback handles ODT without it

## [2.2.4] — 2026-04-09

### Bug Fixes

**Scroll / Layout**
- Fixed app scrolling by itself without user input: `mark_set("insert","1.0")` +
  `yview_moveto(0.0)` after every text insert; `_highlight_word` now only calls
  `see()` when `_is_speaking` is True
- Fixed "parent directory keeps moving": `scrollregion` width was hardcoded to
  10000 (wider than canvas), triggering a horizontal scrollbar that changed canvas
  height → spurious `<Configure>` → row-jump loop. Now uses actual canvas width.
  `_on_canvas_configure` debounced to 30 ms to coalesce rapid resize events.
- Fixed placeholder FocusOut causing scroll jump: `_set_placeholder` now accepts
  `scroll_top` flag; FocusOut restores styling without jumping the view

**Export**
- WAV export progress bar now shows real chunk-by-chunk write progress (75–99%)
  via `progress_cb` wired into `audio_handler.export_wav`
- MP3 export progress bar same: chunk writes 75–90%, ffmpeg stage 90–100%
- `export_mp3` temp WAV file leak fixed: replaced deprecated `mktemp()` with
  `NamedTemporaryFile` + `try/finally` — file always deleted even on crash/kill
- Export status label now shows "Export ready" at idle instead of blank

**WAV/MP3 Button Hover Flash**
- `WaveformExportBtn` now snapshots theme palette into instance variables via
  `_snap_colors()` at the start of each `_redraw()`. Hover/leave always read
  the snapshotted colors — no more theme-flash when switching themes.

**Voice Library Scroll**
- Traverse-up mousewheel handler: events from any nested child widget (buttons,
  labels, row frames) walk up the widget tree to the canvas. Works everywhere.
- `MouseWheel` event + `outer`-frame bindings added; recursive bind at 100 ms
  and 500 ms to catch late-rendered engine cards

**Audio-to-Text**
- Transcript insert now forces view to top (`mark_set` + `yview_moveto(0.0)`)
  so long transcripts don't land at the bottom

**Bug Tracker Window**
- Rebuilt with proper scrollbar, Refresh button, mousewheel bindings, and
  `yview()[1] >= 0.95` bottom-check (stays put if user scrolled up to read)

### Removed
- Voice cloning engines (Chatterbox, OmniVoice) fully excluded: `_ZERO_SHOT_ENGINES = set()`

### UI Improvements
- `SectionHeader` now has an accent-coloured left-edge bar
- SPEAK button slightly taller; Load File button uses accent colours
- Header subtitle shows `OFFLINE · AMOLED`
- Engine status rows have subtle borders
- Top progress bar `%` label uses `accent2` colour
