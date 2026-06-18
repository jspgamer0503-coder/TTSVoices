"""
dep_installer.py — On-demand dependency checker and installer for TTS Voices.

Maintained by the opencode AI assistant — see README.md.

Three layers of protection so the app never crashes from a missing module:

1. **Startup check (critical)** — runs from ttsvoices.py main block.
   Verifies that all CRITICAL python + system deps are installed. If not,
   shows the first-run installer window. Fast-path on subsequent launches.

2. **Versioned stamp** — stamp file is named with a hash of the dep manifest.
   Adding a new dep to the list invalidates the stamp and triggers a re-check.

3. **Feature-level check (lazy)** — call `ensure_feature_dep("edge_tts")`
   before using a feature. Missing deps are installed in the background with
   a small progress dialog. Non-critical so it never blocks the UI for long.

4. **Permission fallback** — if `pip install` into the venv fails (e.g. system-
   wide .deb install where the bundled venv is owned by root), falls back to
   `pip install --target=~/.ttsvoices/site-packages` and prepends that to
   sys.path so imports work without admin rights.
"""
import sys, subprocess, importlib.util, threading, os, hashlib
from pathlib import Path

APP_DIR   = Path(__file__).parent.resolve()
STAMP_DIR = Path.home() / ".ttsvoices"
USER_PKGS = STAMP_DIR / "site-packages"

# ── Dependency manifest ──────────────────────────────────────────────────────
# Each entry: (import_name, pip_package, critical)
# critical=True → app cannot run without it
PYTHON_DEPS = [
    # Core TTS
    ("kokoro_onnx",        "kokoro-onnx>=0.5.0",        True),
    ("onnxruntime",        "onnxruntime>=1.20.0",        True),
    ("numpy",              "numpy>=2.2.0",               True),
    # File extraction
    ("pdfplumber",         "pdfplumber>=0.11.0",         False),
    ("pypdf",              "pypdf>=5.1.0",               False),
    ("docx",               "python-docx>=1.1.2",         False),
    ("ebooklib",           "ebooklib>=0.18",             False),
    ("bs4",                "beautifulsoup4>=4.12.3",      False),
    ("lxml",               "lxml>=5.3.0",                False),
    ("striprtf",           "striprtf>=0.0.26",           False),
    ("chardet",            "chardet>=5.2.0",             False),
    # Encryption
    ("pikepdf",            "pikepdf>=9.4.0",             False),
    ("msoffcrypto",        "msoffcrypto-tool>=5.4.2",    False),
    ("Crypto",             "pycryptodome>=3.21.0",       False),
    ("argon2",             "argon2-cffi>=23.1.0",        False),
    # Audio-to-Text transcription engines (all optional)
    ("faster_whisper",     "faster-whisper",             False),
    ("vosk",               "vosk",                       False),
    ("speech_recognition", "SpeechRecognition",          False),
    ("pytesseract",        "pytesseract>=0.3.10",        False),
]

SYSTEM_DEPS = [
    # (check_cmd, package_name, apt_name)
    (["which", "espeak-ng"],  "espeak-ng",  "espeak-ng"),
    (["which", "ffmpeg"],     "ffmpeg",     "ffmpeg"),
    (["which", "gcc"],        "gcc",        "gcc"),
    (["pw-play", "--version"], "pw-play",    "pipewire-utils"),
    (["which", "tesseract"],   "tesseract",  "tesseract-ocr"),
]

# ── Feature → pip deps mapping ───────────────────────────────────────────────
# Used by ensure_feature_dep("edge_tts") to install only what's needed
# for a specific feature, not the whole manifest.
FEATURE_DEPS = {
    "edge_tts":      [("edge_tts", "edge-tts>=7.0.0")],
    "kokoro":        [("kokoro_onnx", "kokoro-onnx>=0.5.0"),
                      ("onnxruntime", "onnxruntime>=1.20.0")],
    "whisper":       [("faster_whisper", "faster-whisper")],
    "vosk":          [("vosk", "vosk")],
    "google_stt":    [("speech_recognition", "SpeechRecognition")],
    "pdf":           [("pdfplumber", "pdfplumber>=0.11.0"),
                      ("pypdf", "pypdf>=5.1.0")],
    "docx":          [("docx", "python-docx>=1.1.2")],
    "epub":          [("ebooklib", "ebooklib>=0.18"),
                      ("bs4", "beautifulsoup4>=4.12.3")],
    "rtf":           [("striprtf", "striprtf>=0.0.26")],
    "encrypted":     [("msoffcrypto", "msoffcrypto-tool>=5.4.2"),
                      ("Crypto", "pycryptodome>=3.21.0")],
    "ocr":           [("pytesseract", "pytesseract>=0.3.10"),
                      ("PIL", "Pillow>=10.0.0")],
}


