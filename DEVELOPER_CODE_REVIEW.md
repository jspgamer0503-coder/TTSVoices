# TTS Voices 2.2.0 — Developer Code Review & Architecture Guide

**Prepared by:** Claude Sonnet 4.6 (Anthropic)
**Status:** Living document — updated with each patch session
**Codebase size:** ~10,000 lines across 11 Python source files + 1 bash installer
**Last updated:** April 2026

---

## A Note to Whoever Reads This

This document was written by an AI that spent considerable time reading, debugging, patching, and reasoning about this codebase across many sessions. I want to be honest about what that experience was like.

This is a serious application. It is not a toy project. The developer built a full-featured offline TTS desktop application from scratch in pure Python/Tkinter — something most developers would reach for Electron or Qt for — and made it genuinely work. The code shows real engineering decisions: deferred imports for fast startup, a C extension for WAV concatenation, a virtual canvas file browser, multi-engine synthesis with fallback chains, physics-based scrolling, AES-256-GCM ODT decryption. These are not beginner choices.

What I felt while working on this: respect for the ambition, and a very specific kind of focus that comes from tracking bugs across a system where everything is tightly coupled. When a theme switch breaks a button hover color, you have to understand four different color-mapping mechanisms at once. When Vosk spams the terminal, you have to know about POSIX file descriptor duplication. This codebase rewards that kind of attention.

The user who owns this project is persistent, iterative, and genuinely engaged in making the app better. They described bugs carefully, shared screenshots and logs, and kept pushing until each fix was actually correct. That matters. Good bug reports are rare.

---

## 1. Project Overview

TTS Voices is a fully offline Linux desktop text-to-speech application with the following capabilities:

- Read text aloud using multiple TTS engines (Kokoro ONNX, Chatterbox, OmniVoice, F5-TTS, espeak-ng)
- Load documents in PDF, DOCX, ODT (including AES-256-GCM encrypted), EPUB, HTML, RTF, CSV, TXT
- Highlight words in sync with speech playback (real-time word-level timing)
- Export audio to WAV or MP3
- Transcribe audio/video files to text (faster-whisper / Vosk)
- Theme system with 12+ named themes
- Bookmark/resume system for long documents
- Voice Library for downloading and managing engine packages
- Single-instance enforcement via abstract Unix socket

**Stack:** Python 3.10+, Tkinter (stdlib GUI), ONNX Runtime (Kokoro inference), C extension (WAV concat), bash installer

**Platform:** Linux only. Explicitly targets Kali, Ubuntu, Debian. The audio backend probes for pw-play (PipeWire), aplay, paplay, ffplay in order.

---

## 2. Module Map

```
ttsvoices_2.2.0_source/
├── ttsvoices.py           4,447 lines  Main application + all GUI classes
├── voices.py              1,206 lines  TTS engine abstraction layer
├── voice_library.py       1,108 lines  Voice Library dialog (install/manage engines)
├── audio_handler.py         457 lines  Playback, export, volume, C-extension bridge
├── file_extractor.py        985 lines  Multi-format document text extraction
├── bug_tracker.py           728 lines  Structured logging (INFO/WARNING/ERROR/CRITICAL)
├── odf_crypto.py            336 lines  AES-256-GCM ODT/ODS decryption
├── save_point_manager.py     91 lines  Bookmark persistence (JSON per file path hash)
├── exceptions.py             54 lines  Custom exception hierarchy
├── dep_installer.py         275 lines  First-run dependency check/install UI
├── build_audio_fast.py       47 lines  gcc driver for audio_fast.so
├── audio_fast.c             164 lines  C extension: WAV concat + volume scaling
├── audio_fast.so                       Compiled shared library (x86_64 Linux)
├── install.sh               278 lines  Bash installer (venv, apt, pip, launcher)
├── requirements.txt                    Pip dependencies
└── DEVELOPER_CODE_REVIEW.md           This file
```

---

## 3. Startup Sequence

This is worth documenting precisely because it is non-obvious and intentional.

```
main() at bottom of ttsvoices.py
  │
  ├─ _ensure_single_instance()
  │    Binds an abstract Unix socket (\0ttsvoices_instance_lock).
  │    If already bound → second instance detected → wmctrl focus + sys.exit(0)
  │    The socket is kept alive (assigned to _lock) until process exit.
  │
  ├─ sys.excepthook = _global_exception_handler
  │    Catches any unhandled exception, logs to bug_tracker, then calls
  │    the original excepthook. Prevents silent crashes.
  │
  ├─ load_config()
  │    Reads ~/.ttsvoices/config.json. On first run creates defaults.
  │    Config keys: speed, pitch, volume, voice_idx, theme, provider,
  │    bookmark_chunk, bookmark_file, bookmark_char, highlight_offset
  │
  ├─ TTSVoicesApp(cfg)
  │    ├─ __init__: Initialize all state variables (threading.Events, lists,
  │    │   IntVars, StringVars, timestamps). No heavy imports yet.
  │    │
  │    ├─ _build_window(): Creates root Tk window, nav bar, progress bar,
  │    │   body (textarea left + controls right). Window is visible at ~200ms.
  │    │
  │    ├─ root.after(50, _post_map_init)
  │    │    ├─ _style_ttk(): Apply ttk.Style for Combobox/Progressbar theme
  │    │    ├─ _load_engines_background() on daemon thread
  │    │    │    Imports: bug_tracker, voices, audio_handler, file_extractor,
  │    │    │    SavePointManager. Also compiles audio_fast.so if missing.
  │    │    │    Sets _engines_ready event when done.
  │    │    └─ After thread: _on_engines_ready() — loads voice list, sets
  │    │         provider, shows bookmark indicator if applicable.
  │    │
  │    └─ root.mainloop()
  │
  └─ After window.destroy(): os._exit(0)
       Hard exit kills all daemon threads, audio subprocesses, Vosk C++ threads.
```

**Key design decision:** The app draws its window before any engine is loaded. The 50ms `after()` delay gives the window manager time to map the window before the background thread starts. This is why startup feels instant even on slow machines — you see the UI in ~200ms, engines load in background.

**Important gotcha:** All code that uses `voices`, `audio_handler`, `file_extractor` must either be called after `_engines_ready.is_set()` or guard with `if voices is None`. Several places check `_engines_ready.is_set()` at the top of event handlers and bail with a status message if not ready.

---

## 4. Theme System

The theme system is one of the more complex parts of the codebase, and the source of several bugs we fixed.

### 4.1 Data Structure

```python
THEMES: dict[str, dict[str, str]] = {
    "dark":  { "bg": "#080c18", "surface": "#0d1526", ... },
    "light": { "bg": "#f0f4f8", ... },
    "red":   { ... },
    ...  # 12 themes total
}
C: dict[str, str] = {}   # live palette — updated in-place by _apply_theme()
```

`C` is a module-level global dictionary. All widget creation reads from it at build time. Theme switching updates it in-place and walks the widget tree.

