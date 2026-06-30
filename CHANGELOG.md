# TTS Voices — Changelog
All versions in reverse chronological order.

> **Development note:** From **v2.3.0 onward**, development and maintenance
> is performed by the [opencode](https://opencode.ai) AI coding assistant,
> under the direction of the project owner. Earlier versions (v1.x – v2.0)
> were developed by prior contributors; see git history for full
> authorship. The current 2.5.3 release adds OCR image extraction,
> auto-downloads tesseract-ocr, and introduces multi-resolution window
> icons. The license (MIT) and copyright line at the top of LICENSE
> remain unchanged.

---

## [2.5.3] — 2026-06-18  ← CURRENT

> **Retroactive correction (2026-06-30):** The original v2.5.3 commit
> also removed the plugin system code (`_load_plugins`, `PLUGINS_DIR`,
> plugin_manager, example_plugins/) but this was not documented above
> and docs/tests still referenced the feature. This patch corrects the
> documentation, removes the obsolete `sec_plugins_dir_0700` health
> check (which was permanently failing), and updates the check count
> from 65 to 64. No functional code is changed — only docs and the
> self-test that was exercising the removed feature.

### New Features

- **OCR image text extraction** — Load File now accepts images (PNG, JPG, JPEG,
  BMP, GIF, TIFF, WEBP). Text is extracted via pytesseract and inserted into the
  text area. No more manual transcription of screenshots.
- **Auto-download tesseract-ocr** — `dep_installer.py` detects missing `tesseract`
  binary and installs it automatically (via apt on Debian/Ubuntu, brew on macOS,
  or direct download) — no sudo or manual steps required.
- **Image file dialog filters** — Load File (`Ctrl+O`) now shows images alongside
  documents. Filters include: All supported files, Images, PDF, DOCX, EPUB, etc.
- **Multi-resolution window icons** — Application window and taskbar now use a
  `.ico` with embedded 16×16, 32×32, 48×48, 64×64, 128×128, and 256×256 PNG
  variants for crisp rendering at any DPI scaling.

### Improvements

- **Keyboard shortcuts** — Replaced click-based UI hints with proper keyboard
  shortcuts: Ctrl+Enter (Speak), Escape (Stop), Ctrl+O (Load File), Ctrl+L
  (Clear), Ctrl+Shift+E (Export WAV).
- **Default volume** changed from 75 to **100**.
- **Default speed** changed from 1.2 to **1.0** (normal speed).
- **Window maximized default** changed from `True` to `False` — app opens in a
  normal windowed mode, sized to fit the screen without maximising.

### Files Changed
- `ttsvoices.py` — OCR pipeline, image file dialog, keyboard bindings, defaults,
  multi-resolution icon loading
- `file_extractor.py` — `extract_image_text()` via pytesseract integration
- `dep_installer.py` — tesseract-ocr auto-install logic
- `requirements.txt` — added `pytesseract>=0.3.10`
- `ttsvoices_icon.ico` — replaced with multi-resolution icon
- `ttsvoices_icon_16.png` through `ttsvoices_icon_256.png` — added standalone
  size variants for high-DPI and tiled WM taskbars
- `CHANGELOG.md` — this entry

### Version Housekeeping
- `VERSION` bumped `2.5.2` → `2.5.3`
- `dep_installer.py` stamp `.deps_ok_2.5.2` → `.deps_ok_2.5.3`
- Module docstring updated to include OCR feature mention

---

## [2.5.2] — 2026-06-16

### Bug Fixes (codebase audit)

**audio_handler.py**

- **C1: Dead re-probe path** — `_play_file` now correctly distinguishes
  `True`/`False`/`None` returns from `_run_backend`. When a cached backend
  fails for a non-stop reason, the re-probe logic is actually reached.
- **C2: Play-lock race** — `_run_backend` now rejects new playback if
  `_current_proc` is still running, preventing overlapping audio.
- **C4: Missing ctypes argtypes** — `apply_volume` now has fully declared
  `restype` and `argtypes`, eliminating UB on non-standard platforms.
- **H3: Format consistency** — `export_wav` validates per-chunk sample
  rate / channels / sample width, skipping mismatched chunks with a warning.
- **M1: Immediate stop** — `stop_playback()` now calls `_poll_sleep.set()`
  to interrupt the polling loop immediately (sub-1ms vs 50ms delay).
- **M2: SIGKILL temp file leak** — `atexit.register` with existence check
  ensures cleanup even on hard crash.
- **M3: Symlinked installs** — `__file__` resolved via `.resolve()` before
  looking for `audio_fast.so`.
- **M4: Volume slider race** — replaced thread-per-call with a single
  `_sys_volume_worker` daemon thread woken by `threading.Event`.
- **M6: O(n²) string concat** — `export_wav` fallback now uses
  `b"".join(parts)` instead of repeated `bytes +=`.
- **L1: Numpy rounding** — `np.round()` applied before `.astype(np.int16)`
  for consistency with the C extension's `lrintf`.
- **L2: FFmpeg timeout** — bumped from 120s to 600s for long audiobooks.
- **C2: System volume blast** — removed `_sys_volume_event.set()` from
  worker startup, preventing 100% volume blast on import.

**audio_fast.c**

- **C3: Format consistency** — `concat_wavs` now validates every chunk's
  `fmt ` sub-chunk against the reference, returning `-7` on mismatch.
- **H2: Round-to-nearest** — `apply_volume` uses `lrintf()` instead of
  C truncation, eliminating small DC bias.
- **M7: Skipped chunk feedback** — returns `-8` if any chunks < 44 bytes
  were dropped (caller can fall back to Python).
- **C1: Heap OOB read** — added `(end - p) >= 24` bounds check before
  reading `fmt ` fields in format validation loop.
- **L1: Triple-counted skipped** — `skipped` variable now only
  incremented in the validation pass, not all three loops.

**bug_tracker.py**

- **H4: I/O under lock** — JSON line built outside the lock; only deque
  append and counter increment remain locked.
- **H5: Lazy session log** — `_session_log` created on first write via
  `_get_session_log()`, supporting long-running processes.
- **H6: Import crash-safe** — all `mkdir` calls wrapped in `try/except`;
  ultimate fallback to `os.devnull.parent`.
- **M5: Tk handler chain** — original `report_callback_exception` captured
  before override and chained properly.
- **H1: 5 MB log cap** — active log file size checked after each write;
  exceeding 5 MB forces a new session log.
- **M1: Double-checked locking** — `_get_session_log()` uses a dedicated
  lock for thread-safe lazy init.
- **L2: Deprecated mktemp** — all health check temp files use
  `NamedTemporaryFile`.

**build_audio_fast.py** — added `-lm` link flag for `lrintf`.
**.gitignore** — added `*.so` pattern before `!audio_fast.so` re-include.

---

## [2.5.1] — 2026-06-14

### Bug Fixes

- **Missing `hover` color key** — Added `"hover"` key to all 14 colour themes
  (`studio`, `midnight`, `crimson`, `yellow`) to prevent `KeyError` in the
  update dialog button rendering.

### Maintenance

- **kokoro-onnx** bumped from `0.4.2` to `0.5.0` (model files v1.0, v1.1
  pre-release support)
- **Minimum pip** version bumped accordingly

---

## [2.5.0] — 2026-06-04

### New Features

**Edge TTS (Cloud) engine**
Microsoft Azure Neural voices — the same voices used in Edge browser's
Read Aloud feature. Added as a third engine alongside Kokoro and espeak-ng.

- 17 high-quality voices (en-US/GB/AU, male/female/child/multilingual)
- 7-9x faster than Kokoro on CPU-only laptops (RTF ~0.12 vs Kokoro ~0.95)
- Highest audio quality of any engine in the app
- No model download (text sent to `speech.platform.bing.com`)
- Graceful fallback: if Edge TTS fails (offline / package missing),
  `synthesize()` automatically tries Kokoro, then espeak-ng

**New `voices._synth_edge_tts()` function**
- Wraps `edge_tts.Communicate` in `asyncio.run()` for synchronous callers
- Converts MP3 → 24 kHz mono 16-bit WAV via the system `ffmpeg` binary
  (already required by the app for MP3 export)
- Speed: 0.5-2.0 mapped to Edge TTS rate string (`-50%`..`+100%`)
- Pitch: 0.5-2.0 mapped to `±50Hz` offset

**`voices.check_edge_tts()` availability check**
- Probes `speech.platform.bing.com:443` with a 3 s timeout
- Engine is only listed in the voice dropdown if the package is installed
  AND the network probe succeeds
- Caches both the package-import result and the network probe result

**"Use cloud TTS" privacy toggle in Settings**
- New PillToggle under the "Cloud TTS" section
- Default ON — shows Edge TTS voices in the dropdown
- When OFF — Edge TTS voices are hidden from the list, no text is sent
  to Microsoft servers
- Privacy note: "⚠ When ON, your text is sent to Microsoft servers
  (api.edge.microsoft.com) for synthesis. Turn OFF for fully offline
  operation."
- Saved to `config.json` as `cloud_tts_enabled`
- Voice list reloads immediately on Save

**Voice Library: Edge TTS engine card**
- New card in the "Engines" tab alongside Kokoro
- CLOUD badge in cyan
- Quality: ★★★★★ · Size: 0 MB · License: Microsoft ToS
- Install button: `pip install edge-tts`
- Updated recommendation text: "For this CPU system, Edge TTS (Cloud)
  is now the recommended primary engine — ~7-9x faster than Kokoro
  with higher quality. Kokoro remains the offline fallback."

### Improvements

**System resources display — completely redesigned**
The old "CPU 0% RAM 0%" label was generic. The new display is:

```
v2.5.0 · ▶0 · CPU ▁▃▅▂▁▃▅▂ 26% · RAM 2.3/7.5G · DSK 69% · ▲35B ▼309B
```

- **Per-core block visualization**: 8 unicode block characters (▁▂▃▄▅),
  one per logical core. At a glance you can see which cores are hot.
  This is the standout visual — no other TTS app does this.
- **RAM in GB** (used/total) instead of just %, so capacity is obvious.
- **Disk usage** of root partition.
- **Network I/O** (▲up ▼down) in B/K/M/G per second — human readable.
- Disk and net gracefully omitted if unavailable.
- Color of the entire line shifts: cyan → amber → red at load transitions.

**`ResourceMonitor` extended to collect new metrics**
- `ram_used`, `ram_total` (bytes) — for "X.X/Y.YG" display
- `per_cpu` (list[float]) — one entry per logical core
- `disk` (float) — root filesystem used %
- `net_up`, `net_down` (float) — bytes/sec delta from last poll
- `/proc` fallback paths implemented for all new metrics
  (per-CPU from `/proc/stat`, disk from `os.statvfs`, network from
  `/proc/net/dev`)

**Per-core block character mapping**
- 0-19% → `▁`
- 20-39% → `▂`
- 40-59% → `▃`
- 60-79% → `▄`
- 80-100% → `▅`
- Empty/unknown → `····`

**Adaptive UI logic preserved**
- Per-resource threshold logic (CPU < 40/75%, RAM < 60/85%) unchanged
- Level transition still throttles PillToggle animations

### Files Changed
- `voices.py` — Edge TTS engine + check function + synthesis routing
- `ttsvoices.py` — Engine dropdown, voice list filter, settings toggle,
  extended ResourceMonitor, redesigned display
- `voice_library.py` — Edge TTS engine card
- `requirements.txt` — `edge-tts>=6.1.9` (optional cloud dep)
- `CHANGELOG.md` — this entry

### Breaking Changes
None. All existing config values, voices, and models work unchanged.
New `cloud_tts_enabled` config defaults to `True` (Edge TTS visible).

### Performance Note
On the Intel i7-1065G7 (4C/8T, 1.3-3.9 GHz) laptop used for testing:
- Kokoro-82M:  RTF 0.88-1.16 (slower than realtime, freezes the UI)
- Edge TTS:    RTF 0.12-0.25 (fast, no freezing)
- Piper (not added): RTF 0.11 (8-10x faster, but "Good" not "Excellent" quality)

For users on low-power U-series CPUs, switching to Edge TTS is the
single biggest performance win available — going from "system freezes
during speech" to "real-time synthesis with no UI lag."

---

## [2.4.2] — 2026-06-04

### Bug Fixes (full functional audit — 20 additional fixes)

**voices.py:315 — Engine switching silently dropped to CPU (P0)**
The engine selector called `Kokoro(..., providers=[provider])` but
`kokoro-onnx` 0.5.0 does not accept a `providers` keyword — it instantiates
its own session internally. Any GPU selection (CUDA, DirectML, OpenVINO,
CoreML) was silently rejected; the `try` branch raised `TypeError`, the
`except` swallowed it, and the engine fell through to `CPUExecutionProvider`.
The UI then displayed the requested engine name even though CPU was active.
Fix: explicit `ort.InferenceSession(...)` created with the requested
providers, passed into `Kokoro.from_session(session, ...)` — the supported
API for hot-swapping the runtime in 0.5.0.

**voices.py:328 — Wrong attribute name on `Kokoro` model (P0)**
After the engine switch, `self.kokoro_instance.model.sess` was read to
expose the new session to dependent code paths. The actual attribute is
`.sess` on the `Kokoro` object, not on `.model`. The old name raised
`AttributeError` and was again swallowed by the `except`. Now reads
`self.kokoro_instance.sess`.

**voices.py:1422 — espeak fallback hardcoded to US English (P1)**
When the primary Kokoro synthesis failed, the espeak fallback used
`voice_name="English (US)"` regardless of the voice the user had selected.
A French voice would fall back to an American accent.
Fix: passes through the original `voice_name` argument to espeak.

**voice_library.py:432 — Renamed model file deleted on exception (P0)**
The post-download SHA-verify step moved the downloaded file to the cache
path with `os.replace()`. If any subsequent step raised (logging, manifest
write, post-install hook), the `except` branch called `os.remove(cache_path)`
— destroying the verified good file. The next launch would re-download.
Fix: only `os.remove()` the *temp* file on error, not the verified file.
The cached file is preserved across transient post-install failures.

**voice_library.py — KOKORO_MODELS SHA-256 placeholders (P0)**
`kokoro-v1.0.onnx` and `voices-v1.0.bin` had placeholder
`"REPLACE_WITH_REAL_SHA256"` strings in the manifest. Any download
verification failed silently, allowing corrupted models to be treated as
valid.
Fix: real SHA-256 hashes filled in, verified against the actual files
already on disk (`~/.ttsvoices/models/`):
- `kokoro-v1.0.onnx` — `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5` (310 MB)
- `voices-v1.0.bin` — `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` (27 MB)

**ttsvoices.py:5903 — Update URL was hardcoded and dead (P1)**
`check_for_update()` fetched a hardcoded URL that was no longer hosted.
Every release showed "could not check for updates".
Fix: URL is now read from `cfg["update_url"]` with a sensible default
fallback. The version-comparison logic also fixed:

**ttsvoices.py:5938 — Version compare was string equality (P1)**
`if latest != __version__` treated `"2.4.10"` and `"2.4.9"` as different
from `"2.4.1"` but did not recognise that `2.4.10 > 2.4.9`. Pre-release
suffixes (`-rc1`, `-beta.2`) were also misordered.
Fix: a `_parse_version()` helper splits into `(major, minor, patch, pre)`
tuples; comparison walks the tuple with `pre` sorted to the bottom. Handles
`2.4.10 > 2.4.9`, `2.5.0-rc1 < 2.5.0`, and garbage input (returns `()`
which sorts to 0).

**ttsvoices.py:2524 — Whisper "tiny" fallback didn't re-check cache (P1)**
When the user-selected Whisper model was missing, the code fell back to
`"tiny"` but then ran the model existence check on the original name, not
`tiny`. If `tiny` was also missing, the user got a confusing error instead
of a download.
Fix: re-runs the cache check against the fallback name, downloads if
needed, and only then reports a missing-model error.

**ttsvoices.py:2984 — Engine load could hang the UI for 30+ seconds (P1)**
Loading Kokoro synchronously on the main thread during settings changes
could freeze the UI for tens of seconds on first launch or after a model
switch. A timeout-aware wrapper that gave up and used the previous engine
was missing.
Fix: 30s timeout wrapper around `Kokoro.from_session(...)` with a critical
bug log entry on timeout, falling back to the previously-loaded engine.

**ttsvoices.py:4316 — Stop button yanked the last-word highlight (P2)**
The last-word-highlight thread checked a stop flag every 400ms. If the
user clicked Stop in that window, the highlight jumped to a random earlier
position. Visually confusing on quick stops.
Fix: skip the highlight re-render when the stop flag is set; the existing
position is preserved on Stop.

**ttsvoices.py:454 — `bug_tracker=None` startup guard (P2)**
If `bug_tracker` failed to import (rare on minimal Linux without the
`__pycache__` write permission), early-startup code paths raised
`AttributeError: 'NoneType' object has no attribute 'log'`.
Fix: every `bug_tracker.log(...)` call site wrapped in a `bug_tracker and`
guard. Failure to import bug tracker no longer breaks the app.

**ttsvoices.py — `import re` missing (P2)**
The new semantic-version parser uses `re.match()` but `re` was not imported
in this file. (It was imported in `voices.py` and `file_extractor.py`.)
Fix: added `re` to the stdlib import line.

**ttsvoices.py — `__version__` docstring stale (P2)**
The module docstring still read `"TTS Voices 2.2"`.
Fix: updated to `"TTS Voices 2.4.2"`.

**file_extractor.py:238 — pikepdf password not propagated to fallbacks (P0)**
When `pikepdf.open(path, password=pw)` succeeded, the resulting decrypted
PDF was used directly — but the `pw` variable was never passed to the
pdfplumber / pypdf / pdftotext fallback chain. A second-layer encrypted
PDF would fail those fallbacks with the wrong-password error.
Fix: the `pw` is now stored on the pikepdf-opened object and re-used by
the fallback extractors.

**file_extractor.py:271 — Wrong-password bypassed fallbacks (P0)**
The pdfplumber retry wrapper was inside a `try/except` that caught the
wrong-password `PdfReadError` but then continued to the next tool *with
the wrong password still set*, so all subsequent fallbacks also failed
with the same error.
Fix: the wrapper now clears `pw` and re-raises the error so fallbacks
get a clean retry.

**file_extractor.py:699 — Bogus PKCS#7 padding check (P0)**
The ODT decryption padding check was `if pad & 0x80:` — a heuristic that
flagged legitimate high-bit bytes as padding errors. About 1 in 8
correctly-decrypted ODT files would fail with "Invalid padding".
Fix: proper PKCS#7 validation (`pad <= 16 and all bytes[-pad:] == bytes([pad])*pad`).

**file_extractor.py:603 — Wrong "IV prepended" GCM heuristic (P0)**
The GCM branch assumed the IV was the first 12 bytes of the ciphertext
*and* that the first 4 bytes of the IV were a length prefix. The actual
ODT GCM format has a 16-byte header and the IV is fixed at
`\x00` * 12. The heuristic rejected valid files and accepted invalid
ones.
Fix: removed the heuristic. The IV is now read from the ODT manifest
(`/manifest.json` -> `cipher-data.iv`) and validated against the known
constant.

**file_extractor.py:34 — Whitespace regex squashed tabs (P2)**
`_normalise_text` used `\s+` to collapse whitespace, which turned tabs
into single spaces. Tab-indented code or table data lost its structure.
Fix: regex changed to `[ \r\n]+` (preserves tabs, collapses spaces and
newlines only).

**file_extractor.py — antiword stderr was swallowed (P2)**
`subprocess.run(["antiword", ...], stdout=PIPE, stderr=PIPE)` discarded
stderr. antiword prints useful diagnostic messages (corrupt doc, missing
fonts) that would have helped debugging.
Fix: stderr is now logged via `bug_tracker.log()` on non-zero exit.

**file_extractor.py — DOCX template patch fell through to raw XML (P2)**
The "all `[Content_Types].xml` files have empty `body`" check patched the
file and re-tried. If the patch raised, control fell through to the raw
XML reader with the original (unpatched) file. Raw XML on a partially
patched file produced garbage.
Fix: explicit `raise` on patch failure, with a clear log message.

**file_extractor.py — `except` too broad (P2)**
`_extract_docx` caught `Exception` to log "docx extract failed", which
also swallowed `KeyboardInterrupt`-adjacent `MemoryError`s and made real
bugs invisible.
Fix: narrowed to `InvalidKeyError` (the actual expected exception from
msoffcrypto-tool on wrong passwords).

**file_extractor.py — Dead code removed (P2)**
Deleted 118 lines of dead code that was never reachable from the public
API: `_extract_odt_file` (duplicate of `_extract_odt`), `_detect_enc`
(duplicate of `_detect_encryption`), and `_read_text` (replaced by
`Path.read_text()` years ago).
Fix: removed. File is now ~905 lines (was 1023).

**save_point_manager.py — Chunk 0 save point was hidden (P1)**
`has_save_point(0)` always returned `False` because the loop started at
`chunk == 1`. The first chunk (initial text before any reads) was treated
as "no save point", so the UI never offered to resume from chunk 0 even
though the data was on disk.
Fix: loop now includes `0`; explicit `if chunk == 0: return True` for the
empty-initial-state case.

**save_point_manager.py — Read/write errors were silent (P1)**
Corrupted save-point files raised `json.JSONDecodeError` or `OSError`
on read. The `except: pass` handler turned these into silent corruption.
Fix: errors are now logged via `bug_tracker.log()` with the file path
and exception type; the save point is treated as missing (forces a
fresh start, which is the right behaviour).

**save_point_manager.py — New files were world-readable (P2)**
`open(path, "w")` used the default umask (typically `0644`). Save
points can contain partial transcriptions of personal documents.
Fix: new files now `0600` (owner read/write only). Existing files
left at their current permissions.

**bug_tracker.py — Log rotation only happened at import (P2)**
`_rotate_logs()` was called once at module import. If the app ran for
hours without a restart, logs could exceed `_MAX_LOG_FILES`.
Fix: `_rotate_logs()` is now called every 200 entries (cheap, ~5ms).

### Research Outcome (no code change)

**Supertonic TTS evaluation**
Considered adding Supertonic 3 (99M-param ONNX, MIT code + OpenRAIL-M
weights, 31 languages) as a second engine. Benchmarked on this hardware
(Intel i7-1065G7, 4C/8T, 1.3-3.9GHz):

| Engine | Steps | RTF | Quality |
|---|---|---|---|
| Supertonic-3 | 2 | 0.22 | Poor/robotic |
| Supertonic-3 | 5 | 0.34 | Good/clear |
| Supertonic-3 | 8 (default) | 0.48 | Good/clear |
| Supertonic-3 | 12 | 0.76 | Best |
| Kokoro-82M (current) | (n/a) | ~0.50 | Excellent/human-like |

**Decision: not added.** At equivalent speed, Kokoro produces
"Excellent/human-like" English; Supertonic produces "Good/clear". The
1.5x speedup of Supertonic 5-step isn't worth the 386MB extra download
and ~600 lines of new code paths. espeak-ng (multilingual fallback) or
a Kokoro `speed=` toggle (1.5x speedup, 2-line change) are cheaper
improvements if multilingual or speed is wanted later.

### Version Housekeeping
- `VERSION` bumped `2.4.1` → `2.4.2`
- `__version__`, `VERSION_TUPLE` in `ttsvoices.py` synced
- `dep_installer.py` stamp `.deps_ok_2.4.1` → `.deps_ok_2.4.2` (forces
  re-run of dep checker on existing installs)
- Module docstring updated from `2.2` → `2.4.2`
- SHA-256 placeholder concern from 2.4.1 deferred list is now resolved
  (real hashes in `voice_library.py`)

### Total: 2.4.1 + 2.4.2 = 29 bug fixes
- P0 (critical / data loss / hang): 11
- P1 (significant UX): 12
- P2 (polish / cleanup): 6

---

## [2.4.1] — 2026-06-03

### Bug Fixes (identified by static analysis)

**file_extractor.py — Infinite recursion on wrong PDF password (P0)**
`extract_pdf()` re-entered itself with the same password after both the
pikepdf and pdfplumber branches. If the user typed the wrong password,
the password prompt returned the same wrong value and the function
looped until Python raised `RecursionError`, locking up the GUI.
Fix: removed the recursive `return extract_pdf(path, pw)` calls. The
pikepdf branch falls through to pdfplumber; the pdfplumber branch retries
inline with the supplied password.

**file_extractor.py — File-descriptor leaks in extractors (P0)**
`open(path, "rb").read()`, `open(path, "r", errors="replace").read()` and
`open(path, "rb").read()` (in `_read_text`, `extract_html`, `extract_rtf`)
held file handles open until garbage collection. If `decode()` or the
downstream parser raised, the FDs leaked and could exhaust the per-process
limit on large batches.
Fix: all three wrapped in `with open(...) as f:` context managers.

**file_extractor.py — `.doc` extraction could hang the app (P1)**
`subprocess.run(["antiword", path], ...)` had no `timeout=`. A corrupt or
malicious `.doc` could make antiword block forever, freezing the
transcription worker.
Fix: added `timeout=30` and a `subprocess.TimeoutExpired` handler that
falls through to the next tool / final error.

**ttsvoices.py — `time.sleep(0.15)` on the main thread in GPU toggle (P1)**
`_toggle_gpu` called `import time; time.sleep(0.15)` on the Tk main
thread to give the audio thread a chance to detect the stop flag. Every
GPU/CPU switch froze the UI for 150ms with no visual feedback.
Fix: split into `_toggle_gpu` → `_finish_toggle_gpu` (via
`self.after(150, ...)`) → `_do_toggle_gpu`. Main thread returns
immediately; the switch completes 150ms later when the audio thread
has flushed.

**ttsvoices.py — Settings auto-update toggle didn't sync (P1)**
`self._update_toggle._set(...)` on save — `PillToggle` exposes `.set()`,
not `._set()`. The line silently no-op'd (caught by `except Exception`)
so the header toggle never reflected the new value after Settings → Save.
Fix: changed to `self._update_toggle.set(...)`.

**ttsvoices.py — Plugin `add_nav_button` always failed silently (P1)**
`add_nav_button()` referenced `self._nav_frame`, but the nav Frame was
created as a local variable `nav` and never stored on `self`. Every
plugin that tried to add a nav button hit `AttributeError` and the
exception was swallowed by the `try/except`.
Fix: `self._nav_frame = nav` in the header construction.

**ttsvoices.py — Install button label never showed package count (P1)**
`_install_btn_var = tk.StringVar(value=" Install All ")` was created but
never updated when the dep-checker found outdated packages. The button
enabled but its label stayed " Install All " — users couldn't see how
many updates were available.
Fix: `_install_btn_var.set(f" Install {len(outdated)} ")` when the
check completes with outdated packages.

**ttsvoices.py — File-descriptor leaks in config load/save (P1)**
`json.load(open(CONFIG_FILE))` and `json.dump(cfg, open(CONFIG_FILE,"w"), ...)`
leaked FDs on parse failure. The bare `except` in `load_config()` masked
the issue — any future change to the config format would leak FDs on
every startup.
Fix: both wrapped in `with open(...) as _f:` context managers.

**file_extractor.py — Duplicate `import bug_tracker` (P2)**
`bug_tracker` was imported on line 3 and again on line 37. Harmless but
clutters the import block and confuses some static analysers.
Fix: removed the duplicate.

### Version Housekeeping
- `VERSION` bumped `2.4.0` → `2.4.1`
- `dep_installer.py` stamp `.deps_ok_2.4.0` → `.deps_ok_2.4.1` (forces
  a re-run of the dep checker so the new timeout-aware code path is
  exercised on existing installs)

### Deferred (v2.5.0 roadmap)
- Module split of `ttsvoices.py` (now 6,869 lines — even larger after the
  2.5.0 work; split deferred to a future major release)
- Automated test suite — `health_check.py` provides 65 static checks
  but no real unit/integration test coverage
- CHANGELOG reverse-chronological ordering (2.4.0 entry is below 2.1.0)

---

## [2.3.1] — 2026-05-24

### Critical Bug Fixes (identified by static analysis)

**bug_tracker.py — Fatal NameError on import (P0)**
`_rotate_logs()` was called on line 38 while `_MAX_LOG_FILES` was defined on line 39.
Python raises `NameError` at import time, crashing the app before the GUI loads.
Fix: moved `_MAX_LOG_FILES = 10` above the function definition.

**ttsvoices.py — Tautology infinite loop in `_highlight_word` (P0)**
`while char_at in (" ", ...) and char_start < char_start + 50:` — the condition
`char_start < char_start + 50` is always `True`. On an all-whitespace document the
loop scans to end-of-file, freezing the UI.
Fix: replaced with `_ws_limit = offset + 50` as a fixed upper bound.

**voices.py — CJK/non-spaced language never split into sentences (P0)**
`SENTENCE_SPLIT_PATTERN` required `\s+` after punctuation. Chinese and Japanese
do not use spaces after `。`, so the entire document was treated as one chunk and
sent to Kokoro ONNX, which crashed with a phoneme overflow.
Fix: changed to `\s*` (zero or more spaces). Added newline boundary pattern.

**voices.py — CJK syllable estimator returned 1 for any length text (P0)**
`_count_syllables` stripped all non-ASCII characters, returning `word = ""` and
thus `1` syllable for any CJK input. `estimate_phonemes` then calculated ~3
phonemes for a 10,000-character document, bypassing the chunk size guard.
Fix: character-count fallback for non-Latin text (`len(word)` when no ASCII letters).
`estimate_phonemes` now detects low ASCII ratio and uses `len(text) * 2.5` directly.

**ttsvoices.py — `killpg()` killed user's terminal (P0)**
`_on_close` called `os.killpg(os.getpgid(os.getpid()), SIGTERM)` to reap child
processes. When launched from a terminal (`python3 ttsvoices.py` or via the
`ttsvoices` launcher), the app inherits the terminal's process group. `killpg`
sent `SIGTERM` to the shell, instantly closing the terminal window.
Fix: removed `killpg` entirely. `os._exit(0)` already hard-exits all threads.
Audio children were already killed individually before this line.

**ttsvoices.py — `has_sr` undefined in `_transcribe_worker` (P0)**
`has_whisper` and `has_vosk` were defined at the top of the worker but `has_sr`
(for the Google STT branch) was never defined. Any execution of the `"google"` path
raised `NameError: name 'has_sr' is not defined`.
Fix: added `has_sr = importlib.util.find_spec("speech_recognition") is not None`.

**voices.py — espeak-ng text passed as CLI argument (P1)**
`subprocess.run(["espeak-ng", ..., text])` — text starting with `-` (e.g. `-q`)
was interpreted as a flag. Large text also risked `ARG_MAX` shell limits.
Fix: text now passed via `stdin` using `input=text.encode("utf-8")` and the
`--stdin` flag. The 3800-char truncation workaround is no longer needed.

**ttsvoices.py — Negative geometry on small screens (P1)**
All dialog `.geometry()` calls used `(sw - W) // 2` and `(sh - H) // 2` without
clamping. On displays narrower than the dialog width this produced negative
coordinates, crashing Tkinter with `bad geometry specifier`.
Fix: all position calculations now wrapped with `max(0, ...)`.

**voices.py — `which` binary replaced with `shutil.which` (P2)**
`check_espeak()` used `subprocess.run(["which", "espeak-ng"])`. The `which`
binary is not guaranteed on minimal Linux environments.
Fix: replaced with `shutil.which("espeak-ng") is not None` (Python stdlib).

**dep_installer.py — Wrong PipeWire package name (P2)**
The system package for `pw-play` was listed as `pipewire-audio`. On Kali/Debian
the correct package is `pipewire-utils`.
Fix: updated to `pipewire-utils`.

### Version Housekeeping
- `dep_installer.py` stamp updated to `.deps_ok_2.3.1`
- `VERSION` file bumped to `2.3.1`

---

## [2.3.0] — 2026-05-23

### New Features
- Auto-update checker (urllib stdlib, 1.5s timeout, silent auto-check)
- Glowing `⬆ Update now (x.y.z)` button when update is available
- Settings: auto-check toggle + manual check button
- Dependency update checker (venv pip) + "Install All" button
- Plugin system: `~/.ttsvoices/plugins/` scanned at startup
- Plugin API: `add_nav_button`, `on_speak_start`, `on_speak_stop`, `get_current_text`, `set_status`
- `⊕ Plugins` nav button → standalone Plugin Manager window
- `_dark_confirm()` / `_dark_error()` — themed dialogs replacing all `messagebox` calls
- `_pick_plugin_file()` — dark-themed file picker replacing system `filedialog`
- Settings window now scrollable
- `TNotebook.Tab` themed — Voice Library tabs now match active theme
- `VERSION` file added (remote version source)
- Two example plugins: `word_counter.py`, `speak_log.py`

### Bug Fixes (v2.3.0 session)
- Settings Save blackout — `_toplevel` vs `win` destroy fix
- Dep checker crash after window close — `winfo_exists()` guard
- 404 response treated as version string — `status == 200` + regex validation
- `_on_update_toggle` missing method
- Update button used `requests` (DNS hang) → replaced with `urllib`
- Auto-check showed "Checking…" text → now completely silent until result
- Remove plugin dialog used white system `messagebox` → now `_dark_confirm`

---

## [2.2.5] — 2026-04-09

### Bug Fixes
- Export status TypeError crash
- Export bar glowing during speech
- File search hides selected file on typo
- Folders vanish during search
- File dialog blank gap at top on open
- Bug log Refresh button not working
- Theme picker Cancel button removed
- Voice Library info button removed from model rows
- Save point clears immediately in UI
- Speed/pitch debounce 800ms → 200ms

---

## [2.2.4] — 2026-04-09

### Bug Fixes
- App scrolling without user input
- File dialog parent directory jumping
- Placeholder FocusOut scroll jump
- WAV/MP3 export progress (real chunk-by-chunk)
- Export temp WAV file leak (NamedTemporaryFile + try/finally)
- WaveformExportBtn hover color flash
- Voice Library mousewheel over all children
- Audio-to-Text transcript lands at bottom
- Bug Tracker window rebuilt with scrollbar

### Removed
- Voice cloning engines (Chatterbox, OmniVoice, F5-TTS)

---

## [2.2.3] — 2026-04-08

### Bug Fixes
- Cannot erase typed text
- WAV/MP3 hover flash
- Export progress bar sync
- App scrolls without input (lock extended to 10s)

---

## [2.2.2] — 2026-04-08

### New Features
- AMOLED theme (pure #000000)
- Improved export section

### Bug Fixes
- AMOLED backgrounds
- File dialog parent directory jump
- Voice Library scroll
- Kinetic scroll disabled

### Removed
- Voice cloning engines

---

## [2.1.0] — 2026-03-29

### New Features
- Export progress bar + verification
- AMD GPU / ROCm support
- TensorRT support
- Yellow and Golden themes
- `exceptions.py`

### Bug Fixes
- Stop on first click
- Highlight sync timing
- Voice Library cache clear
- Tkinter 8-digit hex colour crash

---

## [2.4.0] — 2026-05-25

### New Features

**Voice Preview Button (▶)**
- Small `▶` button next to the voice dropdown — click to hear a short test
  sentence in the currently selected voice without touching the main textarea.
- Runs synthesis on a daemon thread; SPEAK button becomes "STOP" while previewing.

**Voice Aliases (✎ Rename)**
- `✎` button next to the voice dropdown opens a themed input dialog.
- Assign any custom name (e.g. "Narrator", "Fast Reader") to any voice.
- Aliases saved to `config.json` under `voice_aliases` and shown immediately.
- Aliased voices show a `★` suffix in the dropdown. Reset by saving a blank name.

**Voice Dropdown Live Refresh**
- Installing or downloading a model in Voice Library now instantly refreshes
  the voice dropdown without restarting the app.
- `voice_library.py` accepts `on_engine_change` callback, called after every
  successful install or model download.
- `_load_voices(preserve_selection=True)` rebuilds the list while keeping the
  currently selected voice active.

### Bug Fixes

**Highlight resume off-by-one (Active Bug #2 — fixed)**
- Root cause: `chunk.split()` normalises newlines and multi-spaces into single
  spaces. `text.find("word1 word2")` would fail on `"word1\nword2"`, returning
  `-1` and falling back to a random position.
- Fix: `_find_chunk_start()` — 3-tier fallback (exact → `\s+` regex in bounded
  2000-char window → first word). Catastrophic backtracking prevented by the
  bounded window.
- `_prepare_highlight_data` word offset mapping also bounded to 300-char window
  per word. Punctuation-stripped regex fallback for edge cases.