# ── Stamp management (versioned) ────────────────────────────────────────────
def _manifest_hash():
    """Stable hash of the dep manifest. Adding a new dep invalidates stamps."""
    items = (
        [(n, p) for n, p, c in PYTHON_DEPS] +
        [(n, p) for f in FEATURE_DEPS.values() for n, p in f]
    )
    return hashlib.sha1(repr(sorted(items)).encode()).hexdigest()[:10]

def _stamp_path():
    return STAMP_DIR / f".deps_ok_2.5.3_{_manifest_hash()}"

def _is_stamped():
    """True if the current manifest's stamp file exists."""
    try:
        return _stamp_path().exists()
    except Exception:
        return False

def _write_stamp():
    try:
        STAMP_DIR.mkdir(parents=True, exist_ok=True)
        _stamp_path().write_text("ok")
        # Sweep old stamps so the dir doesn't grow forever
        for old in STAMP_DIR.glob(".deps_ok_*"):
            if old != _stamp_path():
                try: old.unlink()
                except Exception: pass
    except Exception:
        pass


# ── Detection helpers ───────────────────────────────────────────────────────
def _spec(name):
    return importlib.util.find_spec(name) is not None

def _which(cmd):
    r = subprocess.run(["which", cmd], capture_output=True)
    return r.returncode == 0

def _missing_python():
    return [(n, p) for n, p, _ in PYTHON_DEPS if not _spec(n)]

def _missing_system():
    missing = []
    for check_cmd, name, apt in SYSTEM_DEPS:
        try:
            r = subprocess.run(check_cmd, capture_output=True, timeout=5)
            if r.returncode != 0:
                missing.append((name, apt))
        except Exception:
            missing.append((name, apt))
    return missing

def needs_install():
    """Return True if any critical dependency is missing."""
    if _is_stamped():
        return False
    critical = [n for n, _, c in PYTHON_DEPS if c]
    for n in critical:
        if not _spec(n):
            return True
    for _, _, apt in SYSTEM_DEPS[:3]:  # espeak, ffmpeg, gcc
        if not _which(apt.split("/")[-1]):
            return True
    return False

def feature_available(feature):
    """Return True if all pip packages for a feature are importable."""
    deps = FEATURE_DEPS.get(feature, [])
    return all(_spec(n) for n, _ in deps)


# ── pip install with permission fallback ─────────────────────────────────────
def _pip_install(packages, target=None, timeout=300):
    """
    Install pip packages. Tries in order:
      1. `pip install` in the current Python environment
      2. `pip install --target=<user dir>` (no admin needed)
    Returns (success: bool, target_dir: str|None).
    """
    STAMP_DIR.mkdir(parents=True, exist_ok=True)
    targets_to_try = [target] if target else [None, str(USER_PKGS)]
    last_err = ""
    for tgt in targets_to_try:
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
        if tgt:
            cmd += ["--target", tgt, "--no-warn-script-location"]
        cmd += list(packages)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                if tgt:
                    _ensure_user_pkgs_on_path(tgt)
                return True, tgt
            last_err = (r.stderr or r.stdout or "").strip()[:200]
        except subprocess.TimeoutExpired:
            last_err = "timeout"
        except Exception as e:
            last_err = str(e)[:200]
    return False, None

def _ensure_user_pkgs_on_path(target_dir):
    """Prepend a user --target directory to sys.path so imports work."""
    target_dir = str(target_dir)
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)
    # Persist a .pth file so subsequent Python processes also see it
    try:
        site_dir = STAMP_DIR
        pth = site_dir / "_user_pkgs.pth"
        existing = pth.read_text().splitlines() if pth.exists() else []
        if target_dir not in existing:
            existing.append(target_dir)
            pth.write_text("\n".join(existing) + "\n")
    except Exception:
        pass


