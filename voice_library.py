"""
TTS Voices 2.5.2 - Voice Library Module

Maintained by the opencode AI assistant — see README.md.
GUI window for downloading and managing voice models.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import os
import urllib.request
from pathlib import Path
import bug_tracker

# COLORS is a live proxy to the main app's theme palette (C dict).
# It is updated in VoiceLibraryWindow.__init__ from the caller's C.
COLORS = {
    "bg":       "#0a0e1a",
    "surface":  "#111827",
    "surface2": "#0d1526",
    "border":   "#1e2d45",
    "border2":  "#223055",
    "accent":   "#1a6cf5",
    "accent2":  "#00d4ff",
    "accent_dim":"#0d3d99",
    "text":     "#e2e8f0",
    "text2":    "#8fa3c4",
    "muted":    "#64748b",
    "success":  "#22c55e",
    "warning":  "#f59e0b",
    "error":    "#ef4444",
    "speak_bg": "#1553d0",
    "speak_hover": "#1d6aff",
}

# Cache for importlib.util.find_spec results (avoids repeating slow imports on every open)
_install_check_cache: dict = {}

MODELS_DIR = Path.home() / ".ttsvoices" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Correct download URLs from kokoro-onnx GitHub releases
# SHA-256 hashes are hardcoded so downloads can be verified before use.
# If a model file is updated upstream, update the hash here and bump the
# version string so existing installs re-download the new file.
# To obtain a fresh hash: sha256sum <file>   or   shasum -a 256 <file>
KOKORO_MODELS = [
    {
        "name":     "Kokoro Model (v1.0)",
        "file":     "kokoro-v1.0.onnx",
        "url":      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
        "size":     "310 MB",
        "desc":     "Primary neural TTS model. Required for all Kokoro voices.",
        "required": True,
        # Verified against the actual upstream release file
        # (325532387 bytes / 310.4 MB).
        "sha256":   "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    },
    {
        "name":     "Voices Data (v1.0)",
        "file":     "voices-v1.0.bin",
        "url":      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
        "size":     "27 MB",
        "desc":     "Voice embeddings for all 11 Kokoro voices.",
        "required": True,
        # Verified against the actual upstream release file
        # (28214398 bytes / 26.9 MB).
         "sha256":   "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    },
# ── Quantized variants (optional) ─────────────────────────────────────────
# Smaller, faster, lower-quality. Useful for low-power U-series CPUs
# where the 310 MB FP32 model is too slow. Download ONE of these in
# place of the FP32 "kokoro-v1.0.onnx" above — the app will use
# whichever one it finds first. SHA-256 hashes are placeholders
# (empty string = skip hash check) so the user must trust the
# first download manually. To enable strict verification, fill in
# the SHA after a clean download:
#   sha256sum ~/.ttsvoices/models/kokoro-v1.0.fp16.onnx
# FP16 model removed — optional and not needed
]


class VoiceLibraryWindow:
    def __init__(self, parent, on_engine_change=None):
        self._on_engine_change = on_engine_change
        # Sync COLORS with the main app's live theme palette
        try:
            import ttsvoices as _tv
            COLORS.update(_tv.C)
        except Exception:
            pass

        self.win = tk.Toplevel(parent)
        self.win.title("Voice Library")
        self.win.geometry("740x580")
        self.win.configure(bg=COLORS["bg"])
        self.win.resizable(True, True)
        self.win.transient(parent)   # share taskbar entry with parent
        self._build()

    def _build(self):
        # Header
        hdr = tk.Frame(self.win, bg=COLORS["surface"], pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Voice Library",
                 font=("Courier New", 14, "bold"),
                 fg=COLORS["accent2"], bg=COLORS["surface"]).pack(side="left", padx=20)
        tk.Label(hdr, text="Manage & download TTS voice models",
                 font=("Courier New", 9), fg=COLORS["muted"],
                 bg=COLORS["surface"]).pack(side="left", padx=8)

        # Models dir info
        tk.Label(self.win,
                 text=f"Models directory: {MODELS_DIR}",
                 font=("Courier New", 8), fg=COLORS["muted"],
                 bg=COLORS["bg"]).pack(anchor="w", padx=16, pady=(8, 0))

        # Tabs
        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=12, pady=8)
        self._build_kokoro_tab(nb)
        self._build_engines_tab(nb)
        self._build_installed_tab(nb)

    def _build_kokoro_tab(self, nb):
        frame = tk.Frame(nb, bg=COLORS["bg"])
        nb.add(frame, text="  Kokoro ONNX  ")

        tk.Label(frame, text="Kokoro ONNX  -  Neural TTS (Apache 2.0)",
                 font=("Courier New", 11, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(frame,
                 text="High-quality offline neural voices. 82M parameters. Runs on CPU.",
                 font=("Courier New", 9), fg=COLORS["muted"],
                 bg=COLORS["bg"]).pack(anchor="w", padx=16, pady=(0, 12))

        self._progress_vars  = {}
        self._status_labels  = {}
        self._detail_labels  = {}   # ℹ popup detail text per model file key
        self._info_logs      = {}   # raw log lines per model file key (list of str)
        self._download_btns  = {}   # Download button widgets per model file key

        for m in KOKORO_MODELS:
            self._model_row(frame, m)

        # Download all button
        btn_frame = tk.Frame(frame, bg=COLORS["bg"])
        btn_frame.pack(pady=20)
        self._download_all_btn = tk.Button(btn_frame,
                  text="  Download All Required  ",
                  font=("Courier New", 10, "bold"),
                  bg=COLORS["accent"], fg="white", relief="flat",
                  padx=16, pady=8, cursor="hand2",
                  command=self._download_all,
                  activebackground="#1d6aff",
                  activeforeground="white")
        self._download_all_btn.pack()
        self._refresh_download_all_btn()

    def _model_row(self, parent, model):
        row = tk.Frame(parent, bg=COLORS["surface"], pady=12, padx=16,
                       highlightthickness=1,
                       highlightbackground=COLORS["border"])
        row.pack(fill="x", padx=16, pady=4)

        left = tk.Frame(row, bg=COLORS["surface"])
        left.pack(side="left", fill="both", expand=True)

        name_row = tk.Frame(left, bg=COLORS["surface"])
        name_row.pack(anchor="w")
        tk.Label(name_row, text=model["name"],
                 font=("Courier New", 10, "bold"),
                 fg=COLORS["text"], bg=COLORS["surface"]).pack(side="left")
        if model.get("required"):
            tk.Label(name_row, text="  required",
                     font=("Courier New", 8),
                     fg=COLORS["warning"], bg=COLORS["surface"]).pack(side="left")

        tk.Label(left, text=model["desc"],
                 font=("Courier New", 8),
                 fg=COLORS["muted"], bg=COLORS["surface"]).pack(anchor="w")

        installed = (MODELS_DIR / model["file"]).exists()
        status_text  = "  Installed" if installed else f"Not installed  ({model['size']})"
        status_color = COLORS["success"] if installed else COLORS["muted"]
        sl = tk.Label(left, text=status_text,
                      font=("Courier New", 8),
                      fg=status_color, bg=COLORS["surface"])
        sl.pack(anchor="w")
        self._status_labels[model["file"]] = sl

        # Progress bar + ℹ info button on same row
        prog_row = tk.Frame(left, bg=COLORS["surface"])
        prog_row.pack(anchor="w", pady=(4, 0), fill="x")

        pv = tk.DoubleVar()
        self._progress_vars[model["file"]] = pv
        ttk.Progressbar(prog_row, variable=pv, maximum=100, length=300).pack(
            side="left")



        # Hidden detail label — shown inline during active download
        dl = tk.Label(left, text="", font=("Courier New", 7),
                      fg=COLORS["muted"], bg=COLORS["surface"], anchor="w", justify="left")
        dl.pack(anchor="w")
        self._detail_labels[model["file"]] = dl
        self._info_logs[model["file"]]     = []

        # Download button (only if not installed)
        if not installed:
            btn = tk.Button(row, text="Download",
                      font=("Courier New", 9),
                      bg=COLORS["accent"], fg="white", relief="flat",
                      padx=10, pady=4, cursor="hand2",
                      command=lambda m=model: self._download_one(m),
                      activebackground="#1d6aff",
                      activeforeground="white")
            btn.pack(side="right", padx=8, anchor="center")
            self._download_btns[model["file"]] = btn

    def _show_info_popup(self, model_key):
        """Open a small Toplevel window showing the raw download event log for
        the given model file key.  Each download tick appends a timestamped line
        to self._info_logs[model_key]; this popup renders them in a read-only
        Text widget so the developer / power user can see progress at byte-level
        granularity (bytes fetched, speed, ETA, final status).

        Design rationale:  the main row only has room for a one-liner status; the
        popup gives unlimited vertical space without cluttering the normal view.
        The window is non-modal so the user can keep monitoring while the download
        runs — each subsequent click reopens / refreshes it.
        """
        log_lines = self._info_logs.get(model_key, [])
        popup = tk.Toplevel(self.win)
        popup.title(f"Download info — {model_key}")
        popup.configure(bg=COLORS["bg"])
        popup.geometry("560x300")
        popup.resizable(True, True)

        tk.Label(popup, text=f"Download log: {model_key}",
                 font=("Courier New", 9, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w", padx=12, pady=(10, 4))

        txt_frame = tk.Frame(popup, bg=COLORS["bg"])
        txt_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        sb = tk.Scrollbar(txt_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(txt_frame, font=("Courier New", 8), bg=COLORS["surface"],
                      fg=COLORS["text"], relief="flat", wrap="word",
                      yscrollcommand=sb.set, state="normal")
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)

        if log_lines:
            txt.insert("end", "\n".join(log_lines))
        else:
            txt.insert("end", "No download activity recorded yet.\n"
                               "Click Download to start, then re-open this panel.")
        txt.config(state="disabled")
        txt.see("end")

    def _refresh_download_all_btn(self):
        """Update the Download All button text/state based on install status."""
        all_installed = all(
            (MODELS_DIR / m["file"]).exists()
            for m in KOKORO_MODELS if m.get("required")
        )
        try:
            if all_installed:
                self._download_all_btn.config(
                    text="  All packages installed  ",
                    state="disabled",
                    bg=COLORS["surface"],
                    fg=COLORS["success"],
                )
            else:
                self._download_all_btn.config(
                    text="  Download All Required  ",
                    state="normal",
                    bg=COLORS["accent"],
                    fg="white",
                )
        except Exception:
            pass

    def _download_one(self, model):
        threading.Thread(target=self._do_download, args=(model,), daemon=True).start()

    def _download_all(self):
        for m in KOKORO_MODELS:
            if not (MODELS_DIR / m["file"]).exists():
                threading.Thread(target=self._do_download, args=(m,), daemon=True).start()

    def _do_download(self, model):
        """Download a single Kokoro ONNX model file from its URL to MODELS_DIR.

        HOW IT WORKS
        ────────────
        1.  Opens an HTTP(S) connection with urllib.  The Content-Length header
            is read once so we can compute a percentage progress.
        2.  Data is read in 128 KB chunks.  After each chunk we:
              a.  Write the bytes to a file on disk.
              b.  Compute speed (bytes/s) from wall-clock elapsed time.
              c.  Compute ETA from remaining bytes / current speed.
              d.  Push a short status string to the status label via
                  root.after() — Tk is not thread-safe so all widget updates
                  MUST go through after().
              e.  Append a timestamped log line to self._info_logs[key].
                  The ℹ popup reads this list; because it's a plain list and
                  CPython's GIL protects list.append() we don't need a Lock.
        3.  On success the Kokoro engine singleton is cleared so the next Speak
            call picks up the newly downloaded file without restarting the app.
        4.  On failure the incomplete file is deleted so a future retry starts
            from scratch (partial ONNX files cause cryptic load errors).
        """
        import time as _time
        dest = MODELS_DIR / model["file"]
        sl   = self._status_labels.get(model["file"])
        pv   = self._progress_vars.get(model["file"])
        key  = model["file"]
        log  = self._info_logs.setdefault(key, [])

        def _log(msg):
            """Append a timestamped line to the per-model info log."""
            ts = _time.strftime("%H:%M:%S")
            log.append(f"[{ts}] {msg}")

        def set_status(txt, color):
            if sl:
                self.win.after(0, lambda t=txt, c=color: sl.config(text=t, fg=c))

        def set_detail(txt):
            dl = self._detail_labels.get(key)
            if dl:
                self.win.after(0, lambda t=txt: dl.config(text=t))

        def set_progress(val):
            if pv:
                self.win.after(0, lambda v=val: pv.set(v))

        # Initialise progress vars up-front so the success-path logging
        # at the bottom never references an unbound local even if the
        # response body is empty / zero-byte / disconnects after headers.
        downloaded = 0
        done_mb    = 0.0
        elapsed    = 0.001
        speed_mb   = 0.0
        chunk_num  = 0
        total      = 0
        total_mb   = 0.0
        dest_tmp   = dest.with_suffix(dest.suffix + ".tmp")
        t_start    = _time.monotonic()

        try:
            _log(f"Starting download: {model['name']}")
            _log(f"URL: {model['url']}")
            set_status("Connecting...", COLORS["warning"])
            set_detail("Connecting…")
            headers = {"User-Agent": "TTSVoices/2.0"}
            req = urllib.request.Request(model["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=None) as resp:
                # NOTE: The file write loop MUST stay inside this `with` block.
                # Once `urlopen` exits, the response is closed and subsequent
                # .read() calls return b"" — which is exactly the bug that made
                # every Kokoro model download report "0 bytes" even when the
                # server was returning 300+ MB of real data via 302 redirects.
                total      = int(resp.getheader("Content-Length", 0))
                total_mb   = total / (1024 * 1024) if total else 0
                _log(f"Content-Length: {total_mb:.2f} MB" if total else "Content-Length: unknown")
                try:
                    with open(dest_tmp, "wb") as f:
                        while True:
                            data = resp.read(131072)  # 128 KB chunks
                            if not data:
                                break
                            f.write(data)
                            downloaded += len(data)
                            chunk_num  += 1
                            elapsed    = max(0.001, _time.monotonic() - t_start)
                            speed_b    = downloaded / elapsed
                            speed_mb   = speed_b / (1024 * 1024)
                            done_mb    = downloaded / (1024 * 1024)
                            if total:
                                pct         = 100 * downloaded / total
                                remaining_b = total - downloaded
                                eta_s = remaining_b / speed_b if speed_b > 0 else 0
                                eta_str = (f"{int(eta_s//60)}m {int(eta_s%60)}s"
                                           if eta_s >= 60 else f"{int(eta_s)}s")
                                status_txt  = (f"  {done_mb:.1f}/{total_mb:.1f} MB"
                                               f"  {speed_mb:.1f} MB/s  ETA {eta_str}")
                                detail_txt  = (f"Chunk {chunk_num}  {pct:.1f}%  "
                                               f"{speed_mb:.2f} MB/s  ETA {eta_str}")
                                set_status(status_txt, COLORS["warning"])
                                set_detail(detail_txt)
                                set_progress(pct)
                                if chunk_num % 20 == 0:
                                    _log(f"Progress: {pct:.1f}%  {done_mb:.1f}/{total_mb:.1f} MB"
                                         f"  {speed_mb:.2f} MB/s  ETA {eta_str}")
                            else:
                                set_status(f"  {done_mb:.1f} MB downloaded...", COLORS["warning"])
                                set_detail(f"Chunk {chunk_num}  {done_mb:.1f} MB")
                                if chunk_num % 20 == 0:
                                    _log(f"Downloaded: {done_mb:.1f} MB (no total)")
                    # After the file is fully written — guard against zero-byte responses
                    if downloaded == 0:
                        raise RuntimeError(
                            "Server returned 0 bytes (empty body or immediate disconnect). "
                            "Check the URL or your network connection.")
                except Exception:
                    # Clean up the partial .tmp file on any download error
                    if dest_tmp.exists():
                        try:
                            os.unlink(dest_tmp)
                        except OSError:
                            pass
                    raise
            set_progress(100)
            _log(f"Download complete: {done_mb:.1f} MB in {elapsed:.1f}s"
                 f" avg {done_mb/elapsed:.2f} MB/s")

            # ── SHA-256 integrity check ───────────────────────────────────
            expected_hash = model.get("sha256", "")
            if expected_hash:
                set_status("  Verifying checksum…", COLORS["warning"])
                set_detail("Checking SHA-256…")
                import hashlib as _hl
                sha = _hl.sha256()
                try:
                    with open(dest_tmp, "rb") as _fv:
                        for _blk in iter(lambda: _fv.read(1 << 20), b""):
                            sha.update(_blk)
                    actual_hash = sha.hexdigest()
                    if actual_hash.lower() != expected_hash.lower():
                        _log(f"CHECKSUM MISMATCH: expected {expected_hash} got {actual_hash}")
                        bug_tracker.error(
                            f"SHA-256 mismatch for {model['file']}. "
                            f"File may be corrupt or tampered with. Deleting."
                        )
                        if dest_tmp.exists():
                            os.unlink(dest_tmp)
                        set_status("  ✗ Checksum failed — file deleted", COLORS["error"])
                        set_detail("Re-download or check your connection")
                        return
                    _log(f"SHA-256 OK: {actual_hash[:16]}…")
                    set_detail("")
                except Exception as _ve:
                    _log(f"Checksum verification error: {_ve}")
                    bug_tracker.warning(f"Could not verify checksum for {model['file']}: {_ve}")
            else:
                _log("No SHA-256 hash configured for this model — skipping verification")

            # ── Atomic rename: only expose the file once it is complete ───
            # os.replace() is atomic on Linux (same filesystem) so no other
            # process ever sees a partial file at the final dest path.
            os.replace(dest_tmp, dest)

            set_status("  ✓ Installed", COLORS["success"])
            set_detail("")
            # Destroy per-model Download button so user cannot re-download
            btn = self._download_btns.pop(key, None)
            if btn:
                self.win.after(0, btn.destroy)
            self.win.after(0, self._refresh_download_all_btn)
            # Invalidate Kokoro singleton so new model is picked up on next Speak.
            # _kokoro_singleton is a dict used as a 1-slot cache in voices.py;
            # clearing it forces re-instantiation which loads the new .onnx file.
            import voices as v_mod
            v_mod._kokoro_singleton.clear()
            bug_tracker.info(f"Downloaded: {model['name']}")
            if self._on_engine_change:
                self.win.after(100, self._on_engine_change)
        except Exception as e:
            _log(f"ERROR: {e}")
            set_status(f"  Failed: {e}", COLORS["error"])
            set_detail("")
            bug_tracker.error(f"Model download failed {model['name']}: {e}")
            # Only clean up the partial .tmp file. NEVER unlink the final
            # `dest` here — it might already contain a valid previous model
            # that survived a post-rename exception (e.g. Tk handle destroyed,
            # bug_tracker hiccup, dropped .after callback). Unlinking the
            # verified-good model in those cases was silently breaking
            # working installs.
            if dest_tmp is not None and dest_tmp.exists():
                try:
                    os.unlink(dest_tmp)
                except OSError:
                    pass

    def _build_engines_tab(self, nb):
        """Engine comparison tab with install/check buttons for each engine."""
        outer = tk.Frame(nb, bg=COLORS["bg"])
        nb.add(outer, text="  Engines  ")

        tk.Label(outer, text="TTS Engine Comparison (2026)",
                 font=("Courier New", 11, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(outer,
                 text="Kokoro is installed and running. Below are other engines you can install for higher quality.",
                 font=("Courier New", 9), fg=COLORS["muted"],
                 bg=COLORS["bg"], wraplength=680, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

        # Scrollable area for engine cards
        canvas = tk.Canvas(outer, bg=COLORS["bg"], highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=0)
        scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                                  bg=COLORS["surface"], width=10, relief="flat")
        scrollbar.place(relx=1, rely=0, relheight=1, anchor="ne")
        canvas.configure(yscrollcommand=scrollbar.set)

        frame = tk.Frame(canvas, bg=COLORS["bg"])
        cw = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cw, width=e.width - 14))

        # ── Physics-based smooth scrolling for the engine list canvas ──────
        _vel   = [0.0]    # kinetic velocity (canvas units/frame)
        _job   = [None]   # after job id
        FRICTION   = 0.84
        VEL_STOP   = 0.02
        STEP_BASE  = 2.5  # canvas units per notch

        def _cancel_job():
            if _job[0]:
                try: self.win.after_cancel(_job[0])
                except Exception: pass
            _job[0] = None

        def _coast():
            _job[0] = None
            _vel[0] *= FRICTION
            if abs(_vel[0]) < VEL_STOP:
                _vel[0] = 0.0
                return
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(1 if _vel[0] > 0 else -1, "units")
            except Exception:
                return
            _job[0] = self.win.after(16, _coast)

        def _scroll(e):
            _cancel_job()
            direction = -1 if (e.num == 4 or getattr(e, "delta", 0) > 0) else 1
            # Ease: immediately scroll a step, then coast the rest
            _vel[0] = direction * STEP_BASE
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(direction * 2, "units")
            except Exception:
                pass
            _job[0] = self.win.after(16, _coast)

        # Traverse-up scroll handler: any event from a nested child widget
        # walks up the widget tree until it finds the canvas and scrolls it.
        # This means the wheel works over buttons, labels, entry widgets etc.
        def _traverse_scroll(e):
            direction = -1 if (e.num == 4 or getattr(e, "delta", 0) > 0) else 1
            widget = e.widget
            while widget:
                if widget is canvas:
                    _scroll(e)
                    return "break"
                try:
                    widget = widget.master
                except Exception:
                    break
            # Fallback: directly scroll the canvas
            _scroll(e)
            return "break"

        canvas.bind("<Button-4>", _traverse_scroll)
        canvas.bind("<Button-5>", _traverse_scroll)
        canvas.bind("<MouseWheel>", _traverse_scroll)
        frame.bind("<Button-4>",  _traverse_scroll)
        frame.bind("<Button-5>",  _traverse_scroll)
        frame.bind("<MouseWheel>", _traverse_scroll)

        ENGINES = [
            {
                "name":    "Kokoro ONNX v1.0",
                "badge":   "✓ INSTALLED",
                "badge_color": COLORS["success"],
                "quality": "★★★★☆",
                "size":    "326 MB",
                "license": "Apache 2.0",
                "desc":    "82M neural TTS. Fastest offline engine.",
                "pro":     "Fast · Offline · CPU-friendly · 11 voices",
                "con":     "Not quite ElevenLabs quality at slow speeds",
                "pip":     None,
                "needs":   "Already installed",
                "installed_check": "kokoro_onnx",
            },
            {
                "name":    "Edge TTS (Cloud)",
                "badge":   "CLOUD",
                "badge_color": COLORS["accent2"],
                "quality": "★★★★★",
                "size":    "0 MB",
                "license": "Microsoft ToS",
                "desc":    "Microsoft Azure Neural voices. ~7-9x faster than Kokoro.",
                "pro":     "Highest quality · Fastest on CPU · 17 voices · No download",
                "con":     "Requires internet · Text sent to Microsoft servers",
                "pip":     "edge-tts",
                "needs":   "Network access to speech.platform.bing.com",
                "installed_check": "edge_tts",
            },
            {
                "name":    "Dia 1.6B",
                "badge":   "High RAM needed",
                "badge_color": COLORS["error"],
                "quality": "★★★★★",
                "size":    "~3.2 GB",
                "license": "Apache 2.0",
                "desc":    "Dialogue model. Ultra-realistic multi-speaker synthesis.",
                "pro":     "Best audiobook quality · Multi-speaker · Apache 2.0",
                "con":     "Very large (3.2GB) · English only · Slow on CPU",
                "pip":     "git+https://github.com/nari-labs/dia.git",
                "needs":   "8GB+ RAM or 6GB+ VRAM",
                "installed_check": "dia",
            },
        ]

        self._engine_status = {}

        for eng in ENGINES:
            self._engine_card(frame, eng)

        tk.Label(frame,
                 text="Note: For this CPU system, Edge TTS (Cloud) is now the recommended primary engine — "
                      "~7-9x faster than Kokoro with higher quality. Kokoro remains the offline fallback.\n"
                      "GPU-accelerated engines (Chatterbox, F5-TTS) require NVIDIA CUDA.",
                 font=("Courier New", 8), fg=COLORS["accent2"],
                 bg=COLORS["bg"], wraplength=660, justify="left").pack(
            anchor="w", padx=16, pady=(10, 4))

        # ── Custom pip install "Add" button ────────────────────────────────
        add_row = tk.Frame(frame, bg=COLORS["bg"])
        add_row.pack(fill="x", padx=16, pady=(0, 16))
        tk.Button(add_row,
                  text="⊕  Add Custom Engine / Package",
                  font=("Courier New", 8, "bold"),
                  bg=COLORS["surface"], fg=COLORS["accent2"],
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  highlightthickness=1, highlightbackground=COLORS["border2"],
                  activebackground=COLORS["border"], activeforeground=COLORS["accent2"],
                  command=lambda: self._open_custom_install_dialog()
                  ).pack(side="right")

        # ── Bind scroll to ALL child widgets so the wheel works everywhere ──
        def _bind_scroll_recursive(widget):
            widget.bind("<Button-4>", _traverse_scroll, "+")
            widget.bind("<Button-5>", _traverse_scroll, "+")
            widget.bind("<MouseWheel>", _traverse_scroll, "+")
            for child in widget.winfo_children():
                _bind_scroll_recursive(child)
        # Also bind at window and outer-frame level for complete coverage
        outer.bind("<Button-4>", _traverse_scroll, "+")
        outer.bind("<Button-5>", _traverse_scroll, "+")
        outer.bind("<MouseWheel>", lambda e: _traverse_scroll(e), "+")
        # Delay so all cards are fully rendered before binding children
        outer.after(100, lambda: _bind_scroll_recursive(frame))
        # Re-bind after 500ms to catch any late-rendered widgets
        outer.after(500, lambda: _bind_scroll_recursive(frame))

    def _engine_card(self, parent, eng):
        """Render one engine card with install/check/remove buttons."""
        import importlib.util, sys

        # Check if installed — cache results to avoid slow repeated find_spec calls
        check = eng.get("installed_check", "")
        if check not in _install_check_cache:
            try:
                _install_check_cache[check] = importlib.util.find_spec(check) is not None
            except Exception:
                _install_check_cache[check] = False
        installed = _install_check_cache.get(check, False)
        # Kokoro is always installed in our context
        if eng["name"].startswith("Kokoro"):
            installed = True

        border_color = COLORS["success"] if installed else COLORS["border"]
        card = tk.Frame(parent, bg=COLORS["surface"], pady=12, padx=16,
                        highlightthickness=1, highlightbackground=border_color)
        card.pack(fill="x", padx=16, pady=5)

        # ── Header row ────────────────────────────────────────────────────
        hdr = tk.Frame(card, bg=COLORS["surface"])
        hdr.pack(fill="x")

        tk.Label(hdr, text=eng["name"],
                 font=("Courier New", 10, "bold"),
                 fg=COLORS["text"], bg=COLORS["surface"]).pack(side="left")
        tk.Label(hdr, text=f"  {eng['badge']}",
                 font=("Courier New", 8, "bold"),
                 fg=eng["badge_color"], bg=COLORS["surface"]).pack(side="left")

        # Stars + size + license on right
        meta = tk.Frame(hdr, bg=COLORS["surface"])
        meta.pack(side="right")
        tk.Label(meta, text=eng["quality"],
                 font=("Courier New", 9),
                 fg=COLORS["warning"], bg=COLORS["surface"]).pack(side="left", padx=(0,8))
        tk.Label(meta, text=f"{eng['size']} · {eng['license']}",
                 font=("Courier New", 8),
                 fg=COLORS["muted"], bg=COLORS["surface"]).pack(side="left")

        # ── Description ───────────────────────────────────────────────────
        tk.Label(card, text=eng["desc"],
                 font=("Courier New", 8),
                 fg=COLORS["text2"], bg=COLORS["surface"],
                 wraplength=640, justify="left").pack(anchor="w", pady=(4, 2))

        # ── Pro/Con row ───────────────────────────────────────────────────
        pc = tk.Frame(card, bg=COLORS["surface"])
        pc.pack(anchor="w")
        tk.Label(pc, text="✓ ", font=("Courier New",8,"bold"),
                 fg=COLORS["success"], bg=COLORS["surface"]).pack(side="left")
        tk.Label(pc, text=eng["pro"],
                 font=("Courier New",8), fg=COLORS["success"],
                 bg=COLORS["surface"]).pack(side="left", padx=(0,16))
        tk.Label(pc, text="✗ ", font=("Courier New",8,"bold"),
                 fg=COLORS["error"], bg=COLORS["surface"]).pack(side="left")
        tk.Label(pc, text=eng["con"],
                 font=("Courier New",8), fg=COLORS["error"],
                 bg=COLORS["surface"]).pack(side="left")

        # ── Requirements + buttons ────────────────────────────────────────
        bot = tk.Frame(card, bg=COLORS["surface"])
        bot.pack(fill="x", pady=(6, 0))

        tk.Label(bot, text=f"Requires: {eng['needs']}",
                 font=("Courier New", 8),
                 fg=COLORS["muted"], bg=COLORS["surface"]).pack(side="left")

        btn_frame = tk.Frame(bot, bg=COLORS["surface"])
        btn_frame.pack(side="right")

        status_var = tk.StringVar(value="✓ Installed" if installed else "")
        status_lbl = tk.Label(btn_frame, textvariable=status_var,
                               font=("Courier New",8,"bold"),
                               fg=COLORS["success"], bg=COLORS["surface"])
        status_lbl.pack(side="left", padx=(0,8))

        if eng["pip"] and not eng["name"].startswith("Kokoro"):
            pip_cmd = eng["pip"]

            # ── Shared mutable state for install/cancel coordination ──────
            _proc_ref        = [None]   # running subprocess (set inside _do_install)
            _install_btn_ref = [None]   # Install button widget
            _cancel_btn_ref  = [None]   # Cancel button widget (hidden until install starts)
            _install_active  = [False]  # prevents double-click
            _cancelled       = [False]  # set by _do_cancel so _do_install knows

            # ── Helper: swap Install ↔ Cancel button visibility ───────────
            def _show_cancel_btn():
                try:
                    if _install_btn_ref[0]:
                        _install_btn_ref[0].pack_forget()
                    if _cancel_btn_ref[0]:
                        _cancel_btn_ref[0].pack(side="left", padx=2)
                except Exception:
                    pass

            def _hide_cancel_btn():
                try:
                    if _cancel_btn_ref[0]:
                        _cancel_btn_ref[0].pack_forget()
                    if _install_btn_ref[0]:
                        _install_btn_ref[0].pack(side="left", padx=2)
                except Exception:
                    pass

            def _switch_to_remove(bf=btn_frame):
                """After successful install: destroy Install+Cancel and show Remove."""
                try:
                    if _install_btn_ref[0]:
                        _install_btn_ref[0].destroy()
                        _install_btn_ref[0] = None
                    if _cancel_btn_ref[0]:
                        _cancel_btn_ref[0].destroy()
                        _cancel_btn_ref[0] = None
                    tk.Button(bf,
                              text="Remove",
                              font=("Courier New", 8),
                              bg=COLORS["surface"], fg=COLORS["error"],
                              relief="flat", padx=10, pady=3,
                              cursor="hand2",
                              highlightthickness=1,
                              highlightbackground=COLORS["error"],
                              command=lambda: threading.Thread(
                                  target=_do_uninstall, daemon=True).start()
                              ).pack(side="left", padx=2)
                except Exception:
                    pass

            def _do_install(cmd=pip_cmd, sv=status_var, sl=status_lbl, c=card, _win=self.win):
                import subprocess, sys, re

                def _ui(fn):
                    """Schedule fn on the Tk main thread. Silently drops if window destroyed."""
                    def _safe():
                        try:
                            fn()
                        except tk.TclError:
                            pass
                        except Exception:
                            pass
                    try:
                        _win.after(0, _safe)
                    except Exception:
                        pass

                # Show Cancel button, hide Install button
                _ui(_show_cancel_btn)

                # ── Create progress widgets on the main thread ─────────────
                pv2     = [None]   # [DoubleVar]
                pip_pb  = [None]   # [Progressbar]
                log_lbl = [None]   # [Label for streaming output]

                def _setup_widgets():
                    try:
                        import tkinter.ttk as _ttk
                        pv2[0] = tk.DoubleVar(value=0)
                        pb = _ttk.Progressbar(c, variable=pv2[0], maximum=100, length=280)
                        pb.pack(fill="x", pady=(4, 1))
                        pip_pb[0] = pb
                        lbl = tk.Label(c, text="Connecting...",
                                       font=("Courier New", 7), fg=COLORS["muted"],
                                       bg=COLORS["surface"], anchor="w")
                        lbl.pack(fill="x", padx=4)
                        log_lbl[0] = lbl
                    except Exception:
                        pass
                _ui(_setup_widgets)

                import time as _time
                _time.sleep(0.12)   # give Tk time to create widgets

                _ui(lambda: sv.set("Installing…"))
                _ui(lambda: sl.configure(fg=COLORS["warning"]))

                try:
                    pip = str(Path(sys.executable).parent / "pip")
                    args = [pip, "install", "--no-cache-dir",
                            "--progress-bar", "off", "--timeout", "120"]
                    if cmd.startswith("git+"):
                        args += [cmd, "--timeout", "300"]
                    else:
                        args.append(cmd)

                    proc = subprocess.Popen(
                        args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )
                    _proc_ref[0] = proc   # expose to _do_cancel

                    # Stream output and detect progress
                    lines_seen = [0]
                    for line in proc.stdout:
                        line = line.strip()
                        if not line:
                            continue
                        lines_seen[0] += 1
                        # Estimate progress from pip's output patterns
                        if "Collecting" in line:
                            _ui(lambda l=line: pv2[0] and pv2[0].set(10))
                            _ui(lambda l=line: log_lbl[0] and log_lbl[0].configure(
                                text=l[:60], fg=COLORS["muted"]))
                        elif "Downloading" in line:
                            _ui(lambda l=line: pv2[0] and pv2[0].set(
                                min(90, (pv2[0].get() if pv2[0] else 0) + 5)))
                            _ui(lambda l=line: log_lbl[0] and log_lbl[0].configure(
                                text=l[:60], fg=COLORS["warning"]))
                        elif "Installing" in line:
                            _ui(lambda: pv2[0] and pv2[0].set(92))
                            _ui(lambda l=line: log_lbl[0] and log_lbl[0].configure(
                                text=l[:60], fg=COLORS["warning"]))
                        elif "Successfully installed" in line:
                            _ui(lambda: pv2[0] and pv2[0].set(100))
                            _ui(lambda l=line: log_lbl[0] and log_lbl[0].configure(
                                text=l[:60], fg=COLORS["success"]))
                        elif "ERROR" in line or "error" in line:
                            _ui(lambda l=line: log_lbl[0] and log_lbl[0].configure(
                                text=l[:60], fg=COLORS["error"]))

                    proc.wait()
                    _proc_ref[0] = None

                    if _cancelled[0]:
                        _ui(lambda: sv.set("Cancelled"))
                        _ui(lambda: sl.configure(fg=COLORS["muted"]))
                    elif proc.returncode == 0:
                        _install_check_cache.pop(eng.get("installed_check", ""), None)
                        _ui(lambda: sv.set("✓ Installed"))
                        if self._on_engine_change:
                            _ui(self._on_engine_change)
                        _ui(lambda: sl.configure(fg=COLORS["success"]))
                        _ui(lambda: c.configure(highlightbackground=COLORS["success"]))
                        _ui(_switch_to_remove)
                        bug_tracker.info(f"Installed: {cmd}")
                    else:
                        _ui(lambda: sv.set("✗ Install failed — see log"))
                        _ui(lambda: sl.configure(fg=COLORS["error"]))
                        bug_tracker.error(f"Install {cmd} failed (exit {proc.returncode})")

                except FileNotFoundError:
                    if not _cancelled[0]:
                        _ui(lambda: sv.set("✗ pip not found"))
                        _ui(lambda: sl.configure(fg=COLORS["error"]))
                except Exception as e:
                    if not _cancelled[0]:
                        _ui(lambda err=str(e): sv.set(f"✗ {err[:50]}"))
                        _ui(lambda: sl.configure(fg=COLORS["error"]))
                        bug_tracker.error(f"Install exception {cmd}: {e}")
                finally:
                    _proc_ref[0] = None
                    _install_active[0] = False
                    was_cancelled = _cancelled[0]
                    def _cleanup():
                        # Destroy BOTH the progress bar AND log label.
                        # Previously only log_lbl was destroyed, leaving the
                        # Progressbar widget in the card. A second Install click
                        # would pack a new bar on top of the orphaned one → double bars.
                        try:
                            if pip_pb[0]:
                                pip_pb[0].destroy()
                                pip_pb[0] = None
                        except Exception: pass
                        try:
                            if pv2[0]: pv2[0].set(100)
                        except Exception: pass
                        if was_cancelled:
                            # Restore install button immediately on cancel
                            _hide_cancel_btn()
                            _remove_log_widget()
                        else:
                            _win.after(5000, lambda: _remove_log_widget())
                    def _remove_log_widget():
                        try:
                            if log_lbl[0]: log_lbl[0].destroy()
                        except Exception: pass
                    _ui(_cleanup)

            def _do_uninstall(pkg=pip_cmd.split("/")[-1].replace(".git",""), sv=status_var, sl=status_lbl, c=card, _win=self.win, _bf=btn_frame):
                import subprocess, sys
                def _ui(fn):
                    def _safe():
                        try:
                            fn()
                        except tk.TclError:
                            pass
                        except Exception:
                            pass
                    try:
                        _win.after(0, _safe)
                    except Exception:
                        pass

                _ui(lambda: sv.set("Uninstalling..."))
                _ui(lambda: sl.configure(fg=COLORS["warning"]))
                try:
                    pip = str(Path(sys.executable).parent / "pip")
                    result = subprocess.run(
                        [pip, "uninstall", "-y", pkg],
                        capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        _ui(lambda: sv.set("Uninstalled — click Install to reinstall"))
                        _ui(lambda: sl.configure(fg=COLORS["muted"]))
                        _ui(lambda: c.configure(highlightbackground=COLORS["border"]))
                        # Invalidate cache so Install button detects fresh state
                        _install_check_cache.pop(eng.get("installed_check", ""), None)
                        # Destroy the Remove button and show a proper working Install button
                        def _swap_to_install():
                            try:
                                for w in _bf.winfo_children():
                                    w.destroy()
                                # Re-wire a full _do_install-backed install button
                                _ri_active  = [False]
                                _ri_cancel  = [False]
                                _ri_proc    = [None]
                                _ri_ibtn    = [None]
                                _ri_cbtn    = [None]

                                def _ri_show_cancel():
                                    try:
                                        if _ri_ibtn[0]: _ri_ibtn[0].pack_forget()
                                        if _ri_cbtn[0]: _ri_cbtn[0].pack(side="left", padx=2)
                                    except Exception: pass

                                def _ri_hide_cancel():
                                    try:
                                        if _ri_cbtn[0]: _ri_cbtn[0].pack_forget()
                                        if _ri_ibtn[0]: _ri_ibtn[0].pack(side="left", padx=2)
                                    except Exception: pass

                                def _ri_do_install():
                                    import subprocess as _sp, sys as _sys, time as _t
                                    _ri_cancel[0] = False
                                    _ui(_ri_show_cancel)
                                    _ui(lambda: sv.set("Installing…"))
                                    _ui(lambda: sl.configure(fg=COLORS["warning"]))
                                    pip = str(Path(_sys.executable).parent / "pip")
                                    cmd = pip_cmd
                                    try:
                                        proc = _sp.Popen(
                                            [pip, "install", "--no-cache-dir",
                                             "--progress-bar", "off", "--timeout", "120", cmd],
                                            stdout=_sp.PIPE, stderr=_sp.STDOUT,
                                            text=True, bufsize=1)
                                        _ri_proc[0] = proc
                                        for line in proc.stdout:
                                            if _ri_cancel[0]: break
                                        proc.wait()
                                        _ri_proc[0] = None
                                        if _ri_cancel[0]:
                                            _ui(lambda: sv.set("Cancelled"))
                                            _ui(lambda: sl.configure(fg=COLORS["muted"]))
                                            _ui(_ri_hide_cancel)
                                        elif proc.returncode == 0:
                                            _install_check_cache.pop(
                                                eng.get("installed_check",""), None)
                                            _ui(lambda: sv.set("✓ Installed"))
                                            if self._on_engine_change:
                                                _ui(self._on_engine_change)
                                            _ui(lambda: sl.configure(fg=COLORS["success"]))
                                            _ui(lambda: c.configure(
                                                highlightbackground=COLORS["success"]))
                                            # Replace install button with Remove
                                            def _to_remove():
                                                try:
                                                    for w in _bf.winfo_children():
                                                        w.destroy()
                                                    tk.Button(_bf, text="Remove",
                                                              font=("Courier New", 8),
                                                              bg=COLORS["surface"],
                                                              fg=COLORS["error"],
                                                              relief="flat", padx=10, pady=3,
                                                              cursor="hand2",
                                                              highlightthickness=1,
                                                              highlightbackground=COLORS["error"],
                                                              command=lambda: threading.Thread(
                                                                  target=_do_uninstall,
                                                                  daemon=True).start()
                                                              ).pack(side="left", padx=2)
                                                except Exception: pass
                                            _ui(_to_remove)
                                        else:
                                            _ui(lambda: sv.set("✗ Install failed"))
                                            _ui(lambda: sl.configure(fg=COLORS["error"]))
                                            _ui(_ri_hide_cancel)
                                    except Exception as e:
                                        _ui(lambda: sv.set(f"✗ {str(e)[:50]}"))
                                        _ui(lambda: sl.configure(fg=COLORS["error"]))
                                        _ui(_ri_hide_cancel)
                                    finally:
                                        _ri_active[0] = False

                                def _ri_cancel_fn():
                                    _ri_cancel[0] = True
                                    p = _ri_proc[0]
                                    if p:
                                        try: p.terminate()
                                        except Exception: pass
                                    _ri_active[0] = False

                                def _ri_start():
                                    if _ri_active[0]: return
                                    _ri_active[0] = True
                                    threading.Thread(target=_ri_do_install, daemon=True).start()

                                ib = tk.Button(_bf, text="⬇ Install",
                                               font=("Courier New", 8, "bold"),
                                               bg=COLORS["accent"], fg="white",
                                               relief="flat", padx=10, pady=3,
                                               cursor="hand2",
                                               activebackground="#1d6aff",
                                               activeforeground="white",
                                               command=_ri_start)
                                ib.pack(side="left", padx=2)
                                _ri_ibtn[0] = ib

                                cb = tk.Button(_bf, text="Cancel",
                                               font=("Courier New", 8, "bold"),
                                               bg=COLORS["error"], fg="white",
                                               relief="flat", padx=10, pady=3,
                                               cursor="hand2",
                                               activebackground="#ff5555",
                                               activeforeground="white",
                                               command=_ri_cancel_fn)
                                _ri_cbtn[0] = cb
                                # Cancel starts hidden
                            except Exception:
                                pass
                        _ui(_swap_to_install)
                    else:
                        _ui(lambda: sv.set("✗ Uninstall failed"))
                        _ui(lambda: sl.configure(fg=COLORS["error"]))
                except Exception as e:
                    _ui(lambda: sv.set(f"✗ {str(e)[:40]}"))

            def _do_cancel():
                """Terminate ongoing pip install and restore the Install button."""
                _cancelled[0] = True
                p = _proc_ref[0]
                if p:
                    try:
                        p.terminate()
                    except Exception:
                        pass
                _proc_ref[0] = None
                _install_active[0] = False

            if not installed:
                def _start_install():
                    if _install_active[0]:
                        return
                    _cancelled[0] = False
                    _install_active[0] = True
                    threading.Thread(target=_do_install, daemon=True).start()

                install_btn_widget = tk.Button(btn_frame,
                          text="⬇ Install",
                          font=("Courier New", 8, "bold"),
                          bg=COLORS["accent"], fg="white",
                          relief="flat", padx=10, pady=3,
                          cursor="hand2",
                          activebackground="#1d6aff",
                          activeforeground="white",
                          command=_start_install)
                install_btn_widget.pack(side="left", padx=2)
                _install_btn_ref[0] = install_btn_widget

                # Cancel button — created but NOT packed until install starts
                cancel_btn_widget = tk.Button(btn_frame,
                          text="Cancel",
                          font=("Courier New", 8, "bold"),
                          bg=COLORS["error"], fg="white",
                          relief="flat", padx=10, pady=3,
                          cursor="hand2",
                          activebackground="#ff5555",
                          activeforeground="white",
                          command=_do_cancel)
                _cancel_btn_ref[0] = cancel_btn_widget

        # pip command display
        if eng["pip"]:
            pip_row = tk.Frame(card, bg=COLORS["surface2"], padx=10, pady=4)
            pip_row.pack(fill="x", pady=(4,0))
            tk.Label(pip_row, text="pip install " + eng["pip"],
                     font=("Courier New", 8),
                     fg=COLORS["accent2"], bg=COLORS["surface2"]).pack(side="left")

    def _open_custom_install_dialog(self):
        """Open a dialog to install any package by pip command."""
        dlg = tk.Toplevel(self.win)
        dlg.title("Add Custom Engine / Package")
        dlg.geometry("620x400")
        dlg.configure(bg=COLORS["bg"])
        dlg.resizable(True, True)
        dlg.transient(self.win)
        dlg.grab_set()

        # Header
        hdr = tk.Frame(dlg, bg=COLORS["surface"], pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="⊕  Install Custom Package",
                 font=("Courier New", 12, "bold"),
                 fg=COLORS["accent2"], bg=COLORS["surface"],
                 padx=16).pack(side="left")

        body = tk.Frame(dlg, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=12)

        tk.Label(body,
                 text="Enter a pip package name or command to install:",
                 font=("Courier New", 9), fg=COLORS["text2"], bg=COLORS["bg"]
                 ).pack(anchor="w")

        entry_frame = tk.Frame(body, bg=COLORS["border"], pady=1, padx=1)
        entry_frame.pack(fill="x", pady=(4, 8))
        cmd_var = tk.StringVar()
        entry = tk.Entry(entry_frame, textvariable=cmd_var,
                         font=("Courier New", 10),
                         bg=COLORS["surface"], fg=COLORS["text"],
                         insertbackground=COLORS.get("accent2", "#00d4ff"),
                         relief="flat")
        entry.pack(fill="x", padx=2, pady=2)
        entry.insert(0, "")
        entry.focus_set()

        tk.Label(body,
                 text="Examples:  omnivoice  |  piper-tts  |  git+https://github.com/user/repo.git",
                 font=("Courier New", 7), fg=COLORS["muted"], bg=COLORS["bg"]
                 ).pack(anchor="w")

        # Output log
        log_frame = tk.Frame(body, bg=COLORS["surface2"], pady=1, padx=1)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        import tkinter.scrolledtext as _st
        log_box = _st.ScrolledText(log_frame,
                                   font=("Courier New", 8),
                                   bg=COLORS["surface2"], fg=COLORS["text2"],
                                   relief="flat", state="disabled", height=8)
        log_box.pack(fill="both", expand=True)

        def _log(msg, color=None):
            def _do():
                try:
                    log_box.configure(state="normal")
                    log_box.insert("end", msg + "\n")
                    if color:
                        # Tag last inserted line
                        start = log_box.index("end-2l")
                        end   = log_box.index("end-1l")
                        tag   = f"col_{color.replace('#','')}"
                        log_box.tag_configure(tag, foreground=color)
                        log_box.tag_add(tag, start, end)
                    log_box.configure(state="disabled")
                    log_box.see("end")
                except Exception:
                    pass
            dlg.after(0, _do)

        # Button row
        btn_row = tk.Frame(body, bg=COLORS["bg"])
        btn_row.pack(fill="x", pady=(8, 0))

        run_active = [False]
        proc_ref   = [None]

        def _run():
            cmd = cmd_var.get().strip()
            if not cmd or run_active[0]:
                return
            run_active[0] = True
            run_btn.configure(state="disabled", text="Running...")
            cancel_btn.configure(state="normal")

            def _worker():
                import subprocess, sys
                pip = str(Path(sys.executable).parent / "pip")
                # Support bare package names and git+ URLs
                if cmd.startswith("git+") or cmd.startswith("http"):
                    args = [pip, "install", "--no-cache-dir", cmd]
                else:
                    args = [pip, "install", "--no-cache-dir", cmd]
                _log(f"▶ pip install {cmd}", COLORS["accent2"])
                try:
                    proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT,
                                            text=True, bufsize=1)
                    proc_ref[0] = proc
                    for line in proc.stdout:
                        line = line.strip()
                        if not line: continue
                        col = COLORS["success"] if "Successfully" in line else \
                              COLORS["error"]   if "ERROR" in line.upper() else \
                              COLORS["warning"]
                        _log(line[:90], col)
                    proc.wait()
                    proc_ref[0] = None
                    if proc.returncode == 0:
                        _log("✓ Done! Restart the app to use the new engine.",
                             COLORS["success"])
                    else:
                        _log(f"✗ Failed (exit {proc.returncode})", COLORS["error"])
                except Exception as e:
                    _log(f"✗ Error: {e}", COLORS["error"])
                finally:
                    run_active[0] = False
                    try:
                        dlg.after(0, lambda: run_btn.configure(
                            state="normal", text="▶ Install"))
                        dlg.after(0, lambda: cancel_btn.configure(state="disabled"))
                    except Exception:
                        pass

            threading.Thread(target=_worker, daemon=True).start()

        def _cancel():
            p = proc_ref[0]
            if p:
                try: p.terminate()
                except Exception: pass
            run_active[0] = False
            try:
                run_btn.configure(state="normal", text="▶ Install")
                cancel_btn.configure(state="disabled")
            except Exception: pass
            _log("Cancelled.", COLORS["muted"])

        run_btn = tk.Button(btn_row, text="▶ Install",
                            font=("Courier New", 9, "bold"),
                            bg=COLORS["accent"], fg="white", relief="flat",
                            padx=14, pady=4, cursor="hand2",
                            activebackground="#1d6aff", activeforeground="white",
                            command=_run)
        run_btn.pack(side="left", padx=(0, 6))

        cancel_btn = tk.Button(btn_row, text="Cancel",
                               font=("Courier New", 9),
                               bg=COLORS["surface"], fg=COLORS["error"],
                               relief="flat", padx=10, pady=4, cursor="hand2",
                               state="disabled",
                               highlightthickness=1,
                               highlightbackground=COLORS["error"],
                               command=_cancel)
        cancel_btn.pack(side="left")

        tk.Button(btn_row, text="Close",
                  font=("Courier New", 9),
                  bg=COLORS["surface"], fg=COLORS["text2"],
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=dlg.destroy).pack(side="right")

        entry.bind("<Return>", lambda _: _run())

    def _build_installed_tab(self, nb):
        frame = tk.Frame(nb, bg=COLORS["bg"])
        nb.add(frame, text="  Installed  ")

        tk.Label(frame, text="Installed Models",
                 font=("Courier New", 11, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(
            anchor="w", padx=16, pady=(14, 8))

        files = sorted(MODELS_DIR.iterdir()) if MODELS_DIR.exists() else []
        if not files:
            tk.Label(frame,
                     text="No models installed yet.\nDownload from the Kokoro ONNX tab.",
                     font=("Courier New", 10), fg=COLORS["muted"],
                     bg=COLORS["bg"], justify="center").pack(pady=60)
        else:
            for f in files:
                row = tk.Frame(frame, bg=COLORS["surface"], pady=8, padx=14,
                               highlightthickness=1,
                               highlightbackground=COLORS["border"])
                row.pack(fill="x", padx=16, pady=3)
                size_mb = f.stat().st_size / (1024 * 1024)
                tk.Label(row, text=f.name,
                         font=("Courier New", 10),
                         fg=COLORS["text"], bg=COLORS["surface"]).pack(side="left")
                tk.Label(row, text=f"{size_mb:.1f} MB",
                         font=("Courier New", 9),
                         fg=COLORS["muted"], bg=COLORS["surface"]).pack(side="right")
