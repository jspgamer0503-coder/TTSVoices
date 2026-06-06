"""
dep_installer.py — First-run dependency checker and installer for TTS Voices.

Maintained by the opencode AI assistant — see README.md.
Shows a progress window and installs all missing pip and system packages
before the main app loads. Only runs when packages are actually missing.
Subsequent launches skip this entirely (fast path).
"""
import sys, subprocess, importlib.util, threading, os
from pathlib import Path

APP_DIR   = Path(__file__).parent.resolve()
STAMP     = Path.home() / ".ttsvoices" / ".deps_ok_2.5.0"

# ── Dependency manifest ──────────────────────────────────────────────────────
# Each entry: (import_name, pip_package, critical)
# critical=True → app cannot run without it
PYTHON_DEPS = [
    # Core TTS
    ("kokoro_onnx",        "kokoro-onnx>=0.4.2",        True),
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
]

SYSTEM_DEPS = [
    # (check_cmd, package_name, apt_name)
    (["which", "espeak-ng"],  "espeak-ng",  "espeak-ng"),
    (["which", "ffmpeg"],     "ffmpeg",     "ffmpeg"),
    (["which", "gcc"],        "gcc",        "gcc"),
    (["pw-play", "--version"], "pw-play",    "pipewire-utils"),
]


def _spec(name):
    return importlib.util.find_spec(name) is not None

def _which(cmd):
    r = subprocess.run(["which", cmd], capture_output=True)
    return r.returncode == 0

def needs_install():
    """Return True if any critical dependency is missing."""
    if STAMP.exists():
        return False
    # Quick check: critical deps only
    critical = [n for n, _, crit in PYTHON_DEPS if crit]
    return not all(_spec(n) for n in critical)

def _missing_python():
    return [(n, pkg, c) for n, pkg, c in PYTHON_DEPS if not _spec(n)]

def _missing_system():
    missing = []
    for check_cmd, name, apt in SYSTEM_DEPS:
        r = subprocess.run(check_cmd, capture_output=True)
        if r.returncode != 0:
            missing.append((name, apt))
    return missing