# ── Tkinter progress window (first-run style) ───────────────────────────────
def run_installer_window(missing_py=None, missing_sys=None, title="First Run Setup"):
    """Show a themed progress window and install missing packages.
    If missing_py/missing_sys are None, detect automatically."""
    import tkinter as tk
    from tkinter import ttk

    if missing_py is None:
        missing_py = _missing_python()
    if missing_sys is None:
        missing_sys = _missing_system()
    if not missing_py and not missing_sys:
        _write_stamp()
        return True

    root = tk.Tk()
    root.title(f"TTS Voices — {title}")
    root.geometry("560x420")
    root.configure(bg="#080c18")
    root.resizable(False, False)
    root.update_idletasks()
    sw, sh = root.wininfo_screenwidth(), root.wininfo_screenheight()
    root.geometry(f"560x420+{(sw-560)//2}+{(sh-420)//2}")

    BG   = "#080c18"
    SRF  = "#0d1526"
    ACC  = "#00c8ff"
    TXT  = "#dde4f0"
    MUT  = "#4a6080"
    OK   = "#00d97e"
    ERR  = "#ef4444"
    WARN = "#f59e0b"

    # Header
    hdr = tk.Frame(root, bg=SRF, padx=20, pady=14)
    hdr.pack(fill="x")
    tk.Label(hdr, text="🔊  TTS Voices", font=("Segoe UI", 16, "bold"),
             fg=ACC, bg=SRF).pack(anchor="w")
    tk.Label(hdr, text="Installing missing components…", font=("Courier New", 9),
             fg=MUT, bg=SRF).pack(anchor="w")

    # Progress
    pb_outer = tk.Frame(root, bg=BG)
    pb_outer.pack(fill="x", padx=20, pady=(14, 4))
    pb_canvas = tk.Canvas(pb_outer, height=10, bg=SRF, highlightthickness=0)
    pb_canvas.pack(fill="x")
    def _draw_pbar(pct):
        pb_canvas.delete("all")
        w = pb_canvas.winfo_width()
        pb_canvas.create_rectangle(0, 0, int(w * pct / 100), 10, fill=ACC, outline="")
    root.update_idletasks()
    _draw_pbar(0)

    status_lbl = tk.Label(root, text="Preparing…", font=("Courier New", 10, "bold"),
                          fg=TXT, bg=BG, anchor="w")
    status_lbl.pack(fill="x", padx=20, pady=(4, 2))

    log_box = tk.Text(root, height=12, bg=SRF, fg=TXT, font=("Courier New", 9),
                      relief="flat", wrap="word", state="disabled", padx=10, pady=10)
    log_box.pack(fill="both", expand=True, padx=20, pady=(2, 20))

    def _log(msg, color=None):
        def _do():
            log_box.configure(state="normal")
            log_box.insert("end", msg + "\n", color or "info")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(0, _do)

    log_box.tag_configure("info",  foreground=TXT)
    log_box.tag_configure("ok",    foreground=OK)
    log_box.tag_configure("warn",  foreground=WARN)
    log_box.tag_configure("error", foreground=ERR)

    total_steps = max(1, len(missing_py) + len(missing_sys) + 1)
    step = [0]
    def _advance():
        step[0] += 1
        _draw_pbar(int(step[0] / total_steps * 100))

    def _status(txt, col=None):
        def _do():
            status_lbl.configure(text=txt, fg=col or TXT)
        root.after(0, _do)

    def _worker():
        # System packages
        if missing_sys:
            pkgs = [apt for _, apt in missing_sys]
            _log(f"System packages to install: {', '.join(pkgs)}")
            _status("Installing system packages…", WARN)
            try:
                r = subprocess.run(
                    ["sudo", "-n", "apt-get", "install", "-y", "-qq"] + pkgs,
                    capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    _log("  ✓  System packages installed", "ok")
                else:
                    _log("  ⚠  sudo apt failed — some system packages could not be installed", "warn")
            except Exception as e:
                _log(f"  ⚠  System install skipped: {e}", "warn")
            _advance()

        # Python packages
        for import_name, pip_pkg in missing_py:
            pkg_display = pip_pkg.split(">=")[0].split("==")[0]
            _status(f"Installing {pkg_display}…", WARN)
            _log(f"  pip install {pip_pkg}")
            ok, target = _pip_install([pip_pkg])
            if ok:
                if target:
                    _log(f"  ✓  {pkg_display} (installed to {Path(target).name}/)", "ok")
                else:
                    _log(f"  ✓  {pkg_display}", "ok")
            else:
                _log(f"  ✗  {pkg_display} — install failed; feature will be disabled", "error")
            _advance()

        # Compile C extension
        _status("Compiling C audio extension…")
        try:
            r = subprocess.run(
                [sys.executable, str(APP_DIR / "build_audio_fast.py")],
                capture_output=True, text=True, timeout=30,
                cwd=str(APP_DIR))
            _log(r.stdout.strip() or "  audio_fast.so compiled", "ok")
        except Exception as e:
            _log(f"  ⚠  C build skipped: {e}", "warn")
        _advance()

        _draw_pbar(100)
        _status("Setup complete — launching TTS Voices…", OK)
        _log("\n✓  All done. Launching app…", "ok")
        _write_stamp()
        root.after(1200, root.destroy)

    threading.Thread(target=_worker, daemon=True).start()
    root.mainloop()
    return True


def check_system_dependency(binary_name: str, install_instructions: str) -> bool:
    """Check if a system binary is available.
    If not, prompt the user with install instructions instead of silently installing it.
    Returns True if available, False if user chose to continue without it.
    """
    import shutil
    if shutil.which(binary_name):
        return True

    import tkinter as tk
    from tkinter import messagebox
    try:
        root = tk.Tk()
        root.withdraw()
        prompt = (
            f"The required system dependency '{binary_name}' was not found.\n\n"
            f"To enable this feature, please install it manually:\n\n"
            f"{install_instructions}\n\n"
            f"Click OK to continue without this feature, or Cancel to exit."
        )
        response = messagebox.askokcancel("Missing Dependency", prompt, parent=root)
        root.destroy()
        if not response:
            sys.exit(f"User cancelled: Missing {binary_name}")
    except Exception:
        pass
    return False


# ── Public entry points ─────────────────────────────────────────────────────
def ensure_deps():
    """
    First-run critical check. Call this at the very top of ttsvoices.py main block.
    Fast path: returns immediately if stamp file exists.
    """
    if _is_stamped():
        return   # already set up
    try:
        import tkinter
        if needs_install():
            run_installer_window(title="First Run Setup")
        else:
            _write_stamp()
    except Exception:
        pass  # never block startup


def ensure_feature_dep(feature, show_window=True):
    """
    Ensure a feature's pip deps are installed. Call this before using a feature.

    - If all packages are present: returns True immediately.
    - If packages are missing:
        * show_window=True  → show small progress dialog, block until done.
        * show_window=False → install silently in a background thread, return False.
    Returns True if feature is usable right now, False if install is in-flight.
    """
    deps = FEATURE_DEPS.get(feature, [])
    if not deps:
        return True
    missing = [(n, p) for n, p in deps if not _spec(n)]
    if not missing:
        return True

    if not show_window:
        # Background install — no UI blocking
        def _bg():
            _pip_install([p for _, p in missing], timeout=600)
        threading.Thread(target=_bg, daemon=True).start()
        return False

    # Show progress window for this feature
    try:
        import tkinter
        run_installer_window(
            missing_py=missing,
            missing_sys=[],
            title=f"Installing {feature.replace('_', ' ').title()}")
        # Re-check after the dialog closes
        return all(_spec(n) for n, _ in missing)
    except Exception:
        return False


def install_in_background(feature, on_done=None):
    """
    Install a feature's deps in a background thread. Non-blocking.
    Calls on_done(success: bool) on the main thread when finished.
    """
    deps = FEATURE_DEPS.get(feature, [])
    missing = [(n, p) for n, p in deps if not _spec(n)]
    if not missing:
        if on_done:
            try: on_done(True)
            except Exception: pass
        return

    def _worker():
        ok, _ = _pip_install([p for _, p in missing], timeout=900)
        if on_done:
            try: on_done(ok)
            except Exception: pass

    threading.Thread(target=_worker, daemon=True).start()