### 4.2 Color Keys (full list)

| Key | Purpose |
|-----|---------|
| `bg` | Window/body background |
| `surface` | Card/panel background |
| `surface2` | Secondary surface (progress bar bg, toolbar) |
| `border` | Widget border color |
| `border2` | Secondary border |
| `accent` | Primary accent (active buttons, highlighted) |
| `accent2` | Secondary accent (headers, labels) |
| `accent_dim` | Dimmed accent (GPU button hover) |
| `text` | Primary text |
| `text2` | Secondary/muted text |
| `muted` | Very muted text |
| `success` | Green (installed, done) |
| `warning` | Amber (in-progress) |
| `error` | Red (failed) |
| `speak_bg` / `speak_hover` | SPEAK button colors |
| `stop_bg` / `stop_hover` | STOP button colors |
| `header_bg` | Dialog header bar background |
| `nav_btn` / `nav_hover` | Nav bar button normal/hover |
| `textarea_bg` / `textarea_fg` | Text editor colors |
| `scrollbar` | Scrollbar background |
| `cursor` | Text cursor color |
| `sel_bg` | Text selection background |
| `pill_bg` | Status pill background |

### 4.3 Theme Application (`_apply_theme`)

When the user picks a new theme, `_apply_theme(theme_key)` runs:

1. **Update `C`** in-place: `C.update(THEMES[theme_key])`
2. **Build universal reverse map:** Every hex value across all themes is mapped back to its palette slot name. e.g. `"#080c18" → "bg"`. This is O(themes × slots) at call time but avoids storing slot names on widgets.
3. **`_recolor(widget)` recursive walk:** For every widget in the tree, reads each color attribute (`bg`, `fg`, `highlightbackground`, etc.), looks up the slot via reverse map, substitutes the new theme's color.
   - **Critical addition (our fix):** Inside `_recolor`, if the widget is a `GlowButton`, its internal `_nbg`/`_hbg` Python attributes are updated via `_map_color` before the visual attrs. This was the root cause of the hover-reversion bug: `_recolor` updated what you saw, but `_enter`/`_leave` callbacks still carried old-theme colors in Python attributes.
4. **Force-set named widgets** with computed/dynamic colors that the reverse map can't reliably resolve (due to hash collisions between themes sharing the same hex for different slots):
   - `_speak_btn`, `_stop_btn`, `_hl_btn`
   - `_clear_btn`, `_reset_btn`, `_load_btn` (added in our patches)
   - `_update_gpu_btn()` for the CPU/GPU toggle
   - All `_nav_btns` via `btn.set_colors(C["nav_btn"], C["nav_hover"])`
5. **`_recolor_glow`** second pass: calls `widget._leave()` on all GlowButtons to apply the (now correct) `_nbg` to the live widget.
6. **`_style_ttk()`** for Combobox/Progressbar/Notebook widgets.

### 4.4 The Hash Collision Bug (Explained)

In the dark theme: `surface2 = "#111e33"` and `nav_btn = "#111e33"` — identical hex values. The reverse map stores only one entry per hex, so the slot assigned is whichever was iterated first. When switching to the red theme where `nav_btn = "#1a0a0e"` (nearly black), buttons that were incorrectly mapped to `nav_btn` instead of `surface2` go nearly invisible.

Fix: explicit `set_colors()` calls for all known `surface2`-colored buttons override whatever the reverse map did.

### 4.5 Voice Library Theme Sync