# ── Tkinter progress window ──────────────────────────────────────────────────
def run_installer_window():
    """Show a themed progress window and install all missing packages."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("TTS Voices — First Run Setup")
    root.geometry("560x420")
    root.configure(bg="#080c18")
    root.resizable(False, False)
    # Centre on screen
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
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
    hdr = tk.Frame(root, bg="#060a14")
    hdr.pack(fill="x")
    tk.Label(hdr, text="◈  TTS Voices 2.3  —  First Run Setup",
             font=("Courier New", 11, "bold"), fg=ACC, bg="#060a14",
             padx=20, pady=12).pack(side="left")

    tk.Frame(root, bg="#1a2a45", height=1).pack(fill="x")

    tk.Label(root, text="Installing dependencies. This only happens once.",
             font=("Courier New", 9), fg=MUT, bg=BG, pady=8).pack()

    # Progress bar
    prog_var = tk.DoubleVar(value=0)
    pb_frame = tk.Frame(root, bg="#111e33")
    pb_frame.pack(fill="x", padx=0)
    pb = ttk.Progressbar(pb_frame, variable=prog_var, maximum=100)
    s = ttk.Style()
    s.theme_use("clam")
    s.configure("TProgressbar", troughcolor="#111e33", background="#1a6cf5",
                 bordercolor="#1a2a45", lightcolor="#1a6cf5", darkcolor="#1a6cf5")
    pb.pack(fill="x", side="left", expand=True)
    pct_lbl = tk.Label(pb_frame, text="0%", font=("Courier New", 8),
                        fg=TXT, bg="#111e33", width=5)
    pct_lbl.pack(side="right", padx=4)
    prog_var.trace_add("write", lambda *_:
        pct_lbl.configure(text=f"{prog_var.get():.0f}%"))

    # Status label
    status_var = tk.StringVar(value="Checking installed packages…")
    status_lbl = tk.Label(root, textvariable=status_var,
                           font=("Courier New", 9), fg=WARN, bg=BG, pady=4)
    status_lbl.pack()

    # Log area
    log_frame = tk.Frame(root, bg="#1a2a45", pady=1, padx=1)
    log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    log_frame.rowconfigure(0, weight=1)
    log_frame.columnconfigure(0, weight=1)

    log_ta = tk.Text(log_frame, bg=SRF, fg=TXT, font=("Courier New", 8),
                     relief="flat", padx=10, pady=8, state="disabled",
                     wrap="word")
    log_ta.grid(row=0, column=0, sticky="nsew")
    vsc = tk.Scrollbar(log_frame, command=log_ta.yview,
                        bg=SRF, troughcolor=BG, width=8, relief="flat")
    vsc.grid(row=0, column=1, sticky="ns")
    log_ta.configure(yscrollcommand=vsc.set)

    # Skip / launch button
    bot = tk.Frame(root, bg="#0d1526", pady=8)
    bot.pack(fill="x")
    skip_btn = tk.Button(bot, text="  Skip & Launch  ",
                          font=("Courier New", 9, "bold"),
                          bg="#111e33", fg=MUT, relief="flat",
                          padx=12, pady=5, cursor="hand2",
                          command=root.destroy)
    skip_btn.pack(side="right", padx=12)

    def _log(msg, color=None):
        def _do():
            log_ta.configure(state="normal")
            log_ta.insert("end", msg + "\n")
            if color:
                start = log_ta.index("end-2l linestart")
                end   = log_ta.index("end-1l lineend")
                tag   = f"c_{color.replace('#','')}"
                log_ta.tag_configure(tag, foreground=color)
                log_ta.tag_add(tag, start, end)
            log_ta.configure(state="disabled")
            log_ta.see("end")
        root.after(0, _do)

    def _status(txt, col=WARN):
        root.after(0, lambda: status_var.set(txt))
        root.after(0, lambda: status_lbl.configure(fg=col))

    def _prog(pct):
        root.after(0, lambda: prog_var.set(pct))

    def _worker():
        pip = str(Path(sys.executable).parent / "pip")

        missing_py  = _missing_python()
        missing_sys = _missing_system()
        total_steps = len(missing_py) + len(missing_sys) + 2  # +2 for system update + C build
        step        = [0]

        def _advance():
            step[0] += 1
            _prog(step[0] / total_steps * 100)

        # ── System packages ────────────────────────────────────────────────
        if missing_sys:
            _status("Installing system packages…")
            pkgs = [apt for _, apt in missing_sys]
            _log(f"System packages to install: {', '.join(pkgs)}")
            try:
                r = subprocess.run(
                    ["sudo", "apt-get", "install", "-y", "-qq"] + pkgs,
                    capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    _log(f"  ✓  System packages installed", OK)
                else:
                    _log(f"  ⚠  sudo apt failed (may need manual install): {r.stderr[:100]}", WARN)
            except Exception as e:
                _log(f"  ⚠  System install skipped: {e}", WARN)
        _advance()

        # ── Python packages ────────────────────────────────────────────────
        for import_name, pip_pkg, critical in missing_py:
            pkg_display = pip_pkg.split(">=")[0].split("==")[0]
            _status(f"Installing {pkg_display}…")
            _log(f"  pip install {pip_pkg}")
            try:
                r = subprocess.run(
                    [pip, "install", pip_pkg, "--quiet"],
                    capture_output=True, text=True, timeout=300)
                if r.returncode == 0:
                    _log(f"  ✓  {pkg_display}", OK)
                else:
                    marker = ERR if critical else WARN
                    _log(f"  {'✗' if critical else '⚠'}  {pkg_display}: {r.stderr.strip()[:80]}", marker)
            except subprocess.TimeoutExpired:
                _log(f"  ⚠  {pkg_display} timed out", WARN)
            except Exception as e:
                _log(f"  ⚠  {pkg_display}: {e}", WARN)
            _advance()

        # ── Compile C extension ────────────────────────────────────────────
        _status("Compiling C audio extension…")
        try:
            r = subprocess.run(
                [sys.executable, str(APP_DIR / "build_audio_fast.py")],
                capture_output=True, text=True, timeout=30,
                cwd=str(APP_DIR))
            _log(r.stdout.strip() or "  audio_fast.so compiled", OK)
        except Exception as e:
            _log(f"  ⚠  C build skipped: {e}", WARN)
        _advance()

        # ── Done ──────────────────────────────────────────────────────────
        _prog(100)
        _status("Setup complete — launching TTS Voices…", OK)
        _log("\n✓  All done. Launching app…", OK)

        # Write stamp file
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text("ok")

        root.after(1500, root.destroy)

    threading.Thread(target=_worker, daemon=True).start()
    root.mainloop()


def ensure_deps():
    """
    Entry point: run the installer window if needed, then return.
    Call this at the very top of ttsvoices.py main block.
    """
    if STAMP.exists():
        return   # Fast path — already set up
    try:
        import tkinter   # noqa — check Tk is available before showing window
        if needs_install():
            run_installer_window()
        else:
            # Everything is present — just write stamp
            STAMP.parent.mkdir(parents=True, exist_ok=True)
            STAMP.write_text("ok")
    except Exception:
        pass   # Never block the main app from launching