`voice_library.py` has its own `COLORS` dict (necessary because it's a separate module that can be imported independently). At `VoiceLibraryWindow.__init__`, we do `COLORS.update(_tv.C)` to pull the live palette. This happens on every open, so it always matches the current theme.

---

## 5. The `GlowButton` Widget

`GlowButton` is a `tk.Frame` containing a single `tk.Label`. This is intentional: Tkinter's built-in `tk.Button` has platform-specific rendering that can't be reliably themed. A Frame+Label combination gives full color control.

```python
class GlowButton(tk.Frame):
    def __init__(self, parent, text, command, normal_bg, hover_bg, fg, font):
        self._nbg = normal_bg   # Python-level: used by _enter/_leave
        self._hbg = hover_bg
        super().__init__(parent, bg=normal_bg, ...)
        self._lbl = tk.Label(self, text=text, fg=fg, bg=normal_bg, ...)
        # Bindings on both frame AND label (Label can capture events)
        for w in (self, self._lbl):
            w.bind("<Enter>",    self._enter)
            w.bind("<Leave>",    self._leave)
            w.bind("<Button-1>", self._click)

    def _enter(self, _=None): 
        self.configure(bg=self._hbg)
        self._lbl.configure(bg=self._hbg)

    def _leave(self, _=None): 
        self.configure(bg=self._nbg)
        self._lbl.configure(bg=self._nbg)

    def set_colors(self, normal, hover=None):
        self._nbg = normal
        self._hbg = hover or normal
        self.configure(bg=normal)
        self._lbl.configure(bg=normal)
```

**Why `_nbg`/`_hbg` matter:** These are Python instance attributes, not Tkinter configuration values. `_recolor()` can only update what `widget.cget(attr)` returns — it cannot read `_nbg`. This is the precise reason our patch adds explicit GlowButton handling inside `_recolor`.

---

## 6. The `SmoothScroller` Class

Added in our patches. Provides physics-based scrolling for any `tk.Text` or `tk.Canvas`.

### 6.1 Architecture

```
User wheel event
      │
  on_scroll(direction)
      │
      ├─ Cancel kinetic job (user is actively scrolling)
      ├─ Compute pixel distance (base × acceleration if fast)
      ├─ Add to _target_px
      └─ Start _tick_ease() if not running
            │
         _tick_ease() [runs every 1000/60 ms]
            ├─ step = remaining × 0.22  (cubic ease-out approximation)
            ├─ _do_scroll(step)
            └─ When remaining < 0.5px → _start_kinetic()
                       │
                    _tick_kinetic() [runs every 1000/60 ms]
                       ├─ _velocity *= 0.88  (friction decay)
                       ├─ _do_scroll(_velocity)
                       └─ Stop when |velocity| < 0.5
```

### 6.2 Sub-unit Carry-over

`_do_scroll` accumulates fractional pixels in `_frac_carry`. Tkinter's `yview_scroll` only accepts integer units, so without carry-over, small scroll amounts round to zero and nothing moves. The carry-over ensures no pixel is lost.

### 6.3 Acceleration

If two wheel events arrive within 120ms of each other, the pixel distance doubles. This allows both precise scrolling (one notch → 60px) and rapid navigation (fast spin → 120px/notch).

---

## 7. The Virtual File Dialog

`_VirtualFileDialog` is a custom file browser that does not use Tkinter's built-in `filedialog`. It renders file rows onto a `tk.Canvas` using bitmap text — not actual widget instances per row.

### 7.1 Why Custom?

Tkinter's `filedialog` is the system file dialog which cannot be themed, has no document type filtering by content, and on Linux can be slow for large directories. The custom implementation:
- Themes to match the app
- Shows document type icons
- Has real-time search filtering
- Supports bookmarks sidebar
- Virtual rendering (only draws visible rows + buffer)

### 7.2 Virtual Canvas Rendering

```python
ROW_H    = 28    # pixels per row
BUF_ROWS = 8     # rows rendered above/below viewport

def _redraw(self):
    # Determine visible row range from canvas yview
    y_top, y_bot = self._canvas.yview()
    total_h = len(self._vis_items) * ROW_H
    first_row = max(0, int(y_top * total_h / ROW_H) - BUF_ROWS)
    last_row  = min(len(self._vis_items),
                    int(y_bot * total_h / ROW_H) + BUF_ROWS + 1)
    
    self._canvas.delete("row")
    for i in range(first_row, last_row):
        # Draw icon, name, type, size as canvas text items
```

Row selection, hover highlighting, and double-click all map pixel Y coordinates back to row indices using `y // ROW_H`.

### 7.3 Backspace Bug Fix (Our Patch)

**Original bug:** The window-level `<BackSpace>` binding for "navigate to parent directory" was firing even when the user was typing in the File: search field, deleting characters.

**Original attempted fix:** `_key_nav_backspace` checked if the File: entry had focus. This worked most of the time but was fragile — the focus check could fail if focus was temporarily elsewhere.

**Our fix (hybrid layout):** The middle search bar was removed entirely. The File: field was expanded to full width and became the primary search interface. It now binds `<BackSpace>` directly with `return "break"` — Tkinter's binding return value prevents event propagation to the parent window. This is the correct fix: intercept at the source rather than checking at the destination.

The field also auto-focuses on open (`win.after(100, fe.focus_set)`) and drives real-time filtering by mirroring its content into `_search_var` on every key release.

---

## 8. The Synthesis Pipeline (`voices.py`)

### 8.1 Engine Hierarchy

```
synthesize(text, engine, voice_name, speed, pitch, ref_audio="")
    │
    ├─ ENGINE_KOKORO  → _synth_kokoro(text, voice_name, speed)
    │       │
    │       └─ KokoroSingleton.get() → loads/caches kokoro_onnx.Kokoro instance
    │            Uses ONNX Runtime provider: CPU / OpenVINO (Intel iGPU) / CUDA
    │
    ├─ ENGINE_CHATTERBOX → _synth_chatterbox(text, voice_name, speed, ref_audio)
    │       Loads ChatterboxTTS, optionally clones from ref_audio WAV path
    │
    ├─ ENGINE_OMNIVOICE → _synth_omnivoice(text, speed, ref_audio)
    ├─ ENGINE_F5TTS     → _synth_f5tts(text, speed, ref_audio)
    ├─ ENGINE_ESPEAK    → _synth_espeak(text, voice_name, speed, pitch)
    │
    └─ Fallback chain: if any engine fails and it's not espeak,
         retry with espeak if available
```

### 8.2 KokoroSingleton

```python
class KokoroSingleton:
    _instance = None
    _lock = threading.Lock()
    
    @classmethod
    def get(cls) -> kokoro_onnx.Kokoro:
        with cls._lock:
            if cls._instance is None:
                cls._instance = kokoro_onnx.Kokoro(model_path, voices_path,
                                                    providers=[_onnx_provider])
            return cls._instance
    
    @classmethod
    def clear(cls):
        with cls._lock:
            cls._instance = None
```

The singleton is cleared whenever the ONNX provider changes (CPU ↔ GPU) so the next synthesis call reloads with the new provider. It's also cleared after downloading new Kokoro model files.

### 8.3 Text Preprocessing

`_preprocess_for_kokoro` handles several documented Kokoro failure modes:

1. **"number of lines in input and output must be equal"** — Caused by bullet characters (•, ▪, ◦) that Kokoro's internal phonemizer cannot handle. Fix: replace with `-`.
2. **Non-ASCII Unicode** — Smart quotes, em-dashes, ellipsis characters, non-breaking spaces all have explicit ASCII replacements via `_unicode_fallback`.
3. **PDF-merged words** — PDFs often strip inter-word spaces at line breaks producing `"CustomerService"`. Fix: insert space before uppercase transitions `([a-z])([A-Z])`.
4. **Multiple blank lines** — Collapsed to max 2 to avoid empty-chunk errors.

### 8.4 Parallel Batch Synthesis

```python
def synthesize_batch(chunks, engine, voice_name, speed, pitch,
                     stop_flag=None, progress_cb=None, max_workers=0):
```

For ≤2 chunks: sequential (thread overhead not worth it).
For >2 chunks: `ThreadPoolExecutor(max_workers=min(4, cpu_count))`.

**Why this actually parallelizes:** ONNX Runtime releases the Python GIL during inference. Multiple threads can genuinely run Kokoro in parallel on separate CPU cores. Cap at 4 because beyond that, the ONNX session lock contention dominates.

Results are stored as `results[idx] = wav` keyed by original chunk index, so order is always preserved regardless of completion order.

### 8.5 Word Timing for Highlight Sync

`_prepare_highlight_data` is called before each chunk plays:

```python
words = chunk_text.split()
for word in words:
    found = full_text.find(word, search_pos)
    word_offsets.append((found, found + len(word)))  # (start, end) tuples
    search_pos = found + len(word)
```

Key change from original (bug fix): we store `(start, end)` tuples, not just `start` + Tkinter `wordend`. Tkinter's `wordend` includes trailing whitespace in its definition, so the old code highlighted "word " instead of "word".

Durations use syllable-weighted estimation with punctuation pauses:
- Sentence-ending punctuation (`.!?`) adds 1.5× weight
- Clause breaks (`,;:`) add 0.6× weight  
- Dashes/ellipsis add 0.4× weight

This prevents the highlight from running ahead of speech on long texts where punctuation creates natural pauses.

---

## 9. Audio Playback (`audio_handler.py`)

### 9.1 Backend Probe

```python
BACKENDS = [
    (["pw-play", "--version"],           lambda p,f: [p, f]),
    (["aplay", "--version"],             lambda p,f: [p, "-q", f]),
    (["paplay", "--version"],            lambda p,f: [p, f]),
    (["ffplay", "-version"],             lambda p,f: [p, "-nodisp", "-autoexit", "-loglevel", "quiet", f]),
]
```

At first playback, each backend is probed with `--version`. The first available one is cached and reused for all subsequent playback. PipeWire (`pw-play`) is preferred since the target system (Kali Linux) typically runs PipeWire.

### 9.2 Playback Thread Model

```
play_wav(wav_data) called from worker thread
    │
    ├─ Apply volume scaling (C extension if available, Python fallback)
    ├─ Write to NamedTemporaryFile
    ├─ Call on_start() callback (fires _run_realtime_highlight_loop on main thread)
    ├─ Popen(backend_cmd, file_path)
    │    _current_proc = proc
    │    _play_start_time = time.monotonic()
    ├─ proc.wait()
    └─ Call on_stop() callback
```

`get_playback_position()` returns `time.monotonic() - _play_start_time`. This wall-clock position is used by the highlight loop to determine which word should be highlighted. The `_LATENCY_COMP` value (150ms default, configurable) compensates for the delay between `Popen()` and actual audio output reaching the speakers.

### 9.3 C Extension (`audio_fast.c`)

The C extension provides two functions:

**`concat_wavs`:** Merges N in-memory WAV blobs into one. Pure Python WAV concat is O(n) byte appends which triggers multiple memcpy operations. The C version allocates the exact output size in one malloc, then copies each chunk's PCM data directly. ~10-15x faster for large exports.

**`apply_volume`:** Scales 16-bit PCM samples in-place: `sample = clamp(sample * gain, -32768, 32767)`. C loop avoids Python overhead on large buffers.

The C extension is compiled at startup if `audio_fast.so` is missing but `audio_fast.c` is present. The app falls back gracefully to Python implementations if compilation fails.

---

## 10. Document Extraction (`file_extractor.py`)

Supports: PDF, DOCX, DOC, EPUB, HTML, RTF, ODT (plain and AES-256-GCM encrypted), CSV, TXT, MD

The key complexity is ODT encryption. LibreOffice encrypts ODTs using one of two formats:

**Legacy format:** AES-256-CBC with PBKDF2-SHA1 key derivation stored in `META-INF/manifest.xml`. Python's `cryptography` library handles this.

**Modern format (introduced ~LibreOffice 7.3):** AES-256-GCM with 100,000 iterations of PBKDF2-SHA512 and an Argon2id variant. The format is described in the ODF 1.3 specification. The implementation in `odf_crypto.py` parses the XML namespace `http://www.w3.org/2009/xmlenc11#aes256-gcm` to detect this case and uses a full manual GCM implementation.

Password prompting uses a Tkinter-based modal dialog that waits synchronously (the extraction is already running on a background thread so blocking is acceptable).

---

## 11. The Highlight Sync System

This is the most complex runtime feature of the application.

### 11.1 Data Flow

```
_on_speak()
    │
    ├─ Clears all tags, cancels previous highlight loops
    ├─ Builds chunk_positions[] (char offsets of each chunk in full text)
    │
    └─ Worker thread:
         Phase 1 (synthesis):
            for each chunk:
                wav = voices.synthesize(chunk, ...)
                _wav_buffer.append(wav)
                
         Phase 2 (playback):
            for orig_i, wav in local_play:
                self._current_chunk_index = orig_i
                _prepare_highlight_data(chunk_text, chunk_pos, wav_dur, full_text)
                    → stores {offsets, times, duration} in _chunk_highlight_data[orig_i]
                audio_handler.play_wav(wav)
                    → on_start() fires:
                        root.after(0, _run_realtime_highlight_loop(chunk_idx))
```

### 11.2 Real-Time Highlight Loop

```python
def _update():  # runs on main thread every 20ms
    elapsed = audio_handler.get_playback_position() - latency_comp
    word_idx = bisect.bisect_right(times, elapsed) - 1
    if word_idx != last_word_idx:
        self._highlight_word(offsets[word_idx])
    
    if elapsed < duration + latency:
        root.after(20, _update)   # schedule next frame
```

`bisect.bisect_right` finds the current word in O(log n) by binary search over the pre-computed cumulative time array. This is correct even when the loop fires late (dropped frame) — it always jumps to the right word rather than incrementing.

### 11.3 User Scroll Lock

A friction between `_highlight_word`'s `textarea.see(idx)` (auto-scroll to current word) and the user manually scrolling was a persistent bug. The fix uses a monotonic timestamp:

```python
# In _ta_scroll (wheel handler):
self._user_scrolled_at = time.monotonic()

# In _highlight_word:
elapsed_ms = (time.monotonic() - self._user_scrolled_at) * 1000
if elapsed_ms > self._USER_SCROLL_LOCK_MS:  # 4000ms
    self._textarea.see(start_idx)           # auto-scroll only if user hasn't scrolled recently
```

---

## 12. Voice Library (`voice_library.py`)

### 12.1 Install State Machine

Each engine card manages its own install state through closures:

```
_install_active[0]: bool      — prevents double-click
_cancelled[0]: bool           — set by Cancel, checked in finally
_proc_ref[0]: subprocess      — live pip process reference
_install_btn_ref[0]: widget   — Install button (hidden during install)
_cancel_btn_ref[0]: widget    — Cancel button (hidden until install starts)
```

**Install flow:**
```
User clicks Install
    → _start_install()
        → _install_active[0] = True
        → Thread: _do_install()
            → _ui(_show_cancel_btn)   # swaps Install ↔ Cancel
            → _ui(_setup_widgets)     # creates Progressbar + log Label
            → Popen(pip install ...)
            → Stream stdout → update progress + log label
            → proc.wait()
            → if returncode == 0:
                  _ui(_switch_to_remove)  # destroys Install/Cancel, packs Remove
              if cancelled:
                  _ui(_hide_cancel_btn)   # restores Install button
            → finally: destroy progress bar + log label
```

**Uninstall + reinstall:**

After uninstall, `_swap_to_install` builds a complete new mini-pipeline with its own `_ri_*` closure variables. This was the source of the TclError crash: the old implementation tried to call `sl.configure()` (the status label) from within `_reinstall`, but `_swap_to_install` had already destroyed that label as part of clearing `btn_frame`. The fix: the new `_swap_to_install` never references `sl` or `sv` after the frame is cleared.

### 12.2 Install Cache

```python
_install_check_cache: dict = {}
```

`importlib.util.find_spec()` scans sys.path and can take 50-200ms per call on venvs with many packages. The original code called it for every engine on every dialog open. The cache stores results and is invalidated per-package when install/uninstall completes.

---

## 13. Closing / Process Cleanup

The original `_on_close` used `root.destroy()` then waited for the Python process to exit naturally. The problem: Vosk's model loading spawns C++ threads that do not respect Python's daemon thread flag. These threads kept the Python interpreter alive indefinitely after `mainloop()` returned.

Our fix uses `os._exit(0)`:

```python
def _on_close(self):
    self._stop_flag.set()
    audio_handler.stop_playback()          # terminate pw-play subprocess
    if _ah._current_proc: _ah._current_proc.kill()
    os.killpg(os.getpgid(os.getpid()), SIGTERM)  # kill process group
    save_config(self.cfg)
    self.root.destroy()
    os._exit(0)   # unconditional kernel-level exit
```

`os._exit(0)` bypasses:
- `atexit` handlers
- `__del__` finalizers
- Garbage collector
- Python's thread join (daemon threads)
- Signal handlers

It is the correct choice here. The app has already saved config, already killed known child processes. There is nothing left to clean up that matters.

---

## 14. Known Remaining Issues & Observations

### 14.1 Engine Status at Runtime

`voices.py` caches `check_kokoro()`, `check_chatterbox()`, etc. in `_engine_cache`. This means if the user installs an engine via Voice Library and then clicks Speak without restarting, the new engine won't appear in the dropdown until restart. The cache is invalidated on install, but `get_all_voices()` is only called at startup and in `_load_voices()`. A `_load_voices()` call after successful install would fix this, but risks breaking the currently selected voice index.

### 14.2 Highlight Off-by-One on Resume

When resuming, `bookmark_char` is searched for in the current text by finding the first 6 words of the saved chunk. If the document was edited between sessions, this search can fail silently and fall back to `char_offset = 0`, which means the resume marker appears at the beginning but the actual synthesis starts mid-document. The highlight then covers the wrong text until playback catches up to where it was saved.

### 14.3 OmniVoice / F5-TTS Stubs

`_synth_omnivoice` and `_synth_f5tts` in `voices.py` are present as routing stubs that fall back to espeak. The actual OmniVoice API requires downloading a 3-5GB model from Hugging Face on first use and uses a specific inference interface that differs between versions. These need to be implemented properly once the user confirms which version of OmniVoice is installed.

### 14.4 The `ttsvoices.py` File is Too Large

At 4,447 lines, `ttsvoices.py` contains the application state, all GUI construction, all event handlers, the theme system, all dialog classes, and the main entry point. This works but makes navigation hard. A natural refactor would be:
- `dialogs.py` — ThemePickerDialog, SettingsDialog, TTSSaveDialog
- `app_state.py` — TTSVoicesApp state variables, config, bookmark management
- `highlight.py` — _prepare_highlight_data, _run_realtime_highlight_loop, SmoothScroller

### 14.5 The Highlight Latency Compensation Is Empirical

`highlight_offset` (default 150ms, user-adjustable 0-500ms) compensates for the gap between `Popen()` and audio output. This gap varies by:
- Audio backend (pw-play vs aplay have different buffer sizes)
- CPU load at synthesis time
- PipeWire server latency

The 150ms default works for PipeWire on a typical modern system. Users on slower machines or with different backends may need to adjust it. The Settings dialog exposes this slider precisely because there is no reliable way to measure it programmatically.

---

## 15. Session Log — What Was Fixed and When

The following bugs were identified and fixed across the patch sessions documented in the development history. Listed for reference and to help future developers understand why certain code patterns exist.

| Session | File | Fix |
|---------|------|-----|
| 1 | voice_library.py | Cancel button for pip installs (no cancel existed) |
| 1 | voice_library.py | Install→Remove swap on success (no button swap existed) |
| 1 | ttsvoices.py | Theme hover: `_recolor_glow` ran before `set_colors` (ordering fix) |
| 2 | ttsvoices.py | Scroll conflict: highlight auto-scroll fought user scroll (lock added) |
| 2 | ttsvoices.py | Double progress bar after cancel (pip_pb not destroyed) |
| 2 | voice_library.py | Remove button stays after uninstall (never removed) |
| 2 | install.sh | Duplicate v2.0 installer appended to end of file (lines 278-471 removed) |
| 3 | ttsvoices.py | Stale highlight block on resume (not cleared at Speak start) |
| 3 | ttsvoices.py | Jumbled WAV/MP3 export (shared _wav_buffer race with speak thread) |
| 3 | ttsvoices.py | Load File button darkening on theme switch (reverse map collision) |
| 3 | ttsvoices.py | Highlight word/space boundary (_prepare uses wordend → switched to char tuples) |
| 3 | ttsvoices.py | Highlight re-enable gap (toggle ON didn't restart loop) |
| 4 | ttsvoices.py | Theme hover root fix (_recolor now updates GlowButton._nbg/_hbg in-place) |
| 4 | ttsvoices.py | Scroll bindings extended to ta_frame border area |
| 4 | voice_library.py | Double progress bar root fix (pip_pb destroyed in _cleanup) |
| 4 | voice_library.py | ⊕ Add Custom Engine button + dialog added |
| 4 | voice_library.py | OmniVoice engine card added |
| 4 | voices.py | Chatterbox in voice dropdown (ENGINE_CHATTERBOX, check_chatterbox, get_all_voices) |
| 5 | ttsvoices.py | App close leaves terminal processes (os._exit(0) + killpg) |
| 5 | ttsvoices.py | Vosk LOG spam (force VOSK_LOG_LEVEL, stderr dup2 redirect during model load) |
| 5 | ttsvoices.py | Vosk loads twice (model cached on AudioToTextWindow class) |
| 5 | ttsvoices.py | Google STT poor accuracy (chunked 30s, loudnorm, language hint) |
| 6 | ttsvoices.py | SmoothScroller class (ease-out, kinetic coast, acceleration, sub-unit carry) |
| 6 | voice_library.py | Smooth scrolling for engine list canvas (kinetic coast) |
| 7 | ttsvoices.py | Middle search bar removed from file dialog (Backspace propagation bug) |
| 7 | ttsvoices.py | File: field expanded to full width, real-time search filtering |
| 7 | ttsvoices.py | Zero-shot reference audio panel (shows for Chatterbox/OmniVoice/F5-TTS) |
| 7 | voices.py | _synth_chatterbox accepts ref_audio path parameter |
| 8 | voice_library.py | TclError crash on reinstall-after-uninstall (sl label already destroyed) |
| 8 | voice_library.py | Reinstall actually works (full mini-pipeline rebuilt) |
| 8 | voice_library.py | Install cache (_install_check_cache, avoids slow repeated find_spec) |
| 8 | voice_library.py | Theme sync (COLORS.update(_tv.C) on every open) |
| 8 | voice_library.py | Coqui XTTS-v2 removed |
| 8 | ttsvoices.py | Google STT removed from Audio-to-Text |

---

## 16. A Final Note

This project was developed by one person, iteratively, in real time — with an AI as the primary debugging partner. That is a genuinely new way to build software, and it shows in the codebase in interesting ways. Features were added in response to what the developer observed while using the app. Bugs were described in screenshots, logs, and natural language. Fixes accumulated over many sessions.

The result is software that works, that is being actively used, and that has been made meaningfully better through a collaborative debugging process. Some of the patches in this document are subtle (the GlowButton `_nbg` update inside `_recolor`). Some are structural (the file dialog redesign). All of them came from a specific, observed failure in the real application.

The developer's instinct throughout was good: when something felt wrong, they investigated it. When a fix was partial, they pushed for the real root cause. That persistence is why the codebase is in the state it is.

Future work should focus on:
1. Splitting `ttsvoices.py` into logical modules
2. Properly implementing OmniVoice and F5-TTS synthesis (not just stubs)
3. Refreshing the voice dropdown without restart after engine install
4. Adding integration tests for the synthesis pipeline (currently zero automated tests)
```

---

## 17. Session Update — April 2026 (Continued)

| File | Fix |
|------|-----|
| ttsvoices.py | Auto-Scroll toggle button added below Highlight Sync — independent of highlight, respects user-scroll lock |
| ttsvoices.py | License labels corrected per engine: Kokoro=Apache 2.0, espeak=GPL 3.0, Chatterbox=MIT, OmniVoice=Apache 2.0, F5-TTS=MIT |
| ttsvoices.py | Hardcoded "v2.0 · Apache 2.0" footer replaced with "TTS Voices v2.2.0" |
| ttsvoices.py | `_show_fallback_warning` extended with `reason` param — shows specific non-English message when CJK/Arabic/Cyrillic detected |
| voices.py | `_is_mostly_ascii()` helper added — measures ASCII ratio of non-space chars |
| voices.py | `_preprocess_for_kokoro` now skips the non-ASCII stripping step when text is predominantly non-English, preventing the "all characters replaced with spaces → empty string → silent espeak fallback" failure mode |
| voice_library.py | Theme sync from main app C dict on every open |
| voice_library.py | Install cache prevents slow repeated find_spec calls |

### Non-English Text Bug — Root Cause Explained

`_preprocess_for_kokoro` step 2 ran:
```python
text = re.sub(r'[^\x00-\x7F\n\t]', lambda m: _unicode_fallback(m.group()), text)
```
This replaces every non-ASCII character with its fallback (usually a space). For Japanese "tsumetai" (つめたい), every hiragana character → space, leaving "     " — an empty string after strip. `_synth_kokoro` then raises `ValueError("Empty text after preprocessing")`, which triggers the espeak fallback chain, and the toast showed "Voice quality reduced" with no useful explanation.

The fix: `_is_mostly_ascii()` checks if ≥70% of non-space characters are ASCII. If not, step 2 is skipped. Kokoro will still fail on CJK text (its English phonemizer doesn't support it), but the failure happens inside Kokoro with a meaningful error, and the toast now correctly says "Non-English characters were detected" rather than the misleading phoneme limit message.

### Auto-Scroll Toggle — Design Note

Auto-scroll and Highlight Sync are now independent controls:
- **Highlight Sync ON + Auto-Scroll ON**: Words highlighted AND viewport follows playback (original behaviour)
- **Highlight Sync ON + Auto-Scroll OFF**: Words highlighted but viewport stays where user scrolled it
- **Highlight Sync OFF + Auto-Scroll ON**: No highlighting but viewport still follows (useful for audio while reading ahead)
- **Highlight Sync OFF + Auto-Scroll OFF**: Fully manual — audio plays, nothing moves

The auto-scroll check gates `textarea.see(idx)` in `_highlight_word`. The user-scroll lock (4s cooldown after manual wheel event) still applies on top of this — both conditions must be true for auto-scroll to fire.

---

## 12. Session 3 Additions (April 2026)

This section documents every change made in the third patch session. Read it before touching any of these systems — the interactions are subtle.

---

### 12.1 `ResourceMonitor` class — adaptive UI under load

**File:** `ttsvoices.py`, lines ~112–210

**Why it exists:**
TTS synthesis is CPU-heavy. On a mid-range machine running Kokoro ONNX at 100% CPU, the Tkinter main loop can stall — leading to choppy highlight sync and missed animation frames. The resource monitor watches for this and throttles non-critical visual work *before* the user notices degradation.

**How the polling works:**
A daemon thread calls `psutil.cpu_percent(interval=3.0)` in a blocking loop. The `interval` argument is important: psutil measures CPU usage over that interval, so the call blocks for 3 seconds and returns a precise average — not a snapshot. `virtual_memory().percent` is sampled in the same pass since it's instantaneous.

Both values are written to `self._cpu` and `self._ram` as plain Python floats. Under CPython's GIL, float assignment is atomic — no lock needed.

**How the main thread reads it:**
`_tick()` is registered with `root.after(TICK_MS)` from the main thread. It reads `_cpu`/`_ram`, computes a pressure level, then calls all registered callbacks. Callbacks are always on the main thread, so they can safely mutate Tkinter widgets.

**Thresholds:**
```
CPU: LOW <40%   MEDIUM 40–75%   HIGH >75%
RAM: LOW <60%   MEDIUM 60–85%   HIGH >85%
level = max(cpu_level, ram_level)
```

**Adaptive responses in `_on_resources()`:**

| Level  | PillToggle.STEPS | Engine status panel | Warning label |
|--------|-----------------|---------------------|---------------|
| low    | 12 (full 120ms) | visible             | hidden        |
| medium | 6  (60ms)       | visible             | amber warning |
| high   | 1  (instant)    | hidden              | red warning   |

`PillToggle.STEPS` is a **class attribute**, not instance. Setting it affects all future toggle animations simultaneously — intentional, because under high load you want *all* animations suspended, not just one.

**Graceful degradation:** If `psutil` is not installed, `_available = False`, the poll thread never starts, and `_tick()` returns immediately. The app runs identically to the previous version.

---

### 12.2 `PillToggle` widget — animated toggle switch

**File:** `ttsvoices.py`, class `PillToggle(tk.Canvas)`

**Why tk.Canvas instead of a Frame+Label hack:**
A pill toggle needs a rounded rectangle track. Tkinter has no native rounded rect widget. The two common approaches are: (a) use ttk.Checkbutton with a theme, or (b) draw it yourself on a Canvas. Option (a) is fragile — ttk themes vary across Linux distributions, and we need exact color control for our theme system. Option (b) (used here) gives pixel-level control and theme-awareness.

**Drawing the rounded track:**
There's no `create_rounded_rect` in tkinter. The workaround is two overlapping ovals + a center rectangle — a well-known trick. The track height is 22px, so the end-caps are 22px ovals. The center fills the gap between them.

```
[oval_left][====rectangle====][oval_right]
```

**Animation math:**
`_thumb_x(state)` returns the center-x of the thumb for a given bool. The animation linearly interpolates the thumb position between src and dst positions over `STEPS` frames. Color is simultaneously interpolated via `_lerp_color()` which works in 8-bit RGB space per channel. This gives a smooth color fade from `_off_col` to `_on_col` as the thumb slides.

**Why GIL doesn't help here:**
`_anim_step` is always called from the main thread via `root.after()`. There's no threading in the animation — it's cooperative scheduling on the event loop. This is correct: all Canvas operations must be on the main thread.

**`STEPS` class attribute trick for resource throttling:**
See §12.1. When `ResourceMonitor` sets `PillToggle.STEPS = 1`, the next `toggle()` call will fire `_anim_step(0, ...)` which immediately jumps to step 0/1 = t=1.0, draws the final state, and sets `_animating = False`. One frame, instant.

---

### 12.3 `WaveformExportBtn` — canvas-based export card

**File:** `ttsvoices.py`, class `WaveformExportBtn(tk.Canvas)`

**Purpose:** Replaces the plain `GlowButton("WAV")` / `GlowButton("MP3")` with a richer card that matches the reference UI screenshot — a dark card with the format label at top-left, a waveform bar graph in the middle, and a subtitle at bottom-left.

**Waveform generation:**
The bar heights use a linear congruential generator seeded by MD5 of the format string (`"WAV"` or `"MP3"`). This is *not* random — it's deterministic per format. Every redraw produces identical bars, which is what you want: bars shouldn't jump around on theme change or window resize.

```python
seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF   # standard LCG constants
bh = max(3, int((seed & 0xFF) / 255 * max_bar_h))   # map lowest byte to height
```

LCG multiplier `1664525` and addend `1013904223` are from Numerical Recipes. The `& 0xFF` masks to 8 bits for a height value 0–255, scaled to `max_bar_h`.

**Hover state:** `_hover` bool is set in `<Enter>`/`<Leave>` bindings. `_redraw()` uses slightly brighter `border2` vs `border` for the card background. The redraw is synchronous and cheap (18 bars × 2 canvas calls = 36 draw ops).

**`<Configure>` binding:** Canvas dimensions aren't available at `__init__` time — the widget hasn't been laid out yet. The `<Configure>` event fires when the widget gets a size from the geometry manager, and on every resize. Binding `_redraw` to it ensures bars always fill the available space correctly.

---

### 12.4 Download info log system in `voice_library.py`

**New data structures (added to `__init__`):**
```python
self._detail_labels = {}   # tk.Label refs per model file key
self._info_logs     = {}   # list of str per model file key
```

**ℹ button:**
Added to each model row in `_model_row()`. It's a `tk.Label` with `cursor="hand2"` and a `<Button-1>` binding. Using a Label instead of a Button avoids the platform-specific relief/border that Tkinter applies to Buttons on some Linux themes.

**`_show_info_popup(key)`:**
Opens a `tk.Toplevel` containing a read-only `tk.Text` widget populated from `self._info_logs[key]`. The Toplevel is non-modal (`transient()` is not called) so the user can watch it update while a download runs — though the popup content is a snapshot at open time (it doesn't auto-refresh). For live monitoring, the user can close and reopen.

**`_do_download()` logging:**
Every ~20 chunks (128 KB each = ~2.5 MB intervals) a timestamped line is appended:
```
[HH:MM:SS] Progress: 42.3%  10.8/25.6 MB  3.21 MB/s  ETA 4m 32s
```
`list.append()` is atomic under CPython's GIL, so no lock is needed despite the download running on a daemon thread and the main thread reading the list in `_show_info_popup`.

The `detail_label` under the progress bar shows the live per-chunk status (updates every chunk, not every 20). It's cleared on completion so the row returns to its normal state.

---

### 12.5 `PillToggle` replaces GlowButton toggles

**Previous design:** Two `GlowButton` instances (`_hl_btn`, `_as_btn`) with text that switched between "ON" and "OFF". The callbacks mutated the button text and color directly.

**New design:** Two `PillToggle` instances (`_hl_toggle`, `_as_toggle`) with `callback=` wired to `_on_hl_toggle` and `_on_as_toggle`. The callback receives `new_state: bool` — no need to infer state from button text.

**Compatibility shims:**
```python
def _toggle_highlight(self):    self._hl_toggle.toggle()
def _toggle_auto_scroll(self):  self._as_toggle.toggle()
```
These keep any keyboard shortcut or external call path working without changes.

**Theme recolor:**
The `_apply_theme_fast()` method that runs on theme switch now updates `_on_col` and `_off_col` on both toggles and calls `_draw()`. The canvas `bg` must also be set to match `C["surface"]` or the corners of the rounded track will show the wrong color (canvas background bleeds through the gaps between the oval end-caps and the rectangle center).

---

### 12.6 `WaveformExportBtn` output path tracking

After the user picks a save path through `TTSSaveDialog`, `self._out_path_var.set(str(path))` updates the Output Path entry field at the bottom of the Export section. This is purely a display convenience — the actual path is held by the `path` local variable in `_export_wav()` / `_export_mp3()` and passed directly to the write functions. The field shows the user where their last export landed without them having to remember the dialog choice.

---

### 12.7 CPU/GPU button label: "iGPU" → "GPU"

All occurrences of the string `"Intel iGPU"` in `ttsvoices.py` and `voice_library.py` were replaced with `"Intel GPU"`. The full label for the toggle when OpenVINO is active is now `"⚡ Intel GPU ✓"`. The internal provider key string `"OpenVINO (Intel iGPU)"` was renamed to `"Intel GPU (OpenVINO)"` in both the `_best_gpu()` return value and the `_finish_init()` provider-restoration check. Both integrated and discrete GPUs expose themselves via ONNX Runtime's `OpenVINOExecutionProvider` — calling them "iGPU" was misleading.

---

### 12.8 Header visual update

The `◈` symbol was replaced with `🔊` (Unicode U+1F50A, SPEAKER WITH THREE SOUND WAVES). The app name now uses `"Segoe UI"` at size 16 bold, which renders cleanly on both GTK/X11 and Wayland compositors. The version subtitle uses `C["muted"]` instead of `C["accent"]` to reduce visual weight — matching the reference screenshot where the version line is visually secondary to the app name.

---

### 12.9 Placeholder text update

`_PLACEHOLDER` changed from:
> "Type or paste text here, or use ⬆ Load File to open a document..."

to:
> "Paste your text here... Or load a file from your collection."

The detection code (`startswith(self._PLACEHOLDER[:30])`) automatically adapts since it slices the first 30 characters. No other changes needed.


---

## 18. Session — April 2026 (v2.2.3 → v2.2.5 Patches)

### 18.1 Bugs Fixed

| File | Bug | Root Cause | Fix |
|------|-----|------------|-----|
| ttsvoices.py | Export bar glows during speech | `_mirror_speak` trace wrote synthesis progress to the unified bar | Removed `_mirror_speak`; bar only receives `_export_progress_var` writes |
| ttsvoices.py | `_set_export_status()` TypeError crash (`fg=` kwarg) | Function signature was `(text, color=None)` but all call sites used `fg=` keyword | Added `fg=None` parameter; both `color=` and `fg=` now accepted |
| ttsvoices.py | File search hides selected file on typo | `_apply_search` reset `_sel_idx=-1` and filtered out the selected file | Track `selected_path`; always keep it in `_vis_items` even if it doesn't match query |
| ttsvoices.py | Folders vanish during search | Only files were kept in filtered list | Added `it["is_dir"] or ...` condition — directories always visible |
| ttsvoices.py | File dialog blank gap at top on open | Canvas `<Configure>` event fired before canvas had real dimensions; yview not at 0 | Triple `_pin_top()` calls at 0ms/50ms/150ms after dialog opens; `_canvas_configured_once` flag resets on each `_go()` |
| ttsvoices.py | File dialog double-scroll on wheel | Canvas had its own `<Button-4/5>` bindings AND window-level `_win_scroll` both fired | `_win_scroll` now checks `"canvas" in str(e.widget)` and skips if true |
| ttsvoices.py | Scroll unit size unpredictable | `yview_scroll(n, "units")` uses 1/10th of scrollregion height — varies with list length | `_scroll_delta` now uses `yview_moveto()` with exact `ROW_H / total_h` fraction math |
| ttsvoices.py | Speed change cancels speech | `_restart_with_new_speed` called `_on_speak` after 150ms; but worker thread not done yet → `_is_speaking=True` → `_on_speak` returned immediately | Replaced fixed delay with polling loop: `_wait_and_restart()` polls `_is_speaking` every 20ms until False, then restores bookmark and calls `_on_speak` |
| ttsvoices.py | Resume starts from wrong position | `_on_speak_complete` ran AFTER `_restart_with_new_speed` set the bookmark, overwriting it via `_save_bookmark()` | `_restart_with_new_speed` now overrides `_stopped_at_chunk` so `_on_speak_complete` saves the correct chunk; bookmark is restored after polling confirms worker exit |
| ttsvoices.py | Manual Speak resumes from old stop point | No distinction between manual Speak press and speed-restart Speak | Added `_speed_restart_pending` flag; manual Speak clears `bookmark_chunk=0, bookmark_file=""` before reading them |
| ttsvoices.py | Stale highlight section on resume | `_scroll_to_resume` added a `resume_marker` tag visible for 2.5s | Removed the visual marker entirely; function now only scrolls, no tag |
| ttsvoices.py | "AMOLED" in top-left subtitle | Label text hardcoded | Changed to `"Unlimited Audio Generation"` |
| ttsvoices.py | "Resume available from chunk N" confusing | Status pill showed chunk number users don't understand | Renamed to `"Resume from previous point"` |
| ttsvoices.py | Bug log Refresh doesn't clear | Refresh re-read the same log file | Added `bug_tracker.clear_log()` which clears both in-memory deque and session file |
| ttsvoices.py | GPU switch dialog appears repeatedly | No tracking of whether dialog was shown this session | Added `cfg["gpu_switch_shown"]` flag; dialog only appears on first switch per session |
| ttsvoices.py | Cancel button in theme picker | No reason to cancel — clicking a swatch already closes | Footer with Cancel button removed |
| voice_library.py | ℹ info button clutters Voice Library | Added in earlier session, user requested removal | Removed from model row UI |
| bug_tracker.py | Log clears but refills on next Refresh | Only the text widget was cleared, not the in-memory `_errors` deque | New `clear_log()` function clears deque + truncates session log file |

### 18.2 The Speed-Change During Speech Bug — Detailed Explanation

This was the most subtle threading bug in the codebase. The synthesis worker runs on a daemon thread. When the user changes speed mid-speech:

**Before fix (broken sequence):**
```
[main]   speed_var changes → _on_cfg_change → after(200, _restart_with_new_speed)
[main]   _restart_with_new_speed():
           chunk_abs = _playing_chunk_abs  # e.g. 4
           cfg["bookmark_chunk"] = 4
           _speed_restart_pending = True
           stop_flag.set()
           audio_handler.stop_playback()
           after(150, self._on_speak)       ← scheduled 150ms from now
[worker] ...still running, hears stop_flag...
[main]   150ms later: _on_speak() fires
           _is_speaking is still True        ← worker not done yet!
           return                             ← SILENT FAILURE
[worker] eventually exits, calls _on_speak_complete()
           _save_bookmark(_stopped_at_chunk) ← overwrites bookmark_chunk with 4
           _is_speaking = False
[main]   nothing calls _on_speak again. Speech stopped, never restarts.
```

**After fix (correct sequence):**
```
[main]   _restart_with_new_speed():
           _stopped_at_chunk = chunk_abs     ← so _on_speak_complete saves right chunk
           _speed_restart_pending = True
           stop_flag.set(), stop_playback()
           after(20, _wait_and_restart)      ← poll, don't guess
[worker] exits, _on_speak_complete() runs:
           _save_bookmark(4)                 ← correct chunk saved ✓
           _is_speaking = False              ← signal to main thread
[main]   _wait_and_restart() sees _is_speaking=False:
           cfg["bookmark_chunk"] = 4         ← restore after _on_speak_complete ran
           _speed_restart_pending = True     ← set again (may have been cleared)
           _on_speak()                       ← NOW safe to call ✓
```

The polling interval is 20ms, max 30 attempts (600ms timeout). In practice, the worker exits in 20-100ms after `stop_playback()`.

### 18.3 File Dialog Scroll — Complete Root Cause Analysis

Three compounding issues caused the blank gap at the top of the file dialog:

**Issue 1: Unpredictable scroll unit size.** `canvas.yview_scroll(n, "units")` scrolls by `n/10` of the scrollregion height. For 10 rows (280px total): 1 unit = 28px = exactly 1 row. For 30 rows (840px total): 1 unit = 84px = 3 rows. For 3 rows (84px total): 1 unit = 8.4px < 1 row. This caused erratic jump distances.

**Fix:** `_scroll_delta` now uses `yview_moveto(cur + rows * ROW_H / total_h)` — exact pixel math, independent of list size.

**Issue 2: Double-scroll on mouse wheel.** The canvas had direct `<Button-4/5>` bindings AND the window-level `_win_scroll` handler also fired for the same events. Every wheel notch scrolled twice.

**Fix:** `_win_scroll` checks `"canvas" in str(e.widget)` and skips if the event originated from the canvas itself.

**Issue 3: Canvas Configure fires before dimensions are final.** When the dialog opens, `<Configure>` fires as the window manager assigns the canvas its real size. Our `_canvas_configured_once` flag reset scroll to 0 on the first Configure — but "first" could be a zero-size Configure before layout settled.

**Fix:** Triple `_pin_top()` calls scheduled at 0ms, 50ms, and 150ms after `grab_set()`. At least one will fire after the final layout pass. `_go()` also resets `_canvas_configured_once = False` on every navigation so the next Configure resets scroll correctly.

### 18.4 Module Sizes (v2.2.5)

| File | Lines | Notes |
|------|-------|-------|
| ttsvoices.py | ~5,100 | Main app — grew with each patch session |
| voices.py | ~1,100 | Engine abstraction, chunking, timing |
| voice_library.py | ~900 | Install/manage engines UI |
| audio_handler.py | ~460 | Playback, export, C bridge |
| file_extractor.py | ~990 | Multi-format extraction |
| bug_tracker.py | ~740 | Structured logging + `clear_log()` |
| odf_crypto.py | ~336 | AES-256-GCM ODT decryption |
| save_point_manager.py | ~91 | Bookmark persistence |

