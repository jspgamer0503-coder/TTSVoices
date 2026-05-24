#!/usr/bin/env python3
"""TTS Voices 2.2 – Unlimited Text-to-Speech Engine for Linux"""
import os, sys, json, threading, queue, time, tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# ── Semantic versioning ────────────────────────────────────────────────────
__version__   = "2.3.0"
VERSION_TUPLE = (2, 3, 0)
VERSION_DATE  = "2026-05-23"
_STARTUP_T0   = time.monotonic()   # measure cold-start time
APP_NAME      = "TTS Voices"

# ── Clear stale bytecode before importing any local modules ──────────────────
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE   = os.path.join(_APP_DIR, "__pycache__")
if os.path.exists(_CACHE):
    import shutil as _shutil
    try:
        _shutil.rmtree(_CACHE)
    except Exception:
        pass
sys.path.insert(0, _APP_DIR)

# ── Suppress noisy warnings at startup ───────────────────────────────────────
import os as _os_startup
# Suppress HuggingFace Hub "unauthenticated" warning (from faster-whisper)
_os_startup.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
_os_startup.environ.setdefault("HF_HUB_VERBOSITY", "error")
_os_startup.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# Redirect vosk C++ LOG output to /dev/null (it spams terminal with model loading info)
# Done by setting VOSK_LOG_LEVEL=-1 before vosk is imported.
# Use os.environ[] (not setdefault) so we override any shell-set value.
_os_startup.environ["VOSK_LOG_LEVEL"] = "-1"

# Suppress harmless PDF font warnings
import warnings as _warnings
_warnings.filterwarnings("ignore", message=".*FontBBox.*")
_warnings.filterwarnings("ignore", message=".*font descriptor.*")
_warnings.filterwarnings("ignore", message=".*Cannot parse.*floats.*")
_warnings.filterwarnings("ignore", category=UserWarning, module="pdfplumber")
_warnings.filterwarnings("ignore", category=UserWarning, module="pypdf")

# ── Deferred heavy imports ───────────────────────────────────────────────────
# These modules load ONNX models, PDF libs, etc.  They are imported on a
# background thread immediately after the Tk window is mapped, so the user
# sees the app in < 300 ms even on a slow machine.  All code that uses these
# must check _engines_ready.is_set() or be called from the UI after loading.
bug_tracker = None
voices      = None
audio_handler  = None
file_extractor = None
SavePointManager = None
_engines_ready = threading.Event()   # set when background import finishes

def _load_engines_background():
    """Import heavy modules on a daemon thread. Called once after window maps."""
    global bug_tracker, voices, audio_handler, file_extractor, SavePointManager
    try:
        # Compile C extension if not yet built
        _so = Path(_APP_DIR) / "audio_fast.so"
        _c  = Path(_APP_DIR) / "audio_fast.c"
        if _c.exists() and not _so.exists():
            import subprocess as _sp
            _sp.run(["gcc", "-O2", "-shared", "-fPIC",
                     "-o", str(_so), str(_c)],
                    capture_output=True, timeout=30)

        import bug_tracker    as _bt
        import voices         as _v
        import audio_handler  as _ah
        import file_extractor as _fe
        from save_point_manager import SavePointManager as _SPM

        bug_tracker      = _bt
        voices           = _v
        audio_handler    = _ah
        file_extractor   = _fe
        SavePointManager = _SPM
    except Exception as e:
        # Failures logged once engines module is available
        try:
            import bug_tracker as _bt
            bug_tracker = _bt
            _bt.error(f"Engine load failed: {e}")
        except Exception:
            pass
    finally:
        _engines_ready.set()


CONFIG_DIR  = Path.home() / ".ttsvoices"
CONFIG_FILE  = CONFIG_DIR / "config.json"
PLUGINS_DIR  = CONFIG_DIR / "plugins"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "speed": 1.3, "pitch": 1.0, "volume": 63, "voice_idx": 0,
    "theme": "dark", "provider": "CPU", "highlight_offset": 150,
    "auto_update_check": True,
}

# ── Engine name constants (mirrors voices.py — defined here so UI builds
#    before voices module is imported on the background thread) ──────────────
_ENGINE_KOKORO = "Kokoro ONNX"
_ENGINE_ESPEAK = "espeak-ng"
_MODELS_DIR    = Path.home() / ".ttsvoices" / "models"


# ══════════════════════════════════════════════════════════════════════════════
#  RESOURCE MONITOR
# ══════════════════════════════════════════════════════════════════════════════
class ResourceMonitor:
    """Background system resource poller that drives adaptive UI behaviour.

    Architecture
    ────────────
    A daemon thread calls psutil every POLL_INTERVAL_S seconds and stores the
    latest CPU % and virtual-memory % in thread-safe attributes (written as
    plain Python float/int assignments, which are atomic under CPython's GIL).

    The main thread's _tick() method is registered with root.after() at
    TICK_MS cadence. Each tick reads those attributes and calls any registered
    callback with a ResourceSnapshot namedtuple.

    Thresholds
    ──────────
    CPU:   LOW < 40 %   MEDIUM 40–75 %   HIGH > 75 %
    RAM:   LOW < 60 %   MEDIUM 60–85 %   HIGH > 85 %

    The combined pressure level is max(cpu_level, ram_level) and is exposed as
    snapshot.level ∈ {"low", "medium", "high"}.

    Adaptive UI decisions made by TTSVoicesApp._on_resources():
      low    → full animations enabled, right panel fully expanded
      medium → animations throttled (GlowButton hover still works, no extras)
      high   → animations suspended, right panel hints compacted, warning shown

    If psutil is not installed the monitor is a no-op: _available = False and
    callbacks are never fired — the app runs exactly as before.
    """

    POLL_INTERVAL_S = 3.0    # background polling cadence
    TICK_MS         = 3000   # main-thread check cadence (must match or > poll)

    CPU_MED  = 40
    CPU_HIGH = 75
    RAM_MED  = 60
    RAM_HIGH = 85

    def __init__(self):
        self._cpu        = 0.0
        self._ram        = 0.0
        self._cbs        = []
        self._root       = None
        self._after      = None
        self._prev_idle  = 0
        self._prev_total = 0

        # Try psutil first; fall back to reading /proc directly (zero deps, Linux only).
        try:
            import psutil as _ps
            self._ps        = _ps
            self._available = True
            self._use_proc  = False
        except ImportError:
            self._ps        = None
            self._use_proc  = True
            self._available = True   # /proc is always available on Linux

    # ── Public API ───────────────────────────────────────────────────────────
    def start(self, root):
        """Attach to a Tk root and begin polling.  Call once after root is created."""
        self._root = root
        if not self._available:
            return
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()
        self._tick()

    def register(self, cb):
        """Register a callback(snapshot) to be called on the main thread each tick."""
        self._cbs.append(cb)

    def stop(self):
        if self._after and self._root:
            try: self._root.after_cancel(self._after)
            except Exception: pass

    # ── Internal ─────────────────────────────────────────────────────────────
    def _poll_loop(self):
        """Daemon thread: poll CPU/RAM every POLL_INTERVAL_S seconds.

        Uses psutil if available, otherwise reads /proc/stat and /proc/meminfo
        directly — zero additional dependencies, works on any Linux kernel.
        """
        # psutil warmup (establishes baseline for non-blocking calls)
        if not self._use_proc:
            try: self._ps.cpu_percent(interval=0.1)
            except Exception: pass

        while True:
            try:
                if self._use_proc:
                    self._cpu = self._read_proc_cpu()
                    self._ram = self._read_proc_ram()
                else:
                    self._cpu = self._ps.cpu_percent(interval=None)
                    self._ram = self._ps.virtual_memory().percent
            except Exception:
                pass
            time.sleep(self.POLL_INTERVAL_S)

    def _read_proc_cpu(self) -> float:
        """Compute CPU % from /proc/stat delta — no external libs needed."""
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            # fields: user nice system idle iowait irq softirq steal guest guest_nice
            vals  = [int(x) for x in parts[1:]]
            idle  = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
            total = sum(vals)
            d_total = total - self._prev_total
            d_idle  = idle  - self._prev_idle
            self._prev_total = total
            self._prev_idle  = idle
            if d_total == 0:
                return self._cpu
            return round(100.0 * (1.0 - d_idle / d_total), 1)
        except Exception:
            return self._cpu

    def _read_proc_ram(self) -> float:
        """Compute RAM % from /proc/meminfo — no external libs needed."""
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":")
                    info[k.strip()] = int(v.split()[0])
            total    = info.get("MemTotal", 0)
            available = info.get("MemAvailable", info.get("MemFree", 0))
            if total == 0:
                return self._ram
            return round(100.0 * (total - available) / total, 1)
        except Exception:
            return self._ram

    def _level(self, val, med, high):
        if val >= high: return "high"
        if val >= med:  return "medium"
        return "low"

    def _tick(self):
        """Main-thread tick: build snapshot and dispatch callbacks."""
        try:
            cpu_lv  = self._level(self._cpu, self.CPU_MED, self.CPU_HIGH)
            ram_lv  = self._level(self._ram, self.RAM_MED, self.RAM_HIGH)
            overall = "high" if "high" in (cpu_lv, ram_lv) else (
                      "medium" if "medium" in (cpu_lv, ram_lv) else "low")
            snap = {
                "cpu": self._cpu, "ram": self._ram,
                "cpu_level": cpu_lv, "ram_level": ram_lv,
                "level": overall,
            }
            for cb in self._cbs:
                try: cb(snap)
                except Exception: pass
        except Exception:
            pass
        finally:
            if self._root:
                try:
                    self._after = self._root.after(self.TICK_MS, self._tick)
                except Exception:
                    pass



_resource_monitor = ResourceMonitor()   # singleton — started in TTSVoicesApp.__init__
# ══════════════════════════════════════════════════════════════════════════════
THEMES = {
    "dark": {
        "label":"⬛ AMOLED",
        "bg":"#000000","surface":"#050508","surface2":"#0a0a10","border":"#1a1a2e",
        "border2":"#22223a","accent":"#1a6cf5","accent2":"#00c8ff","accent_dim":"#0a2a6e",
        "text":"#e2eaff","text2":"#7a98c8","muted":"#3a4a6a","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#1553d0","speak_hover":"#1d6aff",
        "stop_bg":"#6a1010","stop_hover":"#c0392b","header_bg":"#000000","nav_btn":"#0a0a10",
        "nav_hover":"#14142a","textarea_bg":"#000000","textarea_fg":"#e2eaff",
        "scrollbar":"#0a0a10","cursor":"#00c8ff","sel_bg":"#0a2a6e","pill_bg":"#000000",
    },
    "light": {
        "label":"☀ Light",
        "bg":"#f0f4f8","surface":"#ffffff","surface2":"#e8edf4","border":"#c8d4e4",
        "border2":"#b0c0d8","accent":"#1a6cf5","accent2":"#0d5cd4","accent_dim":"#d6e4ff",
        "text":"#0d1526","text2":"#3a5070","muted":"#7a94b0","success":"#1a9e5a",
        "warning":"#c47a00","error":"#c0392b","speak_bg":"#1553d0","speak_hover":"#1d6aff",
        "stop_bg":"#c0392b","stop_hover":"#e74c3c","header_bg":"#1a2a45","nav_btn":"#dce6f5",
        "nav_hover":"#c8d8f0","textarea_bg":"#ffffff","textarea_fg":"#0d1526",
        "scrollbar":"#dce6f5","cursor":"#1a6cf5","sel_bg":"#c8d8f0","pill_bg":"#f0fff4",
    },
    "red": {
        "label":"🔴 Red",
        "bg":"#0f0608","surface":"#1a0a0e","surface2":"#240d14","border":"#3d1520",
        "border2":"#5c1f2e","accent":"#e53e3e","accent2":"#ff6b6b","accent_dim":"#7a1a1a",
        "text":"#f5dde0","text2":"#c49098","muted":"#7a4a50","success":"#00d97e",
        "warning":"#f59e0b","error":"#ff3333","speak_bg":"#c0392b","speak_hover":"#e53e3e",
        "stop_bg":"#3d1520","stop_hover":"#6b2030","header_bg":"#0a0406","nav_btn":"#1a0a0e",
        "nav_hover":"#2d0e16","textarea_bg":"#1a0a0e","textarea_fg":"#f5dde0",
        "scrollbar":"#240d14","cursor":"#ff6b6b","sel_bg":"#5c1f2e","pill_bg":"#0f0608",
    },
    "blue": {
        "label":"🔵 Blue",
        "bg":"#040c1a","surface":"#071428","surface2":"#0a1c38","border":"#0e2d5c",
        "border2":"#133a78","accent":"#2980f5","accent2":"#5bb8ff","accent_dim":"#0d3080",
        "text":"#d8e8ff","text2":"#7aaad4","muted":"#3a6090","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#0d3d99","speak_hover":"#1d6aff",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020810","nav_btn":"#071428",
        "nav_hover":"#0e2d5c","textarea_bg":"#071428","textarea_fg":"#d8e8ff",
        "scrollbar":"#0a1c38","cursor":"#5bb8ff","sel_bg":"#0d3d99","pill_bg":"#020810",
    },
    "teal": {
        "label":"🩵 Teal",
        "bg":"#040f0f","surface":"#081a1a","surface2":"#0c2424","border":"#0e3838",
        "border2":"#155050","accent":"#00b8a9","accent2":"#00e5d4","accent_dim":"#006060",
        "text":"#d0f0ee","text2":"#70b8b0","muted":"#306860","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#007a70","speak_hover":"#00b8a9",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020a0a","nav_btn":"#081a1a",
        "nav_hover":"#0e3838","textarea_bg":"#081a1a","textarea_fg":"#d0f0ee",
        "scrollbar":"#0c2424","cursor":"#00e5d4","sel_bg":"#0e3838","pill_bg":"#020a0a",
    },
    "orange": {
        "label":"🟠 Orange",
        "bg":"#0f0900","surface":"#1a1000","surface2":"#261700","border":"#402800",
        "border2":"#5c3a00","accent":"#f57c00","accent2":"#ffaa33","accent_dim":"#7a3a00",
        "text":"#fff0d8","text2":"#c4a060","muted":"#806040","success":"#00d97e",
        "warning":"#ffcc00","error":"#ef4444","speak_bg":"#c06000","speak_hover":"#f57c00",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a0600","nav_btn":"#1a1000",
        "nav_hover":"#2d1e00","textarea_bg":"#1a1000","textarea_fg":"#fff0d8",
        "scrollbar":"#261700","cursor":"#ffaa33","sel_bg":"#5c3a00","pill_bg":"#0a0600",
    },
    "purple": {
        "label":"🟣 Purple",
        "bg":"#0a0615","surface":"#110920","surface2":"#180d2e","border":"#2a1550",
        "border2":"#3d2070","accent":"#8b5cf6","accent2":"#c084fc","accent_dim":"#4a1fa0",
        "text":"#eddeff","text2":"#a880d0","muted":"#604888","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#5b21b6","speak_hover":"#7c3aed",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#07040f","nav_btn":"#110920",
        "nav_hover":"#2a1550","textarea_bg":"#110920","textarea_fg":"#eddeff",
        "scrollbar":"#180d2e","cursor":"#c084fc","sel_bg":"#3d2070","pill_bg":"#07040f",
    },
    "pink": {
        "label":"🩷 Pink",
        "bg":"#0f0610","surface":"#1a0c1c","surface2":"#241228","border":"#3d1545",
        "border2":"#5a2060","accent":"#ec4899","accent2":"#f9a8d4","accent_dim":"#831843",
        "text":"#ffe0f0","text2":"#c480a0","muted":"#804868","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#9d174d","speak_hover":"#be185d",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a040c","nav_btn":"#1a0c1c",
        "nav_hover":"#3d1545","textarea_bg":"#1a0c1c","textarea_fg":"#ffe0f0",
        "scrollbar":"#241228","cursor":"#f9a8d4","sel_bg":"#5a2060","pill_bg":"#0a040c",
    },
    "golden": {
        "label":"✨ Golden",
        "bg":"#000000","surface":"#110a00","surface2":"#1a1000","border":"#5a3a00",
        "border2":"#7a5200","accent":"#ffb300","accent2":"#ffd700","accent_dim":"#3d2800",
        "text":"#fff5cc","text2":"#ffcc55","muted":"#907040","success":"#00d97e",
        "warning":"#ffcc00","error":"#ef4444","speak_bg":"#ffaa00","speak_hover":"#ffc533",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a0600","nav_btn":"#1a1000",
        "nav_hover":"#2a1a00","textarea_bg":"#0d0800","textarea_fg":"#fff5cc",
        "scrollbar":"#1a1000","cursor":"#ffd700","sel_bg":"#3d2800","pill_bg":"#0a0600",
    },
    "green": {
        "label":"🟢 Green",
        "bg":"#040f06","surface":"#081a0c","surface2":"#0c2414","border":"#0e3818",
        "border2":"#155024","accent":"#22c55e","accent2":"#4ade80","accent_dim":"#0a5c28",
        "text":"#d8ffe0","text2":"#70b880","muted":"#306840","success":"#4ade80",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#166534","speak_hover":"#16a34a",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020a04","nav_btn":"#081a0c",
        "nav_hover":"#0e3818","textarea_bg":"#081a0c","textarea_fg":"#d8ffe0",
        "scrollbar":"#0c2414","cursor":"#4ade80","sel_bg":"#0e3818","pill_bg":"#020a04",
    },
    "studio": {
        "label":"🎨 Studio",
        "bg":"#04060c","surface":"#080e1c","surface2":"#0d1428","border":"#00c8b4",
        "border2":"#0a8f82","accent":"#00c8b4","accent2":"#29dfd0","accent_dim":"#0a4a44",
        "text":"#e8eef5","text2":"#8fa8c4","muted":"#4a5a70","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#007a6e","speak_hover":"#00c8b4",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020408","nav_btn":"#080e1c",
        "nav_hover":"#0d1428","textarea_bg":"#020609","textarea_fg":"#e8eef5",
        "scrollbar":"#0d1428","cursor":"#29dfd0","sel_bg":"#0a4a44","pill_bg":"#020408",
    },
    "midnight": {
        "label":"🌙 Midnight",
        "bg":"#000000","surface":"#050510","surface2":"#0a0a1a","border":"#1a1a3a",
        "border2":"#252550","accent":"#6c63ff","accent2":"#a78bfa","accent_dim":"#2d2a6e",
        "text":"#e8e0ff","text2":"#9d8fff","muted":"#4a4580","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#4c3fcf","speak_hover":"#6c63ff",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#000000","nav_btn":"#050510",
        "nav_hover":"#0f0f25","textarea_bg":"#020208","textarea_fg":"#e8e0ff",
        "scrollbar":"#0a0a1a","cursor":"#a78bfa","sel_bg":"#1a1a3a","pill_bg":"#000000",
    },
    "crimson": {
        "label":"🩸 Crimson",
        "bg":"#000000","surface":"#100008","surface2":"#180010","border":"#3a0018",
        "border2":"#580025","accent":"#dc143c","accent2":"#ff4d70","accent_dim":"#5a0018",
        "text":"#ffe0e8","text2":"#d080a0","muted":"#6a3050","success":"#00d97e",
        "warning":"#f59e0b","error":"#ff3333","speak_bg":"#b01030","speak_hover":"#dc143c",
        "stop_bg":"#3a0018","stop_hover":"#5a0025","header_bg":"#000000","nav_btn":"#100008",
        "nav_hover":"#200010","textarea_bg":"#080005","textarea_fg":"#ffe0e8",
        "scrollbar":"#180010","cursor":"#ff4d70","sel_bg":"#3a0018","pill_bg":"#000000",
    },
    "yellow": {
        "label":"🟡 Yellow",
        "bg":"#000000","surface":"#111100","surface2":"#1c1c00","border":"#4a4800",
        "border2":"#6a6600","accent":"#ffee00","accent2":"#ffff55","accent_dim":"#3a3400",
        "text":"#fffce0","text2":"#ffe066","muted":"#888840","success":"#00d97e",
        "warning":"#ffcc00","error":"#ef4444","speak_bg":"#ffe000","speak_hover":"#ffff44",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a0a00","nav_btn":"#1a1a00",
        "nav_hover":"#2a2a00","textarea_bg":"#0d0d00","textarea_fg":"#fffce0",
        "scrollbar":"#1c1c00","cursor":"#ffff55","sel_bg":"#3a3400","pill_bg":"#0a0a00",
    },
}

C = dict(THEMES["dark"])   # live colour dict – mutated on theme switch

FONT_LABEL = ("Courier New", 8, "bold")
FONT_BTN   = ("Courier New", 9, "bold")

def load_config():
    try:
        if CONFIG_FILE.exists():
            data = json.load(open(CONFIG_FILE))
            merged = {**DEFAULT_CONFIG, **data}
            # Migrate old 32767-scale or zero volume to sensible default
            vol = merged.get("volume", 63)
            if vol > 100:
                vol = max(1, int(vol / 327.67))
            if vol == 0:
                vol = 63
            merged["volume"] = vol
            return merged
    except Exception: pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    try: json.dump(cfg, open(CONFIG_FILE,"w"), indent=2)
    except Exception as e: bug_tracker.warning(f"Config save: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  VIRTUALIZED FILE DIALOGS
#  Canvas-based rendering: only ~40 rows drawn at any time regardless of dir size.
#  Includes live search, keyboard nav, and permission error handling.
# ══════════════════════════════════════════════════════════════════════════════

class _VirtualFileDialog:
    """
    Shared virtualized file-dialog engine.
    Subclasses set: TITLE, BOOKMARKS, FILTER_EXTS (None=doc filter, set=audio filter).
    """
    ROW_H    = 28     # pixels per row
    BUF_ROWS = 8      # rows to pre-render above/below visible viewport
    TITLE    = "Select File"
    BOOKMARKS = [
        ("⌂  Home",       Path.home()),
        ("⬇  Downloads",  Path.home()/"Downloads"),
        ("⬡  Documents",  Path.home()/"Documents"),
        ("⬡  Desktop",    Path.home()/"Desktop"),
    ]
    FILTER_EXTS = None   # None → document extensions from file_extractor

    DOC_ICONS = {
        ".pdf":"📄",".docx":"📝",".doc":"📝",".epub":"📖",
        ".txt":"📃",".md":"📃",".html":"🌐",".htm":"🌐",
        ".rtf":"📝",".odt":"📝",".csv":"📊",
    }
    AUDIO_ICONS = {
        ".mp3":"🎵",".wav":"🎵",".m4a":"🎵",".ogg":"🎵",
        ".flac":"🎵",".aac":"🎵",".wma":"🎵",".opus":"🎵",
        ".aiff":"🎵",".aif":"🎵",
        ".mp4":"🎬",".webm":"🎬",".mkv":"🎬",
        ".avi":"🎬",".mov":"🎬",
    }
    AUDIO_EXTS = {
        ".mp3",".wav",".m4a",".ogg",".flac",
        ".mp4",".webm",".mkv",".avi",".mov",
        ".wma",".aac",".opus",".aiff",".aif",
    }

    def __init__(self, parent):
        self.result     = None
        self._cur       = Path.home()
        self._all_items = []   # all entries in current dir
        self._vis_items = []   # search-filtered view
        self._sel_idx   = -1   # selected index in _vis_items
        self._hover_idx = -1

        self._search_var = tk.StringVar()
        self._fname_var  = tk.StringVar()
        self._path_var   = tk.StringVar(value=str(self._cur))
        self._filter_var = tk.StringVar(value="All supported")
        self._scroll_job = None   # debounce scroll redraws

        self.win = tk.Toplevel(parent)
        self.win.title(self.TITLE)
        self.win.geometry("920x560")
        self.win.configure(bg=C["bg"])
        self.win.resizable(True, True)
        self.win.transient(parent)
        self._build()
        self._go(self._cur)
        self.win.update_idletasks()
        # Force scroll to row 0 immediately and again after full layout settle.
        # Multiple after() calls catch any Configure events that fire after focus.
        def _pin_top():
            try:
                self._canvas.yview_moveto(0.0)
                self._redraw()
            except Exception:
                pass
        try:
            _pin_top()
        except Exception:
            pass
        self.win.grab_set()
        self.win.focus_force()
        self.win.after(0,   _pin_top)
        self.win.after(50,  _pin_top)
        self.win.after(150, _pin_top)
        self.win.wait_window()

    # ── Build UI ─────────────────────────────────────────────────────────────
    def _build(self):
        # Header bar
        top = tk.Frame(self.win, bg=C["header_bg"], pady=10)
        top.pack(fill="x")
        tk.Label(top, text=self.TITLE,
                 font=("Courier New",11,"bold"),
                 fg=C["accent2"], bg=C["header_bg"]).pack(side="left", padx=16)

        pf = tk.Frame(top, bg=C["surface2"],
                      highlightthickness=1, highlightbackground=C["border"])
        pf.pack(side="left", fill="x", expand=True, padx=10, ipady=3)
        pe = tk.Entry(pf, textvariable=self._path_var,
                      bg=C["surface2"], fg=C["text"],
                      insertbackground=C["cursor"],
                      relief="flat", font=("Courier New",9), bd=4)
        pe.pack(fill="x")
        pe.bind("<Return>", lambda _: self._go(Path(self._path_var.get())))

        tk.Button(top, text=" Go ", font=("Courier New",8,"bold"),
                  bg=C["accent"], fg="white", relief="flat",
                  command=lambda: self._go(Path(self._path_var.get())),
                  activebackground=C["speak_hover"],
                  activeforeground="white").pack(side="left", padx=(0,14), ipady=4)

        # Body
        body = tk.Frame(self.win, bg=C["bg"])
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # Sidebar
        sb = tk.Frame(body, bg=C["surface"], width=165,
                      highlightthickness=1, highlightbackground=C["border"])
        sb.grid(row=0, column=0, sticky="nsew")
        sb.pack_propagate(False)
        tk.Label(sb, text="BOOKMARKS", font=("Courier New",7,"bold"),
                 fg=C["accent2"], bg=C["surface"], pady=8).pack(fill="x", padx=12)
        tk.Frame(sb, bg=C["border"], height=1).pack(fill="x", padx=8)
        for label, path in self.BOOKMARKS:
            if path.exists():
                b = tk.Button(sb, text=label, font=("Courier New",9),
                              fg=C["text2"], bg=C["surface"], relief="flat",
                              anchor="w", padx=14, pady=6, cursor="hand2",
                              activebackground=C["surface2"],
                              activeforeground=C["text"],
                              command=lambda p=path: self._go(p))
                b.pack(fill="x")
        tk.Frame(sb, bg=C["border"], height=1).pack(fill="x", padx=8, pady=8)
        self._build_sidebar_extra(sb)

        # Right side: column headers + canvas (search removed — merged into File: field below)
        lf = tk.Frame(body, bg=C["bg"])
        lf.grid(row=0, column=1, sticky="nsew")
        lf.rowconfigure(1, weight=1)
        lf.columnconfigure(0, weight=1)

        # Column headers
        hdr = tk.Frame(lf, bg=C["surface2"])
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(hdr, text="  Name", font=("Courier New",8,"bold"),
                 fg=C["text2"], bg=C["surface2"], anchor="w",
                 padx=8, pady=5).pack(side="left", fill="x", expand=True)
        tk.Label(hdr, text="Type", font=("Courier New",8,"bold"),
                 fg=C["text2"], bg=C["surface2"], anchor="w",
                 padx=8, pady=5, width=8).pack(side="left")
        tk.Label(hdr, text="Size", font=("Courier New",8,"bold"),
                 fg=C["text2"], bg=C["surface2"], anchor="e",
                 padx=8, pady=5, width=9).pack(side="right")

        # Virtual canvas + scrollbar — _scroll_delta uses fraction math
        # so scroll amount is always exactly ROW_H pixels per row.
        self._canvas = tk.Canvas(lf, bg=C["surface"],
                                  highlightthickness=0, cursor="hand2")
        self._canvas.grid(row=1, column=0, sticky="nsew")
        vsc = tk.Scrollbar(lf, orient="vertical", command=self._on_vscroll,
                           bg=C["surface2"], troughcolor=C["bg"],
                           width=10, relief="flat")
        vsc.grid(row=1, column=1, sticky="ns")
        self._vsc = vsc
        self._canvas.configure(yscrollcommand=vsc.set)

        # Canvas event bindings
        self._canvas.bind("<Configure>",       self._on_canvas_configure)
        self._canvas.bind("<ButtonPress-1>",   self._on_click)
        self._canvas.bind("<Double-Button-1>", self._on_double)
        self._canvas.bind("<Motion>",          self._on_motion)
        self._canvas.bind("<Leave>",           self._on_leave)
        self._canvas.bind("<Button-4>",        lambda e: self._scroll_delta(-3))
        self._canvas.bind("<Button-5>",        lambda e: self._scroll_delta(3))
        self._canvas.bind("<MouseWheel>",
            lambda e: self._scroll_delta(-3 if e.delta > 0 else 3))

        # Propagate scroll from ALL child widgets of the dialog to the canvas.
        # Without this, the wheel only works when the cursor is physically over
        # the canvas widget — hovering over labels, the path bar, or the
        # sidebar swallows the event and nothing scrolls.
        def _win_scroll(e):
            # Don't steal scroll from text entries or from the canvas itself
            # (canvas has its own bindings — firing both causes double-scroll).
            w_str = str(e.widget)
            if w_str.endswith(".!entry") or "canvas" in w_str:
                return
            if e.num == 4 or (hasattr(e, "delta") and e.delta > 0):
                self._scroll_delta(-3)
            else:
                self._scroll_delta(3)
        self.win.bind("<Button-4>",  _win_scroll)
        self.win.bind("<Button-5>",  _win_scroll)
        self.win.bind("<MouseWheel>", lambda e: _win_scroll(e))

        # Keyboard bindings on the whole window
        # BackSpace navigates up ONLY when the File: entry does NOT have focus
        for key in ("<Down>","<Up>","<Return>","<Prior>","<Next>"):
            self.win.bind(key, self._key_nav)
        # BackSpace handled separately to avoid stealing from File: entry
        self.win.bind("<BackSpace>", self._key_nav_backspace)

        # Bottom bar — File: field is now also the live search filter
        bot = tk.Frame(self.win, bg=C["surface"],
                       highlightthickness=1, highlightbackground=C["border"], pady=10)
        bot.pack(fill="x", side="bottom")
        tk.Label(bot, text="🔍 Search / File:", font=("Courier New",9,"bold"),
                 fg=C["text2"], bg=C["surface"]).pack(side="left", padx=(16,6))

        fe = tk.Entry(bot, textvariable=self._fname_var,
                      bg=C["surface2"], fg=C["text"],
                      insertbackground=C["cursor"],
                      relief="flat", font=("Courier New",10),
                      bd=4,
                      highlightthickness=1,
                      highlightbackground=C["border"])
        fe.pack(side="left", fill="x", expand=True, padx=(0,10), ipady=3)
        fe.bind("<Return>", lambda _: self._open_selected())
        # Real-time search filtering as the user types
        fe.bind("<KeyRelease>", self._on_file_entry_key)
        # NOTE: Do NOT bind "<BackSpace>" here — doing so with "break" blocks
        # the default Entry delete-char behavior. The window-level _key_nav_backspace
        # already checks focus and won't navigate when this entry has focus.
        fe.bind("<FocusIn>",   lambda _: fe.configure(highlightbackground=C["accent2"]))
        fe.bind("<FocusOut>",  lambda _: fe.configure(highlightbackground=C["border"]))
        self._file_entry = fe
        # Auto-focus the search field when dialog opens
        self.win.after(100, lambda: fe.focus_set())

        self._build_bottom_bar(bot)

        def _cancel():
            self.result = None
            self.win.destroy()

        tk.Button(bot, text=" Cancel ",
                  font=("Courier New",9,"bold"),
                  bg=C["surface2"], fg=C["text2"], relief="flat",
                  padx=10, pady=5,
                  activebackground=C["border"],
                  command=_cancel).pack(side="right", padx=(4,16))

        self._open_btn = tk.Button(bot, text=" Open ",
                                    font=("Courier New",9,"bold"),
                                    bg=C["accent"], fg="white", relief="flat",
                                    padx=10, pady=5, state="disabled",
                                    activebackground=C["speak_hover"],
                                    activeforeground="white",
                                    command=self._open_selected)
        self._open_btn.pack(side="right", padx=4)

    def _build_sidebar_extra(self, sb):
        """Subclasses override to add sidebar content (e.g. formats list)."""
        pass

    def _build_bottom_bar(self, bot):
        """Subclasses override to add extra bottom bar widgets (e.g. filter)."""
        pass

    # ── Navigation ────────────────────────────────────────────────────────────
    def _go(self, path: Path):
        if not path.is_dir():
            if path.is_file():
                self._select_file(path)
                return
            return

        self._cur = path.resolve()
        self._path_var.set(str(self._cur))
        self._fname_var.set("")
        self._search_var.set("")
        self._sel_idx   = -1
        self._hover_idx = -1
        # Reset configure-once flag so the upcoming resize events reset scroll to top
        self._canvas_configured_once = False
        if hasattr(self, "_open_btn"):
            self._open_btn.configure(state="disabled")

        self._all_items = []

        # Parent directory entry
        if self._cur != self._cur.parent:
            self._all_items.append({
                "name": "..  (parent directory)",
                "path": self._cur.parent,
                "is_dir": True,
                "icon": "▲",
                "ftype": "",
                "size": "",
                "accessible": True,
            })

        # Scan directory
        try:
            with os.scandir(str(self._cur)) as it:
                raw = list(it)
        except PermissionError:
            self._canvas.delete("all")
            self._canvas.create_text(
                10, 20,
                text=f"⛔  Access denied: {self._cur}",
                anchor="w", fill=C["error"],
                font=("Courier New", 9), tags="row")
            return

        # Separate dirs and files, hide hidden entries
        dirs  = sorted([e for e in raw if e.is_dir(follow_symlinks=False)
                        and not e.name.startswith(".")],
                       key=lambda e: e.name.lower())
        files = sorted([e for e in raw if e.is_file(follow_symlinks=False)
                        and not e.name.startswith(".")],
                       key=lambda e: e.name.lower())

        for e in dirs:
            accessible = os.access(e.path, os.R_OK | os.X_OK)
            self._all_items.append({
                "name": e.name + "/",
                "path": Path(e.path),
                "is_dir": True,
                "icon": "▶" if accessible else "⊘",
                "ftype": "folder",
                "size": "",
                "accessible": accessible,
            })

        ok_exts = self._get_ok_exts()
        for e in files:
            ext = Path(e.name).suffix.lower()
            if ok_exts and ext not in ok_exts:
                continue
            icon = self._icon_for(ext)
            try:    sz = self._fmt_size(e.stat().st_size)
            except: sz = "?"
            self._all_items.append({
                "name": e.name,
                "path": Path(e.path),
                "is_dir": False,
                "icon": icon,
                "ftype": ext.lstrip(".").upper(),
                "size": sz,
                "accessible": True,
            })

        self._apply_search(reset_scroll=True)

    # ── Search & Filter ───────────────────────────────────────────────────────
    def _apply_search(self, reset_scroll: bool = False):
        """Filter the visible item list.
        - Directories (including ..) are always shown.
        - The currently-selected file is always kept visible even if it
          does not match the search query, so a one-character typo never
          makes the user's selection disappear.
        - reset_scroll=True only when navigating into a new directory.
        """
        q = self._search_var.get().strip().lower()
        # Remember which path is currently selected so we can keep it visible
        selected_path = None
        if 0 <= self._sel_idx < len(self._vis_items):
            sel_it = self._vis_items[self._sel_idx]
            if not sel_it["is_dir"]:
                selected_path = sel_it["path"]

        if not q:
            self._vis_items = list(self._all_items)
        else:
            self._vis_items = [
                it for it in self._all_items
                if it["is_dir"] or q in it["name"].lower()
                or (selected_path and it["path"] == selected_path)
            ]

        # Re-locate the previously selected item in the new list
        new_sel = -1
        if selected_path is not None:
            for i, it in enumerate(self._vis_items):
                if not it["is_dir"] and it["path"] == selected_path:
                    new_sel = i
                    break

        self._sel_idx   = new_sel
        self._hover_idx = -1
        if hasattr(self, "_open_btn"):
            self._open_btn.configure(state="normal" if new_sel >= 0 else "disabled")
        self._update_scrollregion(reset_scroll=reset_scroll)
        self._redraw()

    def _get_ok_exts(self):
        """Subclasses return a set of allowed extensions, or None for all."""
        return None

    def _icon_for(self, ext: str) -> str:
        return self.DOC_ICONS.get(ext, self.AUDIO_ICONS.get(ext, "·"))

    # ── Virtual Rendering ─────────────────────────────────────────────────────
    def _update_scrollregion(self, reset_scroll: bool = False):
        total_h = max(len(self._vis_items) * self.ROW_H, 1)
        # Use actual canvas width instead of hardcoded 10000.
        # A width wider than the canvas can trigger a horizontal scrollbar
        # which changes canvas height → spurious <Configure> → redraw loop
        # that makes rows visually jump (the "parent directory keeps moving" bug).
        try:
            w = max(self._canvas.winfo_width(), 400)
        except Exception:
            w = 600
        self._canvas.configure(scrollregion=(0, 0, w, total_h))
        # Only jump to top when explicitly navigating (not on window resize)
        if reset_scroll:
            self._canvas.yview_moveto(0.0)

    def _redraw(self, *_):
        """Full redraw of the visible window (called on scroll, resize, navigation)."""
        self._canvas.delete("row")
        n = len(self._vis_items)
        if n == 0:
            self._canvas.create_text(
                10, 14, text="No files match.", anchor="w",
                fill=C["muted"], font=("Courier New", 9), tags="row")
            return

        canvas_h  = max(self._canvas.winfo_height(), 1)
        total_h   = n * self.ROW_H
        yview     = self._canvas.yview()
        y_top_px  = yview[0] * total_h

        first = max(0, int(y_top_px / self.ROW_H) - self.BUF_ROWS)
        last  = min(n, int((y_top_px + canvas_h) / self.ROW_H) + self.BUF_ROWS + 1)

        W = max(self._canvas.winfo_width(), 600)

        for i in range(first, last):
            it = self._vis_items[i]
            y  = i * self.ROW_H

            if i == self._sel_idx:
                bg = C["sel_bg"]
            elif i == self._hover_idx:
                bg = C["accent_dim"]
            else:
                bg = C["surface"] if i % 2 == 0 else C["surface2"]

            # Background rectangle — tagged with bg{i} for fast individual updates
            self._canvas.create_rectangle(
                0, y, W, y + self.ROW_H,
                fill=bg, outline="",
                tags=("row", f"bg{i}"))

            fg_icon = C["accent2"] if it["accessible"] else C["error"]
            fg_name = C["text"]    if it["accessible"] else C["muted"]
            fg_meta = C["muted"]

            # Icon
            self._canvas.create_text(
                18, y + self.ROW_H // 2,
                text=it["icon"], anchor="center",
                fill=fg_icon, font=("Courier New", 10),
                tags=("row",))

            # Name — truncated to fit column
            max_name = max(10, (W - 180) // 8)   # rough char limit
            name_disp = it["name"]
            if len(name_disp) > max_name:
                name_disp = name_disp[:max_name - 1] + "…"

            self._canvas.create_text(
                38, y + self.ROW_H // 2,
                text=name_disp, anchor="w",
                fill=fg_name, font=("Courier New", 10),
                tags=("row",))

            # Type column
            self._canvas.create_text(
                W - 115, y + self.ROW_H // 2,
                text=it["ftype"], anchor="w",
                fill=fg_meta, font=("Courier New", 8),
                tags=("row",))

            # Size column
            self._canvas.create_text(
                W - 6, y + self.ROW_H // 2,
                text=it["size"], anchor="e",
                fill=fg_meta, font=("Courier New", 8),
                tags=("row",))

    # ── Scroll ────────────────────────────────────────────────────────────────
    def _on_vscroll(self, *args):
        self._canvas.yview(*args)
        self._schedule_redraw()

    def _scroll_delta(self, rows: int):
        """Scroll by pixel distance = rows * ROW_H, using yview_moveto for precision.
        This avoids Tkinter's ambiguous "units" which vary with scrollregion size."""
        try:
            total_h = len(self._vis_items) * self.ROW_H
            if total_h <= 0:
                return
            cur_top = self._canvas.yview()[0]
            canvas_h = max(self._canvas.winfo_height(), 1)
            # Pixels per row, expressed as fraction of scrollregion
            frac_per_row = self.ROW_H / total_h
            new_top = max(0.0, min(cur_top + rows * frac_per_row,
                                   1.0 - canvas_h / total_h))
            self._canvas.yview_moveto(new_top)
        except Exception:
            self._canvas.yview_scroll(rows, "units")
        self._schedule_redraw()

    def _schedule_redraw(self):
        """Throttle redraws to max ~60 fps."""
        if self._scroll_job:
            try: self.win.after_cancel(self._scroll_job)
            except Exception: pass
        self._scroll_job = self.win.after(16, self._redraw)

    def _on_canvas_configure(self, event):
        # Debounce: rapid-fire Configure events during window resize cause visible
        # row-jump (the "parent directory keeps moving" symptom). Coalesce them
        # into a single redraw 30 ms after the last event settles.
        if hasattr(self, "_cfg_job") and self._cfg_job:
            try: self.win.after_cancel(self._cfg_job)
            except Exception: pass
        self._cfg_job = self.win.after(30, self._on_canvas_configure_settled)

    def _on_canvas_configure_settled(self):
        self._cfg_job = None
        # On the very first real configure (canvas getting its actual pixel size),
        # reset scroll to row 0 to eliminate the blank gap that appears on dialog open.
        first = not getattr(self, "_canvas_configured_once", False)
        self._canvas_configured_once = True
        self._update_scrollregion(reset_scroll=first)
        self._redraw()

    # ── Mouse interaction ─────────────────────────────────────────────────────
    def _row_at(self, canvas_y: float) -> int:
        return int(canvas_y // self.ROW_H)

    def _on_click(self, event):
        idx = self._row_at(self._canvas.canvasy(event.y))
        if 0 <= idx < len(self._vis_items):
            self._select_row(idx)

    def _on_double(self, event):
        idx = self._row_at(self._canvas.canvasy(event.y))
        if 0 <= idx < len(self._vis_items):
            it = self._vis_items[idx]
            if it["is_dir"]:
                if it["accessible"]:
                    self._go(it["path"])
                else:
                    self._dialog_access_denied(it["path"])
            else:
                self._select_file(it["path"])

    def _on_motion(self, event):
        idx = self._row_at(self._canvas.canvasy(event.y))
        if idx != self._hover_idx:
            old = self._hover_idx
            self._hover_idx = idx
            self._refresh_row_bg(old)
            self._refresh_row_bg(idx)

    def _on_leave(self, _):
        old = self._hover_idx
        self._hover_idx = -1
        self._refresh_row_bg(old)

    def _refresh_row_bg(self, idx: int):
        """O(1) update — only repaint the background of one row."""
        if idx < 0 or idx >= len(self._vis_items):
            return
        if idx == self._sel_idx:
            bg = C["sel_bg"]
        elif idx == self._hover_idx:
            bg = C["accent_dim"]
        else:
            bg = C["surface"] if idx % 2 == 0 else C["surface2"]
        try:
            self._canvas.itemconfigure(f"bg{idx}", fill=bg)
        except Exception:
            pass

    def _select_row(self, idx: int):
        """Select row: visual highlight + update File: entry text."""
        old = self._sel_idx
        self._sel_idx = idx
        self._refresh_row_bg(old)
        self._refresh_row_bg(idx)
        it = self._vis_items[idx]
        if not it["is_dir"]:
            # Set filename in entry WITHOUT pushing it into the search filter.
            # Only keyboard input (via _on_file_entry_key) should filter the list.
            self._fname_var.set(it["name"])
            # Clear the search so the full list remains visible after selection
            if self._search_var.get():
                self._search_var.set("")
                self._apply_search(reset_scroll=False)
            try: self._open_btn.configure(state="normal")
            except Exception: pass
        else:
            self._fname_var.set("")
            try: self._open_btn.configure(state="disabled")
            except Exception: pass
        self._ensure_visible(idx)

    def _highlight_row_only(self, idx: int):
        """
        Visual-only row highlight for type-ahead — does NOT touch the File: entry.
        Used when the user is typing and we want to show which row matches,
        without overwriting what they typed.
        """
        old = self._sel_idx
        self._sel_idx = idx
        self._refresh_row_bg(old)
        self._refresh_row_bg(idx)
        self._ensure_visible(idx)

    def _ensure_visible(self, idx: int):
        try:
            total_h  = len(self._vis_items) * self.ROW_H
            canvas_h = self._canvas.winfo_height()
            if total_h == 0: return
            row_top = idx * self.ROW_H
            row_bot = row_top + self.ROW_H
            view_top = self._canvas.yview()[0] * total_h
            view_bot = view_top + canvas_h
            if row_top < view_top:
                self._canvas.yview_moveto(row_top / total_h)
                self._redraw()
            elif row_bot > view_bot:
                self._canvas.yview_moveto((row_bot - canvas_h) / total_h)
                self._redraw()
        except Exception:
            pass

    # ── Keyboard nav ─────────────────────────────────────────────────────────
    def _key_nav(self, event):
        n = len(self._vis_items)
        if n == 0:
            return
        ks = event.keysym
        cur = self._sel_idx

        if ks == "Down":
            self._select_row(min(cur + 1, n - 1) if cur >= 0 else 0)
        elif ks == "Up":
            self._select_row(max(cur - 1, 0) if cur >= 0 else 0)
        elif ks == "Prior":
            self._select_row(max(0, (cur if cur >= 0 else 0) - 10))
        elif ks == "Next":
            self._select_row(min(n - 1, (cur if cur >= 0 else 0) + 10))
        elif ks == "Return":
            if cur >= 0:
                it = self._vis_items[cur]
                if it["is_dir"]:
                    if it["accessible"]: self._go(it["path"])
                else:
                    self._select_file(it["path"])
            else:
                self._open_selected()
        elif ks == "BackSpace":
            if self._cur != self._cur.parent:
                self._go(self._cur.parent)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _dialog_access_denied(self, path: Path):
        win = tk.Toplevel(self.win)
        win.title("Access Denied")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.transient(self.win)
        tk.Label(win, text=f"⛔  Access Denied\n\n{path}\n\nYou do not have permission to open this folder.",
                 font=("Courier New", 9), fg=C["error"], bg=C["bg"],
                 padx=20, pady=16, justify="left").pack()
        tk.Button(win, text="OK", font=("Courier New",9,"bold"),
                  bg=C["accent"], fg="white", relief="flat", padx=12, pady=5,
                  command=win.destroy).pack(pady=(0,12))
        win.grab_set()

    def _select_file(self, path: Path):
        self.result = str(path)
        self.win.destroy()

    def _open_selected(self):
        fn = self._fname_var.get().strip()
        if fn:
            full = self._cur / fn
            if full.is_file():
                self._select_file(full)
            elif full.is_dir():
                self._go(full)

    def _key_nav_backspace(self, event):
        """BackSpace: delete char in File: entry if focused, else navigate up."""
        fe = getattr(self, "_file_entry", None)
        if fe and self.win.focus_get() == fe:
            return   # Let the Entry widget handle it naturally
        # Not in entry — navigate up
        if self._cur != self._cur.parent:
            self._go(self._cur.parent)

    def _on_file_entry_key(self, event):
        """
        Handle key release in the unified Search/File: field.
        - Navigation/modifier keys: ignored
        - Escape: clear search and restore full file list
        - All other keys: push text into _search_var and re-filter the list,
          then typeahead-jump to the first match
        """
        if event.keysym in ("Return","Left","Right","Home","End","Tab",
                            "Control_L","Control_R","Shift_L","Shift_R",
                            "Alt_L","Alt_R","Up","Down"):
            return
        if event.keysym == "Escape":
            self._fname_var.set("")
            self._search_var.set("")
            self._apply_search(reset_scroll=False)
            return
        # Mirror the File: field text into the search var and refilter.
        # reset_scroll=False so the list doesn't jump to top while typing.
        self._search_var.set(self._fname_var.get())
        self._apply_search(reset_scroll=False)
        # Then typeahead-jump to the first match (read-only highlight)
        self._typeahead_jump()

    def _typeahead_jump(self):
        """
        Scroll the list to and highlight the first row matching what's
        in the File: entry — WITHOUT overwriting what the user typed.
        This is read-only navigation: the entry text is never changed here.
        """
        query = self._fname_var.get().strip().lower()
        if not query:
            return
        # Prefix match first (most intuitive)
        for idx, it in enumerate(self._vis_items):
            name_lower = it["name"].lower().rstrip("/")
            if name_lower.startswith(query):
                self._highlight_row_only(idx)
                return
        # Substring fallback
        for idx, it in enumerate(self._vis_items):
            if query in it["name"].lower():
                self._highlight_row_only(idx)
                return

    @staticmethod
    def _fmt_size(sz: int) -> str:
        if sz < 1024:    return f"{sz} B"
        if sz < 1048576: return f"{sz//1024} KB"
        return f"{sz//1048576} MB"


# ── Document File Dialog ──────────────────────────────────────────────────────
class TTSFileDialog(_VirtualFileDialog):
    TITLE = "Load Document"
    BOOKMARKS = [
        ("⌂  Home",       Path.home()),
        ("⬇  Downloads",  Path.home()/"Downloads"),
        ("⬡  Documents",  Path.home()/"Documents"),
        ("⬡  Desktop",    Path.home()/"Desktop"),
    ]

    def _build_sidebar_extra(self, sb):
        tk.Label(sb, text="FORMATS", font=("Courier New",7,"bold"),
                 fg=C["muted"], bg=C["surface"]).pack(anchor="w", padx=12)
        tk.Label(sb, text="PDF  DOCX  DOC\nEPUB HTML  RTF\nODT  TXT   MD  CSV",
                 font=("Courier New",8), fg=C["muted"], bg=C["surface"],
                 justify="left").pack(anchor="w", padx=14, pady=6)

    def _build_bottom_bar(self, bot):
        tk.Label(bot, text="Filter:", font=("Courier New",9),
                 fg=C["text2"], bg=C["surface"]).pack(side="left", padx=(8,4))
        fc = ttk.Combobox(bot, textvariable=self._filter_var, state="readonly",
                          font=("Courier New",8), width=18,
                          values=["All supported","PDF","Word","EPUB",
                                  "HTML","Text","All files"])
        fc.pack(side="left", padx=(0,14))
        fc.bind("<<ComboboxSelected>>", lambda _: self._go(self._cur))

    def _get_ok_exts(self):
        filt = self._filter_var.get()
        doc_exts = getattr(file_extractor, "SUPPORTED_EXTENSIONS",
                           [".pdf",".docx",".doc",".epub",".html",".htm",
                            ".rtf",".odt",".txt",".md",".csv"]) if file_extractor else []
        M = {
            "All supported": set(doc_exts),
            "PDF":           {".pdf"},
            "Word":          {".docx",".doc"},
            "EPUB":          {".epub"},
            "HTML":          {".html",".htm"},
            "Text":          {".txt",".md"},
            "All files":     None,
        }
        return M.get(filt, set(doc_exts))

    def _icon_for(self, ext: str) -> str:
        return self.DOC_ICONS.get(ext, "·")


# ── Audio File Dialog ─────────────────────────────────────────────────────────
class AudioFileDialog(_VirtualFileDialog):
    TITLE = "Select Audio / Video File"
    BOOKMARKS = [
        ("⌂  Home",       Path.home()),
        ("⬇  Downloads",  Path.home()/"Downloads"),
        ("🎵  Music",      Path.home()/"Music"),
        ("🎬  Videos",     Path.home()/"Videos"),
        ("⬡  Desktop",    Path.home()/"Desktop"),
    ]

    def _build_sidebar_extra(self, sb):
        tk.Label(sb, text="FORMATS", font=("Courier New",7,"bold"),
                 fg=C["muted"], bg=C["surface"]).pack(anchor="w", padx=12)
        tk.Label(sb, text="MP3  WAV  M4A\nOGG  FLAC AAC\nMP4  MKV  WebM",
                 font=("Courier New",8), fg=C["muted"], bg=C["surface"],
                 justify="left").pack(anchor="w", padx=14, pady=6)

    def _get_ok_exts(self):
        return self.AUDIO_EXTS

    def _icon_for(self, ext: str) -> str:
        return self.AUDIO_ICONS.get(ext, "🎵")



class TTSSaveDialog:
    """Themed save dialog matching the Load Document dialog."""
    BOOKMARKS = [
        ("⌂  Home",       Path.home()),
        ("⬇  Downloads",  Path.home()/"Downloads"),
        ("⬡  Documents",  Path.home()/"Documents"),
        ("⬡  Desktop",    Path.home()/"Desktop"),
        ("⬡  Music",      Path.home()/"Music"),
    ]

    def __init__(self, parent, title="Save File", default_ext=".wav",
                 filetypes=None, default_name="output"):
        self.result      = None
        self._cur        = Path.home() / "Downloads"
        self._default_ext= default_ext
        self._filetypes  = filetypes or [("WAV Audio", ".wav")]

        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.geometry("880x520")
        self.win.configure(bg=C["bg"])
        self.win.resizable(True, True)
        self.win.transient(parent)
        self._items = []
        self._build(default_name)
        self._go(self._cur)
        self.win.update()
        self.win.grab_set()
        self.win.focus_force()
        self.win.wait_window()

    def _build(self, default_name):
        # Header
        top = tk.Frame(self.win, bg=C["header_bg"], pady=10)
        top.pack(fill="x")
        tk.Label(top, text=self.win.title(),
                 font=("Courier New",11,"bold"),
                 fg=C["accent2"], bg=C["header_bg"]).pack(side="left", padx=16)
        pf = tk.Frame(top, bg=C["surface2"],
                      highlightthickness=1, highlightbackground=C["border"])
        pf.pack(side="left", fill="x", expand=True, padx=10, ipady=3)
        self._path_var = tk.StringVar(value=str(self._cur))
        pe = tk.Entry(pf, textvariable=self._path_var,
                      bg=C["surface2"], fg=C["text"],
                      insertbackground=C["cursor"],
                      relief="flat", font=("Courier New",9), bd=4)
        pe.pack(fill="x")
        pe.bind("<Return>", lambda _: self._go(Path(self._path_var.get())))
        tk.Button(top, text=" Go ", font=("Courier New",8,"bold"),
                  bg=C["accent"], fg="white", relief="flat",
                  command=lambda: self._go(Path(self._path_var.get())),
                  activebackground=C["speak_hover"],
                  activeforeground="white").pack(side="left", padx=(0,14), ipady=4)

        # Body
        body = tk.Frame(self.win, bg=C["bg"])
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # Sidebar
        sb = tk.Frame(body, bg=C["surface"], width=165,
                      highlightthickness=1, highlightbackground=C["border"])
        sb.grid(row=0, column=0, sticky="nsew")
        sb.pack_propagate(False)
        tk.Label(sb, text="SAVE TO", font=("Courier New",7,"bold"),
                 fg=C["accent2"], bg=C["surface"], pady=8).pack(fill="x", padx=12)
        tk.Frame(sb, bg=C["border"], height=1).pack(fill="x", padx=8)
        for label, path in self.BOOKMARKS:
            if path.exists():
                b = tk.Button(sb, text=label, font=("Courier New",9),
                              fg=C["text2"], bg=C["surface"], relief="flat",
                              anchor="w", padx=14, pady=6, cursor="hand2",
                              activebackground=C["surface2"],
                              activeforeground=C["text"],
                              command=lambda p=path: self._go(p))
                b.pack(fill="x")

        # File list
        lf = tk.Frame(body, bg=C["bg"])
        lf.grid(row=0, column=1, sticky="nsew")
        lf.rowconfigure(1, weight=1)
        lf.columnconfigure(0, weight=1)

        hdr = tk.Frame(lf, bg=C["surface2"])
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(hdr, text="  Name", font=("Courier New",8,"bold"),
                 fg=C["text2"], bg=C["surface2"], anchor="w",
                 padx=8, pady=6, width=50).pack(side="left")
        tk.Label(hdr, text="Size", font=("Courier New",8,"bold"),
                 fg=C["text2"], bg=C["surface2"], anchor="e",
                 padx=8, pady=6, width=10).pack(side="right")

        self._lb = tk.Listbox(lf,
                               bg=C["surface"], fg=C["text"],
                               selectbackground=C["accent"],
                               selectforeground="white",
                               activestyle="none",
                               font=("Courier New",10),
                               relief="flat", bd=0,
                               highlightthickness=0)
        self._lb.grid(row=1, column=0, sticky="nsew")
        vsc = tk.Scrollbar(lf, orient="vertical", command=self._lb.yview,
                           bg=C["surface2"], troughcolor=C["bg"], width=10, relief="flat")
        vsc.grid(row=1, column=1, sticky="ns")
        self._lb.configure(yscrollcommand=vsc.set)
        self._lb.bind("<Double-Button-1>", self._on_dbl)
        self._lb.bind("<<ListboxSelect>>", self._on_select)

        # Bottom bar
        bot = tk.Frame(self.win, bg=C["surface"],
                       highlightthickness=1, highlightbackground=C["border"], pady=10)
        bot.pack(fill="x", side="bottom")

        tk.Label(bot, text="File name:", font=("Courier New",9,"bold"),
                 fg=C["text2"], bg=C["surface"]).pack(side="left", padx=(16,6))
        self._fname_var = tk.StringVar(value=default_name + self._default_ext)
        fname_e = tk.Entry(bot, textvariable=self._fname_var,
                           bg=C["surface2"], fg=C["text"],
                           insertbackground=C["cursor"],
                           relief="flat", font=("Courier New",10),
                           width=30, bd=4,
                           highlightthickness=1,
                           highlightbackground=C["border"])
        fname_e.pack(side="left", padx=(0,10), ipady=3)
        fname_e.bind("<Return>", lambda _: self._save())

        # Format selector
        tk.Label(bot, text="Format:", font=("Courier New",9),
                 fg=C["text2"], bg=C["surface"]).pack(side="left", padx=(8,4))
        self._fmt_var = tk.StringVar(value=self._filetypes[0][0])
        fmt_cb = ttk.Combobox(bot, textvariable=self._fmt_var,
                              state="readonly", font=("Courier New",8), width=14,
                              values=[ft[0] for ft in self._filetypes])
        fmt_cb.pack(side="left", padx=(0,14))
        fmt_cb.bind("<<ComboboxSelected>>", self._on_fmt_change)

        def _cancel(): self.result = None; self.win.destroy()
        tk.Button(bot, text="  Cancel  ",
                  font=("Courier New",9,"bold"),
                  bg=C["surface2"], fg=C["text2"], relief="flat",
                  padx=10, pady=5, activebackground=C["border"],
                  command=_cancel).pack(side="right", padx=(4,16))
        tk.Button(bot, text="  Save  ",
                  font=("Courier New",9,"bold"),
                  bg=C["speak_bg"], fg="white", relief="flat",
                  padx=10, pady=5,
                  activebackground=C["speak_hover"],
                  activeforeground="white",
                  command=self._save).pack(side="right", padx=4)

    def _go(self, path: Path):
        if not path.is_dir(): return
        self._cur = path.resolve()
        self._path_var.set(str(self._cur))
        self._lb.delete(0, "end")
        self._items.clear()

        try:
            entries = sorted(self._cur.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return

        if self._cur != self._cur.parent:
            self._lb.insert("end", "  ▲  ../  (parent directory)")
            self._items.append((self._cur.parent, True))

        for e in entries:
            if e.name.startswith("."): continue
            if e.is_dir():
                self._lb.insert("end", f"  ▶  {e.name}/")
                self._items.append((e, True))
            else:
                # Only show audio files + directories
                ext = e.suffix.lower()
                if ext in (".wav", ".mp3", ".flac", ".ogg"):
                    try:    sz = self._fmt_size(e.stat().st_size)
                    except: sz = "?"
                    self._lb.insert("end", f"  ♪  {e.name}   ({sz})")
                    self._items.append((e, False))

        for i in range(0, self._lb.size(), 2):
            self._lb.itemconfig(i, bg=C["surface"])
        for i in range(1, self._lb.size(), 2):
            self._lb.itemconfig(i, bg=C["surface2"])

    def _fmt_size(self, sz):
        if sz < 1024:    return f"{sz} B"
        if sz < 1024**2: return f"{sz//1024} KB"
        return f"{sz//1024**2} MB"

    def _on_select(self, _=None):
        sel = self._lb.curselection()
        if not sel: return
        path, is_dir = self._items[sel[0]]
        if not is_dir:
            self._fname_var.set(path.name)

    def _on_dbl(self, _=None):
        sel = self._lb.curselection()
        if not sel: return
        path, is_dir = self._items[sel[0]]
        if is_dir:  self._go(path)
        else:       self._fname_var.set(path.name)

    def _on_fmt_change(self, _=None):
        # Update filename extension to match selected format
        fmt_name = self._fmt_var.get()
        for name, ext in self._filetypes:
            if name == fmt_name:
                fn = self._fname_var.get()
                stem = Path(fn).stem if fn else "output"
                self._fname_var.set(stem + ext)
                self._default_ext = ext
                break

    def _save(self):
        fn = self._fname_var.get().strip()
        if not fn: return
        # Ensure correct extension
        if not any(fn.lower().endswith(ext) for _, ext in self._filetypes):
            fn += self._default_ext
        self.result = str(self._cur / fn)
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  THEME PICKER DIALOG
# ══════════════════════════════════════════════════════════════════════════════
class ThemePickerDialog:
    """Reliable theme picker - simple buttons, no complex canvas nesting."""
    def __init__(self, parent, current_theme, on_select):
        self._on_select = on_select
        self._current   = current_theme

        self.win = tk.Toplevel(parent)
        self.win.title("Choose Theme")
        self.win.geometry("780x420")
        self.win.configure(bg=C["bg"])
        self.win.resizable(False, False)
        self.win.transient(parent)
        self._build()
        self.win.update()
        self.win.grab_set()
        self.win.focus_force()

    def _build(self):
        # ── Header ─────────────────────────────────────────────────────────
        hdr = tk.Frame(self.win, bg=C["header_bg"])
        hdr.pack(fill="x", ipady=10)
        tk.Label(hdr, text="◑  Choose Theme",
                 font=("Courier New", 13, "bold"),
                 fg=C["accent2"], bg=C["header_bg"],
                 padx=20, pady=8).pack(side="left")
        tk.Label(hdr, text="Click any swatch to apply instantly",
                 font=("Courier New", 9),
                 fg=C["muted"], bg=C["header_bg"]).pack(side="left")
        tk.Frame(self.win, bg=C["border"], height=1).pack(fill="x")

        # ── Grid of theme swatches ──────────────────────────────────────────
        grid_frame = tk.Frame(self.win, bg=C["bg"])
        grid_frame.pack(fill="both", expand=True, padx=20, pady=16)

        themes = list(THEMES.items())
        cols   = 5
        for idx, (key, pal) in enumerate(themes):
            row = idx // cols
            col = idx % cols
            self._swatch(grid_frame, key, pal, row, col)

        for c in range(cols):
            grid_frame.columnconfigure(c, weight=1)



    def _swatch(self, parent, key, pal, row, col):
        """Each swatch is a single Frame with a Canvas preview + label - fully self-contained."""
        accent   = pal["accent"]
        accent2  = pal["accent2"]
        sel_bg   = pal["surface"]
        outline  = "#ffd700" if key == self._current else pal["border2"]

        # Outer container
        outer = tk.Frame(parent, bg=sel_bg,
                         highlightthickness=2,
                         highlightbackground=outline,
                         cursor="hand2")
        outer.grid(row=row, column=col, padx=7, pady=7, sticky="nsew")

        # Canvas preview (drawn once, immediately)
        W, H = 136, 70
        cv = tk.Canvas(outer, width=W, height=H,
                       bg=pal["bg"],
                       highlightthickness=0)
        cv.pack(padx=0, pady=0)

        # Header bar
        cv.create_rectangle(0,   0,   W,   16,  fill=pal["header_bg"], outline="")
        # Accent pill buttons
        cv.create_rectangle(4,   3,   46,  12,  fill=accent,           outline="")
        cv.create_rectangle(50,  3,   74,  12,  fill=pal["surface"],   outline=pal["border"])
        cv.create_rectangle(78,  3,  102,  12,  fill=pal["surface"],   outline=pal["border"])
        # Textarea area
        cv.create_rectangle(2,   18, 100,  68,  fill=pal["surface"],   outline=pal["border"])
        cv.create_rectangle(5,   23,  75,  27,  fill=pal["text2"],     outline="")
        cv.create_rectangle(5,   31,  55,  34,  fill=pal["muted"],     outline="")
        cv.create_rectangle(5,   38,  65,  41,  fill=pal["muted"],     outline="")
        # Right panel
        cv.create_rectangle(103, 18, W-2,  68,  fill=pal["surface2"],  outline=pal["border"])
        cv.create_rectangle(106, 22, W-5,  31,  fill=accent,           outline="")
        cv.create_rectangle(106, 34, W-5,  41,  fill=pal["stop_bg"],   outline="")
        # Bottom accent stripe
        cv.create_rectangle(0,   66,  W,   70,  fill=accent,           outline="")

        # Label beneath canvas
        lbl_bg = tk.Frame(outer, bg=sel_bg)
        lbl_bg.pack(fill="x")

        # Colour dot
        dot_cv = tk.Canvas(lbl_bg, width=10, height=10,
                           bg=sel_bg, highlightthickness=0)
        dot_cv.pack(side="left", padx=(7, 3), pady=4)
        dot_cv.create_oval(1, 1, 9, 9, fill=accent2, outline="")

        name_lbl = tk.Label(lbl_bg, text=pal["label"],
                            font=("Courier New", 8, "bold"),
                            fg=accent2, bg=sel_bg, anchor="w")
        name_lbl.pack(side="left")

        if key == self._current:
            tk.Label(lbl_bg, text="✓",
                     font=("Courier New", 9, "bold"),
                     fg="#ffd700", bg=sel_bg).pack(side="right", padx=5)

        # Bind ALL widgets – key captured as default arg to avoid closure bug
        def pick(e=None, k=key):
            self._on_select(k)
            self.win.destroy()

        def hover_in(e=None, o=outer, a=accent):
            o.configure(highlightbackground=a)

        def hover_out(e=None, o=outer, b=outline):
            o.configure(highlightbackground=b)

        for w in (outer, cv, lbl_bg, dot_cv, name_lbl):
            w.bind("<Button-1>", pick)
            w.bind("<Enter>",    hover_in)
            w.bind("<Leave>",    hover_out)


# ══════════════════════════════════════════════════════════════════════════════
#  CUSTOM WIDGETS
# ══════════════════════════════════════════════════════════════════════════════
class SmoothScroller:
    """
    Physics-based smooth scroll for any Tkinter Text or Canvas widget.

    Implements:
    - Pixel-accurate fractional scrolling (sub-unit accumulation)
    - Ease-out deceleration curve (cubic) over ~140 ms per event
    - Kinetic / momentum coasting: after the wheel stops, content
      continues to glide and decelerates via a friction coefficient
    - Scrolling acceleration: fast spins multiply the distance

    Usage:
        scroller = SmoothScroller(text_widget, root)
        text_widget.bind("<Button-4>", lambda e: scroller.on_scroll(-1))
        text_widget.bind("<Button-5>", lambda e: scroller.on_scroll(1))
        text_widget.bind("<MouseWheel>",
            lambda e: scroller.on_scroll(-1 if e.delta > 0 else 1))
    """

    # Tuning constants
    PIXELS_PER_NOTCH   = 55    # base scroll distance per wheel notch
    ACCEL_THRESHOLD    = 0.12  # seconds: notches faster than this get acceleration
    ACCEL_MULTIPLIER   = 1.4   # speed boost for fast spinning
    ANIM_DURATION_MS   = 100   # easing animation duration (ms)
    ANIM_FPS           = 60    # animation frame rate
    KINETIC_FRICTION   = 0.60  # velocity decay per frame
    KINETIC_THRESHOLD  = 9999  # effectively disabled — no kinetic coast after scroll stops

    def __init__(self, widget, root):
        self._widget  = widget
        self._root    = root
        self._is_text = isinstance(widget, tk.Text)

        # Animation state
        self._target_px   = 0.0   # accumulated target pixels to scroll (ease target)
        self._current_px  = 0.0   # pixels already eased so far this animation
        self._anim_job    = None

        # Kinetic state
        self._velocity    = 0.0   # pixels/frame for coasting
        self._kinetic_job = None
        self._last_scroll = 0.0   # monotonic time of last wheel event

        # Fractional carry-over (sub-unit accumulation)
        self._frac_carry  = 0.0

    # ── Public ────────────────────────────────────────────────────────────────
    def on_scroll(self, direction: int):
        """Call this from your wheel event handler. direction: +1=down, -1=up."""
        import time
        now = time.monotonic()
        elapsed = now - self._last_scroll
        self._last_scroll = now

        # Cancel any ongoing kinetic coast — user is actively scrolling again
        if self._kinetic_job:
            try: self._root.after_cancel(self._kinetic_job)
            except Exception: pass
            self._kinetic_job = None

        # Acceleration: fast successive notches get a multiplier
        pixels = self.PIXELS_PER_NOTCH
        if elapsed < self.ACCEL_THRESHOLD:
            pixels = int(pixels * self.ACCEL_MULTIPLIER)

        self._target_px += direction * pixels
        self._velocity   = direction * pixels  # seed kinetic velocity

        if not self._anim_job:
            self._current_px = 0.0
            self._anim_job = self._root.after(0, self._tick_ease)

    # ── Easing animation ──────────────────────────────────────────────────────
    def _tick_ease(self):
        """Drive the ease-out animation frame by frame."""
        self._anim_job = None
        remaining = self._target_px - self._current_px
        if abs(remaining) < 0.5:
            # Snap the leftover fraction
            self._do_scroll(remaining)
            self._target_px  = 0.0
            self._current_px = 0.0
            # Hand off to kinetic coasting
            self._start_kinetic()
            return

        # Cubic ease-out: step = remaining * (1 - t^3) approximated per-frame
        # Using a fixed ratio gives a smooth exponential decay feel
        step = remaining * 0.22   # 22% of remaining distance per frame ≈ ease-out
        self._do_scroll(step)
        self._current_px += step

        interval = max(1, 1000 // self.ANIM_FPS)
        self._anim_job = self._root.after(interval, self._tick_ease)

    # ── Kinetic coasting ──────────────────────────────────────────────────────
    def _start_kinetic(self):
        if abs(self._velocity) < self.KINETIC_THRESHOLD:
            return
        self._kinetic_job = self._root.after(
            max(1, 1000 // self.ANIM_FPS), self._tick_kinetic)

    def _tick_kinetic(self):
        self._kinetic_job = None
        self._velocity *= self.KINETIC_FRICTION
        if abs(self._velocity) < self.KINETIC_THRESHOLD:
            self._velocity = 0.0
            return
        self._do_scroll(self._velocity)
        self._kinetic_job = self._root.after(
            max(1, 1000 // self.ANIM_FPS), self._tick_kinetic)

    # ── Low-level scroll ──────────────────────────────────────────────────────
    def _do_scroll(self, pixels: float):
        """Convert pixel delta to widget scroll units with sub-unit carry-over."""
        try:
            if not self._widget.winfo_exists():
                return
        except Exception:
            return

        total = pixels + self._frac_carry
        units, frac = divmod(total, 1.0)
        units = int(units)
        # Preserve sign of fraction
        if total < 0:
            units = -int(abs(total))
            frac  = total - units
        self._frac_carry = frac

        if units == 0:
            return

        try:
            if self._is_text:
                self._widget.yview_scroll(units, "pixels")
            else:
                # Canvas: use yview_scroll in units (1 unit ≈ 20px on most systems)
                # Convert back to approximate canvas units
                self._widget.yview_scroll(units // 20 or (1 if units > 0 else -1), "units")
        except Exception:
            pass

    def cancel(self):
        """Stop all animation immediately (call on widget destroy)."""
        for job in (self._anim_job, self._kinetic_job):
            if job:
                try: self._root.after_cancel(job)
                except Exception: pass
        self._anim_job = self._kinetic_job = None


class GlowButton(tk.Frame):
    def __init__(self, parent, text, command=None, fg="white",
                 normal_bg=None, hover_bg=None, font=FONT_BTN, **kw):
        nbg = normal_bg or C["nav_btn"]
        hbg = hover_bg  or C["nav_hover"]
        super().__init__(parent, bg=nbg, cursor="hand2",
                         highlightthickness=1, highlightbackground=C["border2"], **kw)
        self._nbg=nbg; self._hbg=hbg; self._cmd=command
        self._lbl = tk.Label(self, text=text, fg=fg, bg=nbg, font=font,
                             padx=12, pady=6, cursor="hand2")
        self._lbl.pack(fill="both", expand=True)
        for w in (self, self._lbl):
            w.bind("<Enter>",    self._enter)
            w.bind("<Leave>",    self._leave)
            w.bind("<Button-1>", self._click)

    def _enter(self,_=None): self.configure(bg=self._hbg); self._lbl.configure(bg=self._hbg)
    def _leave(self,_=None): self.configure(bg=self._nbg); self._lbl.configure(bg=self._nbg)
    def _click(self,_=None):
        if self._cmd: self._cmd()
    def set_text(self, t):   self._lbl.configure(text=t)
    def set_colors(self, n, h=None):
        self._nbg=n; self._hbg=h or n
        self.configure(bg=n); self._lbl.configure(bg=n)

class NumericControl(tk.Frame):
    def __init__(self, parent, label, var, mn, mx, step, fmt="{:.1f}", **kw):
        super().__init__(parent, bg=C["surface"], **kw)
        self._var=var; self._mn=mn; self._mx=mx; self._step=step; self._fmt=fmt
        self._dvar = tk.StringVar(value=fmt.format(var.get()))
        tk.Label(self, text=label, font=FONT_LABEL, fg=C["text2"], bg=C["surface"],
                 width=9, anchor="w").pack(side="left")
        self._dec_btn = tk.Button(self, text="−", font=("Courier New",10,"bold"),
                  fg=C["accent2"], bg=C["surface2"], relief="flat",
                  padx=6, pady=1, cursor="hand2",
                  command=self._dec, highlightthickness=0)
        self._dec_btn.pack(side="left")
        tk.Label(self, textvariable=self._dvar, font=("Courier New",10,"bold"),
                 fg=C["accent2"], bg=C["surface"], width=7, anchor="center").pack(side="left")
        self._inc_btn = tk.Button(self, text="+", font=("Courier New",10,"bold"),
                  fg=C["accent2"], bg=C["surface2"], relief="flat",
                  padx=6, pady=1, cursor="hand2",
                  command=self._inc, highlightthickness=0)
        self._inc_btn.pack(side="left")
        # Hover and press feedback for both buttons
        for btn in (self._dec_btn, self._inc_btn):
            btn.bind("<Enter>",    lambda e, b=btn: b.configure(bg=C["accent_dim"]))
            btn.bind("<Leave>",    lambda e, b=btn: b.configure(bg=C["surface2"]))
            btn.bind("<ButtonPress-1>",   lambda e, b=btn: b.configure(bg=C["accent"]))
            btn.bind("<ButtonRelease-1>", lambda e, b=btn: b.configure(
                bg=C["accent_dim"] if b.winfo_containing(e.x_root, e.y_root) == b else C["surface2"]))
        var.trace_add("write", lambda *_: self._refresh())

    def _refresh(self):
        try: self._dvar.set(self._fmt.format(self._var.get()))
        except Exception: pass
    def _inc(self): self._var.set(min(self._mx, round(self._var.get()+self._step,4)))
    def _dec(self): self._var.set(max(self._mn, round(self._var.get()-self._step,4)))

class SectionHeader(tk.Frame):
    def __init__(self, parent, text, icon="◆", **kw):
        super().__init__(parent, bg=C["surface"], **kw)
        row = tk.Frame(self, bg=C["surface"])
        row.pack(fill="x", pady=(10,3))
        # Accent left-edge indicator strip
        accent_bar = tk.Frame(row, bg=C["accent2"], width=3)
        accent_bar.pack(side="left", fill="y", padx=(8,6))
        tk.Label(row, text=f"{icon} {text}", font=("Courier New", 8, "bold"),
                 fg=C["accent2"], bg=C["surface"]).pack(side="left")
        tk.Frame(self, bg=C["border2"], height=1).pack(fill="x", padx=8)


class PillToggle(tk.Canvas):
    """Animated pill-shaped toggle switch.

    Internally this is a tk.Canvas that draws a rounded-rect track and a
    circular thumb.  On click it animates the thumb left↔right using
    root.after() to produce a smooth slide, then calls the optional callback
    with the new bool value.

    Implementation notes
    ────────────────────
    The track width is fixed at 44 px, height 22 px.  The thumb is a 16-px
    circle that travels 22 px (half track width) between the OFF (left) and
    ON (right) positions.  Animation runs at ~60 fps over 120 ms by stepping
    the thumb x-coord in increments via _animate_step.  Color interpolation
    between the off_color and on_color is done in 8-bit RGB space per frame
    so the track also fades smoothly.

    The widget exposes:
      .get()       → current bool state
      .set(bool)   → programmatic set (no animation)
      .toggle()    → flip state with animation
    """
    W, H    = 44, 22       # track dimensions
    THUMB_R = 8            # thumb radius
    STEPS   = 12           # animation frames
    DELAY   = 10           # ms per frame  (≈ 10 * 12 = 120 ms total)

    def __init__(self, parent, state=True, on_color=None, off_color=None,
                 callback=None, **kw):
        bg = C["surface"]
        super().__init__(parent, width=self.W, height=self.H,
                         bg=bg, highlightthickness=0, bd=0, **kw)
        self._state     = bool(state)
        self._on_col    = on_color  or C.get("accent2", "#00c8ff")
        self._off_col   = off_color or C.get("border2", "#223055")
        self._thumb_col = "#ffffff"
        self._cb        = callback
        self._animating = False

        self._draw()
        self.bind("<Button-1>", lambda _e: self.toggle())
        self.configure(cursor="hand2")

    # ── Internal drawing ─────────────────────────────────────────────────────
    def _hex(self, c): return tuple(int(c.lstrip("#")[i:i+2], 16) for i in (0,2,4))

    def _lerp_color(self, a, b, t):
        """Linear interpolate between two #rrggbb hex strings."""
        ra,ga,ba = self._hex(a)
        rb,gb,bb = self._hex(b)
        r = int(ra + (rb-ra)*t)
        g = int(ga + (gb-ga)*t)
        bl= int(ba + (bb-ba)*t)
        return f"#{r:02x}{g:02x}{bl:02x}"

    def _thumb_x(self, state):
        """Return thumb center-x for a given bool state."""
        return self.W - self.THUMB_R - 3 if state else self.THUMB_R + 3

    def _draw(self, thumb_x=None, t=None):
        """Redraw track and thumb.  t=0..1 is animation progress (None=snap)."""
        self.delete("all")
        if thumb_x is None: thumb_x = self._thumb_x(self._state)
        track_col = self._lerp_color(
            self._off_col, self._on_col,
            t if t is not None else (1.0 if self._state else 0.0)
        )
        # Rounded-rect track via overlapping oval + rect trick
        r = self.H // 2
        self.create_oval(0, 0, self.H, self.H, fill=track_col, outline="")
        self.create_oval(self.W-self.H, 0, self.W, self.H, fill=track_col, outline="")
        self.create_rectangle(r, 0, self.W-r, self.H, fill=track_col, outline="")
        # Thumb shadow — use a darkened surface color, no alpha (Tk doesn't support #rrggbbaa)
        cx, cy   = thumb_x, self.H // 2
        shadow_col = self._lerp_color(self._off_col, "#000000", 0.4)
        self.create_oval(cx-self.THUMB_R-1, cy-self.THUMB_R-1,
                         cx+self.THUMB_R+1, cy+self.THUMB_R+1,
                         fill=shadow_col, outline="")
        self.create_oval(cx-self.THUMB_R, cy-self.THUMB_R,
                         cx+self.THUMB_R, cy+self.THUMB_R,
                         fill=self._thumb_col, outline="")

    # ── Animation ────────────────────────────────────────────────────────────
    def toggle(self):
        new_state = not self._state
        self._state = new_state
        if self._cb:
            self._cb(new_state)
        if not self._animating:
            self._animating = True
            self._anim_step(0, not new_state, new_state)

    def _anim_step(self, step, src_state, dst_state):
        t = step / self.STEPS
        sx = self._thumb_x(src_state)
        dx = self._thumb_x(dst_state)
        tx = sx + (dx - sx) * t
        self._draw(thumb_x=tx, t=t)
        if step < self.STEPS:
            self.after(self.DELAY, lambda: self._anim_step(step+1, src_state, dst_state))
        else:
            self._animating = False
            self._draw()

    # ── Public API ───────────────────────────────────────────────────────────
    def get(self): return self._state
    def set(self, val):
        self._state = bool(val)
        self._draw()


class WaveformExportBtn(tk.Canvas):
    """Export button with a decorative waveform bar graph inside.

    Draws a card with:
      • top-left format label (e.g. "WAV")
      • subtitle line (e.g. "File 3 · WAV")
      • bar-graph waveform in the accent color (random-seeded per format)
    On hover the card brightens.  On click it calls command().

    This replicates the visual style of the export cards visible in the
    reference screenshot where WAV and MP3 buttons have embedded waveforms.
    The waveform bars are deterministic (seeded by format name) so they
    look consistent across redraws / theme changes.
    """
    def __init__(self, parent, fmt, subtitle, command=None, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, cursor="hand2",
                         relief="flat", **kw)
        self._fmt      = fmt
        self._subtitle = subtitle
        self._cmd      = command
        self._hover    = False
        # Snapshot current theme colors so hover never reads a stale C dict
        # during the brief window between _recolor() and _redraw().
        self._snap_colors()
        self.bind("<Configure>",  self._redraw)
        self.bind("<Enter>",      lambda _: self._set_hover(True))
        self.bind("<Leave>",      lambda _: self._set_hover(False))
        self.bind("<Button-1>",   lambda _: command() if command else None)

    def _snap_colors(self):
        """Snapshot the current theme palette into instance variables.
        Called on init and whenever _apply_theme calls _redraw().
        This means hover/leave always use the *same* set of colors that
        were snapshotted during the last full redraw — no theme-flash."""
        self._c_base_bg = C.get("surface2", "#111e33")
        self._c_hov_bg  = C.get("accent_dim", "#0a2a6e")
        self._c_acc     = C.get("accent2",  "#00c8ff")
        self._c_txt     = C.get("text",     "#e8f0ff")
        self._c_muted   = C.get("muted",    "#4a6080")
        self._c_bdr     = C.get("border2",  "#223055")

    def _set_hover(self, val):
        self._hover = val
        self._redraw()

    def _redraw(self, _=None):
        # Re-snapshot theme on every full redraw (catches theme switches)
        self._snap_colors()
        w = self.winfo_width()  or 110
        h = self.winfo_height() or 70
        bg = self._c_hov_bg if self._hover else self._c_base_bg
        # Set widget bg BEFORE clearing canvas items so there is never a 1-frame
        # gap where the old color shows through between delete("all") and repaint.
        try:
            self.configure(bg=bg)
        except Exception:
            pass
        self.delete("all")
        acc   = self._c_acc
        txt   = self._c_txt
        muted = self._c_muted
        bdr   = self._c_bdr

        # Card background
        self.create_rectangle(0, 0, w, h, fill=bg, outline=bdr, width=1)

        # Format label top-left
        self.create_text(12, 12, text=self._fmt, anchor="nw",
                         font=("Courier New", 10, "bold"), fill=txt)

        # Waveform bars – heights are deterministic from the format string
        import hashlib
        seed = int(hashlib.md5(self._fmt.encode()).hexdigest(), 16)
        bar_count = 18
        bar_w     = 3
        bar_gap   = 2
        total_w   = bar_count * (bar_w + bar_gap) - bar_gap
        start_x   = (w - total_w) // 2
        wave_y    = h - 20
        max_bar_h = h // 2 - 8
        for i in range(bar_count):
            seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
            bh = max(3, int((seed & 0xFF) / 255 * max_bar_h))
            x0 = start_x + i * (bar_w + bar_gap)
            self.create_rectangle(x0, wave_y - bh, x0 + bar_w, wave_y,
                                  fill=acc, outline="")

        # Subtitle bottom
        self.create_text(12, h - 6, text=self._subtitle, anchor="sw",
                         font=("Courier New", 7), fill=muted)


# ══════════════════════════════════════════════════════════════════════════════
#  AUDIO-TO-TEXT WINDOW
# ══════════════════════════════════════════════════════════════════════════════
class AudioToTextWindow:
    """
    Offline audio-to-text transcription window.
    Supports faster-whisper (preferred), vosk, or SpeechRecognition (online).
    Transcript can be loaded directly into the main TTS editor.
    """

    SUPPORTED_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".webm", ".mkv"}

    def __init__(self, parent, on_load_cb=None):
        self._on_load_cb = on_load_cb
        self._audio_path = None
        self._transcript  = ""
        self._running     = False

        self.win = tk.Toplevel(parent)
        self.win.title("Audio to Text Converter")
        self.win.geometry("820x620")
        self.win.configure(bg=C["bg"])
        self.win.resizable(True, True)
        self.win.transient(parent)
        self._build()
        self.win.update()
        self.win.focus_force()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build(self):
        # Header
        hdr = tk.Frame(self.win, bg=C["header_bg"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="🎙  Audio to Text Converter",
                 font=("Courier New", 13, "bold"),
                 fg=C["accent2"], bg=C["header_bg"],
                 padx=20, pady=10).pack(side="left")
        tk.Label(hdr, text="Offline transcription powered by Whisper / Vosk",
                 font=("Courier New", 8),
                 fg=C["muted"], bg=C["header_bg"]).pack(side="left")
        tk.Frame(self.win, bg=C["border"], height=1).pack(fill="x")

        # Drop zone / file chooser
        dz = tk.Frame(self.win, bg=C["surface"],
                       highlightthickness=2, highlightbackground=C["border"])
        dz.pack(fill="x", padx=16, pady=(14, 6))

        dz_inner = tk.Frame(dz, bg=C["surface"], pady=18)
        dz_inner.pack(fill="x")

        tk.Label(dz_inner, text="🎵",
                 font=("Courier New", 24), fg=C["accent2"],
                 bg=C["surface"]).pack()
        self._drop_lbl = tk.Label(dz_inner,
                 text="Click to select an audio or video file",
                 font=("Courier New", 10), fg=C["text2"],
                 bg=C["surface"])
        self._drop_lbl.pack(pady=(4, 2))
        tk.Label(dz_inner,
                 text="Supports: MP3, WAV, M4A, OGG, FLAC, MP4, WebM, MKV",
                 font=("Courier New", 8), fg=C["muted"],
                 bg=C["surface"]).pack()

        sel_btn = tk.Button(dz_inner, text="  Browse File  ",
                            font=("Courier New", 9, "bold"),
                            bg=C["accent"], fg="white", relief="flat",
                            padx=14, pady=6, cursor="hand2",
                            activebackground=C["speak_hover"],
                            activeforeground="white",
                            command=self._pick_file)
        sel_btn.pack(pady=(10, 0))

        # Only the Browse File button (and the music note icon) trigger the picker
        self._drop_lbl.configure(cursor="hand2")
        self._drop_lbl.bind("<Button-1>", lambda _: self._pick_file())

        # Engine selector row
        eng_row = tk.Frame(self.win, bg=C["bg"])
        eng_row.pack(fill="x", padx=16, pady=4)

        tk.Label(eng_row, text="Engine:",
                 font=("Courier New", 9, "bold"),
                 fg=C["text2"], bg=C["bg"]).pack(side="left", padx=(0, 8))

        self._engine_var = tk.StringVar(value="Auto (best available)")
        engines = ["Auto (best available)", "faster-whisper (offline)", "vosk (offline)"]
        eng_cb = ttk.Combobox(eng_row, textvariable=self._engine_var,
                               state="readonly", font=("Courier New", 9),
                               values=engines, width=26)
        eng_cb.pack(side="left")

        tk.Label(eng_row, text="  Model:",
                 font=("Courier New", 9, "bold"),
                 fg=C["text2"], bg=C["bg"]).pack(side="left", padx=(12, 4))
        self._model_var = tk.StringVar(value="tiny")
        model_cb = ttk.Combobox(eng_row, textvariable=self._model_var,
                                 state="readonly", font=("Courier New", 9),
                                 values=["tiny", "base", "small", "medium", "large"],
                                 width=10)
        model_cb.pack(side="left")

        # Transcribe button + progress
        ctrl = tk.Frame(self.win, bg=C["bg"])
        ctrl.pack(fill="x", padx=16, pady=(8, 4))

        self._transcribe_btn = tk.Button(ctrl, text="  ▶  Transcribe  ",
                            font=("Courier New", 10, "bold"),
                            bg=C["speak_bg"], fg="white", relief="flat",
                            padx=16, pady=8, cursor="hand2",
                            activebackground=C["speak_hover"],
                            activeforeground="white",
                            command=self._start_transcribe)
        self._transcribe_btn.pack(side="left")

        self._cancel_btn = tk.Button(ctrl, text="  ■  Cancel  ",
                            font=("Courier New", 10, "bold"),
                            bg=C["stop_bg"], fg="white", relief="flat",
                            padx=12, pady=8, cursor="hand2",
                            state="disabled",
                            activebackground=C["stop_hover"],
                            activeforeground="white",
                            command=self._cancel)
        self._cancel_btn.pack(side="left", padx=(8, 0))

        self._status_lbl = tk.Label(ctrl, text="Select a file to begin",
                            font=("Courier New", 9),
                            fg=C["muted"], bg=C["bg"])
        self._status_lbl.pack(side="left", padx=16)

        # Progress bar
        self._prog_var = tk.DoubleVar(value=0)
        pb_frame = tk.Frame(self.win, bg=C["surface2"])
        pb_frame.pack(fill="x")
        self._pb = ttk.Progressbar(pb_frame, variable=self._prog_var, maximum=100)
        self._pb.pack(fill="x", side="left", expand=True)
        self._pct_lbl = tk.Label(pb_frame, text="0%", font=("Courier New", 8),
                                  fg=C["text2"], bg=C["surface2"], width=5)
        self._pct_lbl.pack(side="right", padx=6)
        self._prog_var.trace_add("write", lambda *_:
            self._pct_lbl.configure(text=f"{self._prog_var.get():.0f}%")
            if (self.win.winfo_exists() and self._pct_lbl.winfo_exists()) else None)

        # Transcript editor
        tk.Frame(self.win, bg=C["border"], height=1).pack(fill="x", padx=0, pady=(4, 0))
        ta_label = tk.Frame(self.win, bg=C["surface2"])
        ta_label.pack(fill="x")
        tk.Label(ta_label, text="Transcript",
                 font=("Courier New", 8, "bold"),
                 fg=C["accent2"], bg=C["surface2"], padx=12, pady=4).pack(side="left")
        self._word_lbl = tk.Label(ta_label, text="",
                 font=("Courier New", 8), fg=C["muted"], bg=C["surface2"])
        self._word_lbl.pack(side="left")

        ta_frame = tk.Frame(self.win, bg=C["border"], pady=1, padx=1)
        ta_frame.pack(fill="both", expand=True, padx=0)
        ta_frame.rowconfigure(0, weight=1)
        ta_frame.columnconfigure(0, weight=1)

        self._ta = tk.Text(ta_frame,
                           bg=C["textarea_bg"], fg=C["textarea_fg"],
                           insertbackground=C["cursor"],
                           selectbackground=C["sel_bg"],
                           relief="flat", wrap="word",
                           font=("Courier New", 10),
                           padx=16, pady=12, undo=True)
        self._ta.grid(row=0, column=0, sticky="nsew")
        vsc = tk.Scrollbar(ta_frame, command=self._ta.yview,
                           bg=C["scrollbar"], troughcolor=C["bg"],
                           width=10, relief="flat")
        vsc.grid(row=0, column=1, sticky="ns")
        self._ta.configure(yscrollcommand=vsc.set)
        _ta_ss = SmoothScroller(self._ta, self.win)

        def _ta_scroll(e):
            if e.num == 4 or (hasattr(e, "delta") and e.delta > 0):
                _ta_ss.on_scroll(-1)
            else:
                _ta_ss.on_scroll(1)
        self._ta.bind("<Button-4>", _ta_scroll)
        self._ta.bind("<Button-5>", _ta_scroll)

        self._ta.bind("<KeyRelease>", self._update_word_count)

        # Bottom bar
        bot = tk.Frame(self.win, bg=C["surface"],
                        highlightthickness=1, highlightbackground=C["border"],
                        pady=8)
        bot.pack(fill="x", side="bottom")

        tk.Button(bot, text="  Copy All  ",
                  font=("Courier New", 9, "bold"),
                  bg=C["surface2"], fg=C["text2"], relief="flat",
                  padx=12, pady=5,
                  activebackground=C["border"],
                  command=self._copy_all).pack(side="left", padx=(12, 4))

        tk.Button(bot, text="  Clear  ",
                  font=("Courier New", 9, "bold"),
                  bg=C["surface2"], fg=C["text2"], relief="flat",
                  padx=12, pady=5,
                  activebackground=C["border"],
                  command=lambda: (self._ta.delete("1.0", "end"),
                                   self._update_word_count())).pack(side="left", padx=4)

        tk.Button(bot, text="  Load into TTS Editor  ",
                  font=("Courier New", 9, "bold"),
                  bg=C["speak_bg"], fg="white", relief="flat",
                  padx=14, pady=5, cursor="hand2",
                  activebackground=C["speak_hover"],
                  activeforeground="white",
                  command=self._load_to_editor).pack(side="right", padx=(4, 12))

        tk.Button(bot, text="  Save as TXT  ",
                  font=("Courier New", 9, "bold"),
                  bg=C["surface2"], fg=C["text2"], relief="flat",
                  padx=12, pady=5,
                  activebackground=C["border"],
                  command=self._save_txt).pack(side="right", padx=4)

    # ── File picking ─────────────────────────────────────────────────────────
    def _pick_file(self):
        dlg  = AudioFileDialog(self.win)
        path = dlg.result
        if not path:
            return
        ext = Path(path).suffix.lower()
        if ext not in self.SUPPORTED_AUDIO:
            self._set_status(f"Unsupported format: {ext}", C["error"])
            return
        self._audio_path = path
        name = Path(path).name
        self._drop_lbl.configure(text=f"✓  {name}", fg=C["success"])
        self._set_status(f"Ready: {name}", C["accent2"])

    # ── Transcription ────────────────────────────────────────────────────────
    def _start_transcribe(self):
        if not self._audio_path:
            self._set_status("Please select an audio file first.", C["warning"])
            return
        if self._running:
            return

        self._running = True
        self._cancel_requested = False
        self._transcribe_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._prog_var.set(0)
        self._set_status("Starting transcription...", C["warning"])

        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _cancel(self):
        self._cancel_requested = True
        self._set_status("Cancelling...", C["warning"])

    def _transcribe_worker(self):
        def _ui(fn):
            """Schedule fn on main thread, silently drop if window destroyed."""
            def _safe():
                try:
                    if self.win.winfo_exists():
                        fn()
                except tk.TclError:
                    pass
                except Exception:
                    pass
            try: self.win.after(0, _safe)
            except Exception: pass

        def _status(txt, col=None):
            _ui(lambda t=txt, c=col or C["warning"]:
                self._status_lbl.configure(text=t, fg=c))

        try:
            import importlib
            eng_choice = self._engine_var.get()
            model_size = self._model_var.get()

            has_whisper = importlib.util.find_spec("faster_whisper") is not None
            has_vosk    = importlib.util.find_spec("vosk") is not None

            # Resolve engine
            if "faster-whisper" in eng_choice:
                use = "whisper"
            elif "vosk" in eng_choice:
                use = "vosk"
            else:
                # Auto — prefer whisper, fall back to vosk
                if has_whisper: use = "whisper"
                elif has_vosk:  use = "vosk"
                else:
                    _status("No engine installed. See setup below.", C["error"])
                    self._show_setup_hint()
                    return

            transcript = ""

            if use == "whisper":
                if not has_whisper:
                    _status("faster-whisper not installed — click Install below", C["error"])
                    self._show_setup_hint("faster-whisper")
                    return
                _status(f"Loading Whisper '{model_size}' model…", C["warning"])
                _ui(lambda: self._prog_var.set(10))
                try:
                    from faster_whisper import WhisperModel
                    import os as _os
                    # Force offline — never attempt to download during transcription.
                    # If the model isn't cached, tell the user to switch to 'tiny'.
                    cache = _os.path.expanduser("~/.cache/huggingface/hub")
                    model_id = f"Systran/faster-whisper-{model_size}"
                    model_dir = _os.path.join(cache, model_id.replace("/", "--"))
                    model_cached = (
                        _os.path.exists(model_dir) or
                        _os.path.exists(_os.path.expanduser(f"~/.cache/faster_whisper/{model_size}"))
                    )
                    if not model_cached and model_size != "tiny":
                        _status(
                            f"Whisper '{model_size}' not cached — switching to 'tiny' (offline mode)."
                            " Change Model selector and re-transcribe for better quality.",
                            C["warning"])
                        model_size = "tiny"
                    model = WhisperModel(
                        model_size, device="cpu", compute_type="int8",
                        local_files_only=model_cached   # prevent network calls if cached
                    )
                except Exception as e:
                    err = str(e)
                    if "ConnectError" in err or "Name or service" in err or "network" in err.lower():
                        _status(
                            "Network error: faster-whisper tried to download a model. "
                            "Select 'tiny' model (it's already cached offline).",
                            C["error"])
                    elif "model.bin" in err or "No such file" in err:
                        _status(
                            f"Whisper '{model_size}' model not found locally. "
                            "Select 'tiny' from the Model dropdown (cached) and retry.",
                            C["error"])
                    else:
                        _status(f"Whisper load error: {err[:80]}", C["error"])
                    return

                _status("Transcribing… (this may take a moment)", C["warning"])
                _ui(lambda: self._prog_var.set(30))
                try:
                    segments, info = model.transcribe(
                        self._audio_path, beam_size=5,
                        word_timestamps=False)
                except Exception as e:
                    _status(f"Transcription error: {str(e)[:80]}", C["error"])
                    return
                parts = []
                duration = getattr(info, "duration", None)
                for seg in segments:
                    if self._cancel_requested:
                        break
                    parts.append(seg.text.strip())
                    if duration and duration > 0:
                        pct = 30 + (seg.end / duration) * 65
                        _ui(lambda p=min(pct, 95): self._prog_var.set(p))
                    _status(f"Transcribing… {len(parts)} segments", C["warning"])
                transcript = " ".join(parts)

            elif use == "vosk":
                if not has_vosk:
                    _status("vosk not installed — click Install below", C["error"])
                    self._show_setup_hint("vosk")
                    return
                import subprocess, tempfile, os, json
                _status("Converting audio to WAV for Vosk...", C["warning"])
                _ui(lambda: self._prog_var.set(15))
                # Convert to 16kHz mono WAV via ffmpeg
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                    tmp = tf.name
                try:
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-i", self._audio_path,
                         "-ar", "16000", "-ac", "1", tmp],
                        capture_output=True, timeout=120)
                    if r.returncode != 0:
                        _status("ffmpeg required for vosk conversion. sudo apt install ffmpeg", C["error"])
                        return
                    _ui(lambda: self._prog_var.set(40))
                    from vosk import Model as VoskModel, KaldiRecognizer
                    _status("Loading Vosk model...", C["warning"])
                    # Cache the model so it's only loaded once per session
                    # (avoids double LOG spam and 5+ second reload each time)
                    if not hasattr(AudioToTextWindow, "_vosk_model_cache"):
                        # Silence any remaining C++ stderr chatter during first load
                        import sys as _sys
                        _null_fd = os.open(os.devnull, os.O_WRONLY)
                        _old_stderr = os.dup(2)
                        os.dup2(_null_fd, 2)
                        os.close(_null_fd)
                        try:
                            AudioToTextWindow._vosk_model_cache = VoskModel(lang="en-us")
                        finally:
                            os.dup2(_old_stderr, 2)
                            os.close(_old_stderr)
                    vosk_model = AudioToTextWindow._vosk_model_cache
                    rec = KaldiRecognizer(vosk_model, 16000)
                    rec.SetWords(True)
                    parts = []
                    total_bytes = os.path.getsize(tmp)
                    read_bytes  = 0
                    with open(tmp, "rb") as f:
                        f.read(44)   # skip WAV header
                        while True:
                            if self._cancel_requested:
                                break
                            data = f.read(8000)
                            if not data:
                                break
                            read_bytes += len(data)
                            if rec.AcceptWaveform(data):
                                res = json.loads(rec.Result())
                                parts.append(res.get("text", ""))
                            pct = 40 + (read_bytes / max(total_bytes, 1)) * 55
                            _ui(lambda p=min(pct, 95): self._prog_var.set(p))
                    res = json.loads(rec.FinalResult())
                    parts.append(res.get("text", ""))
                    transcript = " ".join(p for p in parts if p)
                finally:
                    try: os.unlink(tmp)
                    except Exception: pass

            elif use == "google":
                if not has_sr:
                    _status("SpeechRecognition not installed — click Install below", C["error"])
                    self._show_setup_hint("SpeechRecognition")
                    return
                import subprocess, tempfile, os, math
                _status("Converting audio for Google STT...", C["warning"])
                _ui(lambda: self._prog_var.set(20))
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                    tmp = tf.name
                try:
                    # Convert to 16kHz mono WAV — Google prefers this format
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", self._audio_path,
                         "-ar", "16000", "-ac", "1",
                         # Normalise volume so quiet recordings are louder
                         "-af", "loudnorm",
                         tmp],
                        capture_output=True, timeout=120)
                    _ui(lambda: self._prog_var.set(50))
                    import speech_recognition as sr_lib
                    r = sr_lib.Recognizer()
                    # Tune recogniser for accuracy
                    r.energy_threshold      = 300   # lower = picks up quieter speech
                    r.dynamic_energy_threshold = True
                    r.pause_threshold       = 0.8   # seconds of silence = end of phrase

                    # Chunk the audio into ≤30s segments (Google free API limit is ~60s
                    # but chunking at 30s avoids RequestError and improves accuracy)
                    CHUNK_SECONDS = 30
                    parts = []
                    with sr_lib.AudioFile(tmp) as src:
                        duration = src.DURATION if hasattr(src, "DURATION") else None
                        if duration is None:
                            # Fallback: record whole file
                            audio_data = r.record(src)
                            chunks = [audio_data]
                        else:
                            n_chunks = max(1, math.ceil(duration / CHUNK_SECONDS))
                            chunks = []
                            for i in range(n_chunks):
                                offset = i * CHUNK_SECONDS
                                dur    = min(CHUNK_SECONDS, duration - offset)
                                try:
                                    chunks.append(r.record(src, duration=dur, offset=0 if i==0 else None))
                                except Exception:
                                    pass

                    total_chunks = max(len(chunks), 1)
                    for ci, chunk in enumerate(chunks):
                        if self._cancel_requested:
                            break
                        pct = 50 + (ci / total_chunks) * 45
                        _ui(lambda p=pct: self._prog_var.set(p))
                        _status(f"Google STT chunk {ci+1}/{total_chunks}...", C["warning"])
                        try:
                            part = r.recognize_google(
                                chunk,
                                language="en-US",
                                show_all=False,
                            )
                            parts.append(part)
                        except sr_lib.UnknownValueError:
                            pass   # silent / unrecognised chunk — skip
                        except sr_lib.RequestError as e:
                            _status(f"Google STT request failed: {e}", C["error"])
                            return
                    transcript = " ".join(p for p in parts if p)
                finally:
                    try: os.unlink(tmp)
                    except Exception: pass

            _ui(lambda: self._prog_var.set(100))

            if self._cancel_requested:
                _status("Cancelled.", C["muted"])
            elif transcript.strip():
                t = transcript.strip()
                def _insert(text=t):
                    self._ta.delete("1.0", "end")
                    self._ta.insert("1.0", text)
                    # Keep view at top — don't auto-scroll to bottom after insert
                    self._ta.mark_set("insert", "1.0")
                    self._ta.yview_moveto(0.0)
                    self._update_word_count()
                _ui(_insert)
                _status(f"✓ Done — {len(transcript.split())} words transcribed", C["success"])
            else:
                _status("No speech detected in file.", C["warning"])

        except Exception as e:
            import traceback as _tb
            bug_tracker.error(f"ATT worker: {e}\n{_tb.format_exc()}")
            try:
                self.win.after(0, lambda err=str(e):
                    self._status_lbl.configure(
                        text=f"Error: {err[:80]}", fg=C["error"]))
            except Exception:
                pass
        finally:
            self._running = False
            self._cancel_requested = False
            def _done():
                try:
                    if not self.win.winfo_exists():
                        return
                    self._transcribe_btn.configure(state="normal")
                    self._cancel_btn.configure(state="disabled")
                except tk.TclError:
                    pass
            try: self.win.after(0, _done)
            except Exception: pass

    def _show_setup_hint(self, pkg: str = None):
        """Show install instructions and a one-click Install button."""
        if pkg:
            hint = (
                f"'{pkg}' is not installed.\n\n"
                f"Click the Install button to install it automatically,\n"
                f"or run in a terminal:\n\n"
                f"  pip install {pkg}\n\n"
                f"After installing, click Transcribe again."
            )
        else:
            hint = (
                "No transcription engine is installed.\n\n"
                "Install one of the following:\n\n"
                "  pip install faster-whisper   (best quality, offline)\n"
                "  pip install vosk             (lightweight, offline)\n"
                "  pip install SpeechRecognition (online via Google)\n\n"
                "Or click an Install button below, then click Transcribe again."
            )
        try:
            def _do():
                self._ta.delete("1.0", "end")
                self._ta.insert("1.0", hint)
                self._status_lbl.configure(
                    text=f"{'Install ' + pkg if pkg else 'No engine'} — see transcript area",
                    fg=C["warning"])
                # Add one-click install button if specific package requested
                if pkg and not getattr(self, "_install_btn_added", False):
                    self._install_btn_added = True
                    install_btn = tk.Button(
                        self.win,
                        text=f"  ⬇  Install {pkg} now  ",
                        font=("Courier New", 9, "bold"),
                        bg=C["accent"], fg="white", relief="flat",
                        padx=12, pady=6, cursor="hand2",
                        activebackground=C["speak_hover"],
                        activeforeground="white",
                        command=lambda p=pkg: self._auto_install(p, install_btn))
                    install_btn.pack(pady=(4, 0))
            self.win.after(0, _do)
        except Exception:
            pass

    def _auto_install(self, pkg: str, btn):
        """Install a package and update the button with progress."""
        import subprocess, sys
        try:
            btn.configure(text=f"Installing {pkg}…", state="disabled",
                          bg=C["warning"])
        except Exception:
            pass

        def _worker():
            pip = str(Path(sys.executable).parent / "pip")
            try:
                r = subprocess.run(
                    [pip, "install", pkg, "--quiet"],
                    capture_output=True, text=True, timeout=300)
                if r.returncode == 0:
                    def _ok():
                        try:
                            btn.configure(text=f"✓ {pkg} installed — click Transcribe",
                                          bg=C["success"])
                            self._status_lbl.configure(
                                text=f"✓ {pkg} installed. Click Transcribe.",
                                fg=C["success"])
                            self._install_btn_added = False
                        except Exception: pass
                    try: self.win.after(0, _ok)
                    except Exception: pass
                else:
                    def _fail():
                        try:
                            btn.configure(text=f"✗ Install failed — see terminal",
                                          bg=C["error"], state="normal")
                        except Exception: pass
                    try: self.win.after(0, _fail)
                    except Exception: pass
            except Exception as e:
                def _err():
                    try:
                        btn.configure(text=f"✗ Error: {str(e)[:40]}", state="normal")
                    except Exception: pass
                try: self.win.after(0, _err)
                except Exception: pass

        threading.Thread(target=_worker, daemon=True).start()

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _set_status(self, txt, col=None):
        def _do(t=txt, c=col or C["muted"]):
            try:
                if self.win.winfo_exists():
                    self._status_lbl.configure(text=t, fg=c)
            except tk.TclError:
                pass
        try: self.win.after(0, _do)
        except Exception: pass

    def _update_word_count(self, _=None):
        try:
            text = self._ta.get("1.0", "end").strip()
            if text:
                w = len(text.split())
                self._word_lbl.configure(text=f"  {w:,} words · {len(text):,} chars")
            else:
                self._word_lbl.configure(text="")
        except Exception:
            pass

    def _copy_all(self):
        text = self._ta.get("1.0", "end").strip()
        if text:
            self.win.clipboard_clear()
            self.win.clipboard_append(text)
            self._set_status("Copied to clipboard.", C["success"])

    def _save_txt(self):
        text = self._ta.get("1.0", "end").strip()
        if not text:
            return
        dlg = TTSSaveDialog(self.win, title="Save Transcript",
                            default_ext=".txt",
                            filetypes=[("Text file", ".txt")],
                            default_name="transcript")
        if dlg.result:
            try:
                Path(dlg.result).write_text(text, encoding="utf-8")
                self._set_status(f"✓ Saved: {dlg.result}", C["success"])
            except Exception as e:
                self._set_status(f"Save failed: {e}", C["error"])

    def _load_to_editor(self):
        text = self._ta.get("1.0", "end").strip()
        if not text:
            self._set_status("Nothing to load — transcribe first.", C["warning"])
            return
        if self._on_load_cb:
            self._on_load_cb(text)
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════
class TTSVoicesApp:
    def __init__(self):
        self.cfg          = load_config()
        self._all_voices  = []
        self._stop_flag   = threading.Event()
        self._is_speaking = False
        self._fire_plugin_cbs('_plugin_speak_stop_cbs')
        self._is_exporting = False
        self._wav_buffer          = []
        self._current_file         = ""
        self._fallback_warned      = False
        self._audio_start_time     = 0.0
        self._last_progress_update = 0.0
        self._playing_chunk_abs      = 0    # absolute chunk index mid-play (for resume)
        # Widget color registry: list of (widget, {tk_attr: color_key})
        # Used by _apply_theme_fast to update colors without rebuilding
        self._themed_widgets: list = []
        # Real-time highlight sync state
        self._highlight_active      = False
        self._highlight_enabled     = True
        self._chunk_highlight_data  = {}
        self._current_chunk_index   = 0
        self._theme_key   = self.cfg.get("theme", "dark")
        # User scroll lock: suppress auto-scroll for N ms after manual scroll
        self._user_scrolled_at      = 0   # time.monotonic() of last user scroll
        self._USER_SCROLL_LOCK_MS   = 10000  # 10 s lock after user scroll
        # Auto-scroll toggle (independent of highlight sync)
        self._auto_scroll_enabled   = True

        C.update(THEMES.get(self._theme_key, THEMES["dark"]))

        # ── Build the Tk window FIRST so it appears immediately ────────────────
        self.root = tk.Tk()
        self.root.title(f"TTS Voices {__version__}")
        self.root.minsize(900, 500)  # prevent nav pill from clipping
        self.root.geometry("1100x680")
        self.root.minsize(860, 560)
        self.root.configure(bg=C["bg"])
        try:
            self.root.tk.call("tk", "appname", "ttsvoices")
            self.root.wm_iconname("TTS Voices")
        except Exception:
            pass

        self.speed_var    = tk.DoubleVar(value=self.cfg.get("speed", 1.3))
        self.pitch_var    = tk.DoubleVar(value=self.cfg.get("pitch", 1.0))
        raw_vol = self.cfg.get("volume", 63)
        if raw_vol > 100:
            raw_vol = max(1, int(raw_vol / 327.67))
        self.volume_var   = tk.IntVar(value=raw_vol)
        self.voice_var    = tk.StringVar()
        self.status_var   = tk.StringVar(value="LOADING…")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._style_ttk()
        self._build_ui()

        # Show loading state in status pill immediately
        self._set_status("LOADING…", C["warning"])
        # Disable interactive controls until engines are ready
        self._speak_btn.set_colors("#333344", "#333344")

        # speed + pitch → may trigger re-synthesis restart if speaking
        for v in (self.speed_var, self.pitch_var):
            v.trace_add("write", self._on_cfg_change)
        # volume → config save + audio level only, never re-synthesizes
        self.volume_var.trace_add("write", self._on_volume_change)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Kick off background engine load ───────────────────────────────────
        _bg = threading.Thread(target=_load_engines_background, daemon=True)
        _bg.start()

        # ── Start resource monitor (no-op if psutil not installed) ────────────
        _resource_monitor.register(self._on_resources)
        _resource_monitor.start(self.root)

        # Poll every 80 ms until engines are ready, then finish init
        self.root.after(80, self._poll_engines_ready)

    def _poll_engines_ready(self):
        """Called every 80 ms until _engines_ready is set, then completes init."""
        if not _engines_ready.is_set():
            self.root.after(80, self._poll_engines_ready)
            return
        # Engines loaded — complete initialisation on the main thread
        self._finish_init()

    def _finish_init(self):
        """Run after _load_engines_background() succeeds — sets up provider, voices, etc."""

        # ── Provider selection ─────────────────────────────────────────────────
        saved_prov = self.cfg.get("provider", "CPU")
        avail      = voices.get_available_providers()

        def _best_gpu():
            for p in ("CUDAExecutionProvider", "ROCMExecutionProvider",
                      "TensorrtExecutionProvider", "OpenVINOExecutionProvider",
                      "DmlExecutionProvider"):
                if p in avail: return p
            return None

        best_gpu = _best_gpu()
        if saved_prov == "CPU":
            voices.set_provider("CPUExecutionProvider")
        elif saved_prov in ("GPU", "CUDA", "CUDA (NVIDIA)") and "CUDAExecutionProvider" in avail:
            voices.set_provider("CUDAExecutionProvider")
        elif saved_prov in ("ROCm (AMD)",) and "ROCMExecutionProvider" in avail:
            voices.set_provider("ROCMExecutionProvider")
        elif saved_prov in ("OpenVINO", "Intel GPU (OpenVINO)") and "OpenVINOExecutionProvider" in avail:
            voices.set_provider("OpenVINOExecutionProvider")
        elif best_gpu:
            voices.set_provider(best_gpu)
        else:
            voices.set_provider("CPUExecutionProvider")

        self._load_voices()
        self._refresh_engine_status()
        self._update_gpu_btn()

        # Re-enable speak button now that engines are ready
        self._speak_btn.set_colors(C["speak_bg"], C["speak_hover"])
        self._set_status("READY", C["success"])

        # Wire audio highlight callbacks
        self._setup_audio_callbacks()

        # ── Export progress bar wiring ────────────────────────────────────────
        # _export_progress_var → unified bar ONLY during export.
        # progress_var (speech) is intentionally NOT mirrored here — the top
        # header bar already shows speech progress. The export bar stays at 0
        # while the voice is speaking so it never glows during playback.
        def _mirror_export(*_):
            try: self._unified_progress_var.set(self._export_progress_var.get())
            except Exception: pass
        self._export_progress_var.trace_add("write", _mirror_export)

        # Apply saved volume
        self.root.after(100, self._apply_volume)

        startup_ms = (time.monotonic() - _STARTUP_T0) * 1000
        # Load plugins from ~/.ttsvoices/plugins/
        self._load_plugins()

        # Kick off update check (respects auto_update_check toggle)
        self._start_update_check_if_enabled()

        bug_tracker.info(
            f"TTS Voices {__version__} ready  theme={self._theme_key}"
            f"  provider={voices.get_current_provider()}"
            f"  startup={startup_ms:.0f}ms"
        )



    def _style_ttk(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TProgressbar", troughcolor=C["surface2"], background=C["accent"],
                    bordercolor=C["border"], lightcolor=C["accent"], darkcolor=C["accent"])
        s.configure("TCombobox", fieldbackground=C["surface2"], background=C["surface2"],
                    foreground=C["text"], selectbackground=C["accent"],
                    selectforeground="white", bordercolor=C["border"], arrowcolor=C["accent2"])
        s.map("TCombobox", fieldbackground=[("readonly",C["surface2"])])

        # ── Notebook (Voice Library tabs) ─────────────────────────────────────
        s.configure("TNotebook",
                    background=C["bg"],
                    bordercolor=C["border"],
                    tabmargins=[2, 4, 0, 0])
        s.configure("TNotebook.Tab",
                    background=C["surface"],
                    foreground=C["text2"],
                    padding=[14, 6],
                    font=("Courier New", 9),
                    bordercolor=C["border"],
                    focuscolor=C["bg"])
        s.map("TNotebook.Tab",
              background=[("selected", C["surface2"]),
                          ("active",   C["border"])],
              foreground=[("selected", C["accent2"]),
                          ("active",   C["text"])],
              expand=[("selected", [1, 1, 1, 0])])

    # ── Build ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header()
        self._build_progress_bar()
        self._build_body()

    def _build_header(self):
        self._hdr = tk.Frame(self.root, bg=C["header_bg"],
                              highlightthickness=1, highlightbackground=C["border"])
        self._hdr.pack(fill="x")

        logo = tk.Frame(self._hdr, bg=C["header_bg"])
        logo.pack(side="left", padx=16, pady=8)
        tk.Label(logo, text="🔊", font=("Segoe UI Emoji", 18),
                 fg=C["accent2"], bg=C["header_bg"]).pack(side="left", padx=(0, 8))
        tf = tk.Frame(logo, bg=C["header_bg"])
        tf.pack(side="left")
        tk.Label(tf, text="TTS Voices", font=("Segoe UI", 15, "bold"),
                 fg=C["text"], bg=C["header_bg"]).pack(anchor="w")
        self._subtitle_lbl = tk.Label(tf,
                 text=f"v{__version__}  ·  Unlimited Audio Generation  ·  CPU 0%  RAM 0%",
                 font=("Courier New", 7, "bold"),
                 fg=C["accent2"], bg=C["header_bg"])
        self._subtitle_lbl.pack(anchor="w")

        nav = tk.Frame(self._hdr, bg=C["header_bg"])
        nav.pack(side="right", padx=10, pady=6)

        self._nav_btns = []
        self._make_nav_btn(nav, "⚙ Settings",      self._open_settings)
        self._make_nav_btn(nav, "◑ Theme",           self._open_theme_picker)
        self._gpu_btn = self._make_nav_btn(nav, "⚡ CPU", self._toggle_gpu, accent=True)
        self._make_nav_btn(nav, "🎙 Audio→Text",     self._open_audio_to_text)
        self._make_nav_btn(nav, "📚 Voice Library",  self._open_voice_library)
        self._make_nav_btn(nav, "🐞 Bug Log",        self._open_bug_tracker)
        self._make_nav_btn(nav, "⊕ Plugins", self._open_plugins_manager)
        self._update_btn = self._make_nav_btn(nav, "⟳ Updates", self._on_update_btn_click)
        self._update_btn._lbl.configure(fg=C["text2"])  # quiet by default
        self._update_available_version = ""

        self._status_pill = tk.Label(nav, textvariable=self.status_var,
                                      font=("Courier New", 9, "bold"),
                                      fg=C["success"], bg=C["pill_bg"],
                                      padx=10, pady=4,
                                      highlightthickness=1,
                                      highlightbackground=C["success"])
        self._status_pill.pack(side="left", padx=(6, 0))

    def _make_nav_btn(self, parent, text, command, accent=False):
        nbg = C["accent"]     if accent else C["nav_btn"]
        hbg = C["accent_dim"] if accent else C["nav_hover"]
        btn = GlowButton(parent, text=text, command=command,
                         normal_bg=nbg, hover_bg=hbg,
                         fg="white" if accent else C["text"],
                         font=("Courier New",8,"bold"))
        btn._lbl.configure(padx=10, pady=5)
        btn.pack(side="left", padx=2)
        btn._is_accent = accent   # remember for theme recolor
        if hasattr(self, "_nav_btns"):
            self._nav_btns.append(btn)
        return btn

    def _build_progress_bar(self):
        pbf = tk.Frame(self.root, bg=C["surface2"], pady=0)
        pbf.pack(fill="x")
        self._pb = ttk.Progressbar(pbf, variable=self.progress_var, maximum=100)
        self._pb.pack(fill="x", side="left", expand=True)
        self._pct_lbl = tk.Label(pbf, text="0%", font=("Courier New",7,"bold"),
                                  fg=C["accent2"], bg=C["surface2"], width=5)
        self._pct_lbl.pack(side="right", padx=4)
        self.progress_var.trace_add("write", lambda *_:
            self._pct_lbl.configure(text=f"{self.progress_var.get():.0f}%"))

    def _build_body(self):
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1); body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)
        self._build_left(body)
        self._build_right(body)

    def _build_left(self, parent):
        left = tk.Frame(parent, bg=C["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(12,6), pady=10)
        left.rowconfigure(1, weight=1); left.columnconfigure(0, weight=1)

        top_bar = tk.Frame(left, bg=C["bg"])
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0,4))
        self._word_lbl = tk.Label(top_bar, text="0 words · 0 chars",
                                   font=("Courier New",8), fg=C["muted"], bg=C["bg"])
        self._word_lbl.pack(side="left", padx=4)
        # Bookmark indicator — shown when a save point is active
        bf = tk.Frame(top_bar, bg=C["bg"]); bf.pack(side="right")
        self._clear_btn = GlowButton(bf, text="✕ Clear", command=self._clear_text,
                   normal_bg=C["surface2"], hover_bg=C["border"],
                   fg=C["text2"], font=("Courier New",8))
        self._clear_btn.pack(side="left", padx=2)
        self._load_btn = GlowButton(bf, text="⬆ Load File", command=self._load_file,
                   normal_bg=C["accent_dim"], hover_bg=C["accent"],
                   fg=C["accent2"], font=("Courier New",8,"bold"))
        self._load_btn.pack(side="left", padx=2)

        ta_frame = tk.Frame(left, bg=C["border"], pady=1, padx=1)
        ta_frame.grid(row=1, column=0, sticky="nsew")
        ta_frame.rowconfigure(0, weight=1); ta_frame.columnconfigure(0, weight=1)

        self._textarea = tk.Text(ta_frame,
                                  bg=C["textarea_bg"], fg=C["textarea_fg"],
                                  insertbackground=C["cursor"],
                                  selectbackground=C["sel_bg"],
                                  selectforeground=C["text"],
                                  relief="flat", wrap="word",
                                  font=("Courier New",11),
                                  padx=20, pady=18, undo=True)
        self._textarea.grid(row=0, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(ta_frame, command=self._textarea.yview,
                                  bg=C["scrollbar"], troughcolor=C["bg"],
                                  width=10, relief="flat")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._textarea.configure(yscrollcommand=scrollbar.set)
        # Attach physics-based smooth scroller to the textarea
        self._textarea_scroller = SmoothScroller(self._textarea, self.root)
        self._set_placeholder()
        # Belt-AND-suspenders: clear placeholder on focus, click, OR first keypress
        self._textarea.bind("<FocusIn>",    self._remove_placeholder)
        self._textarea.bind("<Button-1>",   self._remove_placeholder)
        self._textarea.bind("<Key>",        self._remove_placeholder)
        self._textarea.bind("<FocusOut>",   self._maybe_placeholder)
        self._textarea.bind("<KeyRelease>", self._update_wordcount)
        self._placeholder_active = True
        # Mouse wheel scrolling for the textarea
        def _ta_scroll(e):
            try:
                if not self._textarea.winfo_exists(): return
                # Stamp user-scroll lock so _highlight_word won't fight us
                import time as _t
                self._user_scrolled_at = _t.monotonic()
                if e.num == 4 or (hasattr(e, "delta") and e.delta > 0):
                    self._textarea_scroller.on_scroll(-1)
                else:
                    self._textarea_scroller.on_scroll(1)
            except Exception:
                pass
        self._textarea.bind("<MouseWheel>", _ta_scroll)
        self._textarea.bind("<Button-4>",   _ta_scroll)
        self._textarea.bind("<Button-5>",   _ta_scroll)
        # Also bind scroll to the surrounding frame and ta_frame so the
        # scroll works even when the cursor is on the 1-px border gap
        left.bind("<Button-4>",    _ta_scroll)
        left.bind("<Button-5>",    _ta_scroll)
        ta_frame.bind("<Button-4>", _ta_scroll)
        ta_frame.bind("<Button-5>", _ta_scroll)

        # Stamp user_scrolled_at on any key activity in the textarea too —
        # if the user is editing, auto-scroll during playback should not fight them.
        def _ta_key_stamp(e):
            try:
                import time as _t
                self._user_scrolled_at = _t.monotonic()
            except Exception:
                pass
        self._textarea.bind("<KeyPress>", _ta_key_stamp, "+")

    def _build_right(self, parent):
        self._right = tk.Frame(parent, bg=C["surface"],
                                highlightthickness=1, highlightbackground=C["border"],
                                width=260)
        self._right.grid(row=0, column=1, sticky="nsew", padx=(0,12), pady=10)
        self._right.pack_propagate(False)

        SectionHeader(self._right, "VOICE", "◆").pack(fill="x")
        vf = tk.Frame(self._right, bg=C["surface"]); vf.pack(fill="x", padx=10, pady=4)
        self._voice_name_lbl = tk.Label(vf, text="Loading voices...",
                                         font=("Courier New",10,"bold"),
                                         fg=C["text"], bg=C["surface"],
                                         wraplength=210, justify="left")
        self._voice_name_lbl.pack(anchor="w")
        self._voice_engine_lbl = tk.Label(vf, text="",
                                           font=("Courier New",7), fg=C["muted"], bg=C["surface"])
        self._voice_engine_lbl.pack(anchor="w", pady=(1,4))
        self._voice_combo = ttk.Combobox(vf, textvariable=self.voice_var,
                                          state="readonly", font=("Courier New",9), width=26)
        self._voice_combo.pack(fill="x")
        self._voice_combo.bind("<<ComboboxSelected>>", self._on_voice_change)

        # ── Zero-shot reference audio panel ─────────────────────────────────
        # Shown only when a zero-shot cloning engine (Chatterbox, OmniVoice…)
        # is selected — hidden for predefined-voice engines (Kokoro, espeak).
        self._ref_frame = tk.Frame(vf, bg=C["surface"])
        # (packed/unpacked dynamically by _update_voice_display)
        tk.Label(self._ref_frame,
                 text="Reference audio (voice to clone):",
                 font=("Courier New", 7), fg=C["muted"], bg=C["surface"]
                 ).pack(anchor="w", pady=(6, 1))

        ref_row = tk.Frame(self._ref_frame, bg=C["surface"])
        ref_row.pack(fill="x")
        self._ref_audio_var = tk.StringVar(value="")
        ref_entry = tk.Entry(ref_row, textvariable=self._ref_audio_var,
                             font=("Courier New", 7), bg=C["surface2"],
                             fg=C["text2"], relief="flat",
                             insertbackground=C["cursor"], bd=2)
        ref_entry.pack(side="left", fill="x", expand=True, ipady=2)
        tk.Button(ref_row, text="…", font=("Courier New", 8),
                  bg=C["surface2"], fg=C["accent2"], relief="flat",
                  cursor="hand2", padx=4,
                  activebackground=C["border"],
                  command=self._browse_ref_audio).pack(side="left", padx=(2, 0))

        tk.Label(self._ref_frame,
                 text="Leave blank to use Chatterbox default voice.",
                 font=("Courier New", 6), fg=C["muted"], bg=C["surface"]
                 ).pack(anchor="w", pady=(1, 0))

        SectionHeader(self._right, "CONTROLS", "◆").pack(fill="x", pady=(6,0))
        cf = tk.Frame(self._right, bg=C["surface"]); cf.pack(fill="x", padx=8, pady=4)
        NumericControl(cf,"Speed ×",self.speed_var,0.5,3.0,0.1,"{:.1f}").pack(anchor="w",pady=2)
        NumericControl(cf,"Pitch",  self.pitch_var,0.5,2.0,0.1,"{:.1f}").pack(anchor="w",pady=2)
        NumericControl(cf,"Volume %",self.volume_var,0,100,5,"{:d}").pack(anchor="w",pady=2)

        SectionHeader(self._right, "PLAYBACK", "▶").pack(fill="x", pady=(8,0))
        pf = tk.Frame(self._right, bg=C["surface"]); pf.pack(fill="x", padx=10, pady=6)
        self._speak_btn = GlowButton(pf, text="▶  SPEAK", command=self._on_speak,
                                      normal_bg=C["speak_bg"], hover_bg=C["speak_hover"],
                                      fg="white", font=("Courier New",12,"bold"))
        self._speak_btn.pack(fill="x", pady=(0,5))
        self._speak_btn._lbl.configure(pady=11)
        self._stop_btn = GlowButton(pf, text="■  STOP", command=self._on_stop,
                                     normal_bg=C["stop_bg"], hover_bg=C["stop_hover"],
                                     fg="white", font=("Courier New",9,"bold"))
        self._stop_btn.pack(fill="x")
        self._stop_btn._lbl.configure(pady=6)

        # ── Separator ────────────────────────────────────────────────────
        tk.Frame(pf, bg=C["border"], height=1).pack(fill="x", pady=(8,4))

        # ── Highlight sync toggle ────────────────────────────────────────
        hl_row = tk.Frame(pf, bg=C["surface"])
        hl_row.pack(fill="x", pady=(0,2))
        tk.Label(hl_row, text="Highlight Sync",
                 font=("Courier New", 8), fg=C["text2"], bg=C["surface"]).pack(side="left", padx=(2,0))
        self._hl_toggle = PillToggle(hl_row, state=True,
                                     callback=self._on_hl_toggle)
        self._hl_toggle.pack(side="right", padx=(0,2))

        # ── Auto-scroll toggle ───────────────────────────────────────────
        as_row = tk.Frame(pf, bg=C["surface"])
        as_row.pack(fill="x", pady=(0,2))
        tk.Label(as_row, text="Auto-Scroll",
                 font=("Courier New", 8), fg=C["text2"], bg=C["surface"]).pack(side="left", padx=(2,0))
        self._as_toggle = PillToggle(as_row, state=True,
                                     callback=self._on_as_toggle)
        self._as_toggle.pack(side="right", padx=(0,2))

        # ── Unified progress bar — synthesis while speaking, export while exporting ──
        # This bar (under Auto-Scroll) is the single progress indicator for all operations.
        # Export status text is routed into _play_progress_str so it appears right here.
        pb_frame = tk.Frame(pf, bg=C["surface"],
                            highlightthickness=1, highlightbackground=C["border"])
        pb_frame.pack(fill="x", pady=(4, 0))
        self._unified_progress_var = tk.DoubleVar(value=0.0)
        self._play_pb = ttk.Progressbar(pb_frame, variable=self._unified_progress_var,
                                         maximum=100, length=220)
        self._play_pb.pack(fill="x")
        self._play_progress_str = tk.StringVar(value="Ready")
        self._play_progress_lbl = tk.Label(pb_frame, textvariable=self._play_progress_str,
                 font=("Courier New", 7), fg=C["muted"],
                 bg=C["surface"], anchor="w")
        self._play_progress_lbl.pack(fill="x", padx=4, pady=(1,2))
        def _sync_unified(*_):
            try:
                pct = self._unified_progress_var.get()
                if pct > 0:
                    cur = self._play_progress_str.get()
                    # Only update to bare % if no descriptive text is already showing
                    if cur in ("Ready", "0%") or cur.endswith("%"):
                        self._play_progress_str.set(f"{pct:.0f}%")
                else:
                    if self._play_progress_str.get().endswith("%"):
                        self._play_progress_str.set("Ready")
            except Exception:
                pass
        self._unified_progress_var.trace_add("write", _sync_unified)

        SectionHeader(self._right, "EXPORT", "◆").pack(fill="x", pady=(0,0))
        ef = tk.Frame(self._right, bg=C["surface"]); ef.pack(fill="x", padx=12, pady=(4,4))
        er = tk.Frame(ef, bg=C["surface"]); er.pack(fill="x")
        self._wav_btn = WaveformExportBtn(er, fmt="WAV", subtitle="Save as WAV",
                                          command=self._export_wav,
                                          width=110, height=64)
        self._wav_btn.pack(side="left", fill="x", expand=True, padx=(0,4))
        self._mp3_btn = WaveformExportBtn(er, fmt="MP3", subtitle="Save as MP3",
                                          command=self._export_mp3,
                                          width=110, height=64)
        self._mp3_btn.pack(side="left", fill="x", expand=True)

        # _export_progress_var still exists and mirrors into _unified_progress_var via _finish_init.
        # The status label and output path entry are removed — status goes to _play_progress_str.
        self._export_progress_var = tk.DoubleVar(value=0.0)
        # Stub so existing configure() calls don't crash — they write to _play_progress_str instead.
        self._export_status_lbl  = None
        self._out_path_var        = tk.StringVar(value="")

        SectionHeader(self._right, "ENGINE STATUS", "◉").pack(fill="x", pady=(4,0))
        self._engine_frame = tk.Frame(self._right, bg=C["surface"])
        self._engine_frame.pack(fill="x", padx=8, pady=4)
        self._engine_labels = {}
        for eng in [_ENGINE_KOKORO, _ENGINE_ESPEAK]:
            row = tk.Frame(self._engine_frame, bg=C["surface"],
                           highlightthickness=1, highlightbackground=C["border"])
            row.pack(anchor="w", pady=1, fill="x", padx=2)
            lbl = tk.Label(row, text=f"· {eng}", font=("Courier New",7),
                           fg=C["muted"], bg=C["surface"],
                           anchor="w", wraplength=230, padx=4, pady=2)
            lbl.pack(side="left", fill="x")
            self._engine_labels[eng] = lbl

        ver_frame = tk.Frame(self._right, bg=C["surface"])
        ver_frame.pack(side="bottom", fill="x", pady=4)
        tk.Frame(ver_frame, bg=C["border2"], height=1).pack(fill="x", padx=8, pady=(0,4))
        tk.Label(ver_frame, text=f"TTS Voices  v{__version__}  ·  Offline",
                 font=("Courier New",7), fg=C["accent2"], bg=C["surface"]).pack(pady=(0,4))

        # ── Update check row ──────────────────────────────────────────────
        upd_row = tk.Frame(ver_frame, bg=C["surface"])
        upd_row.pack(fill="x", padx=8, pady=(0,4))
        tk.Label(upd_row, text="Auto-check updates",
                 font=("Courier New",7), fg=C["muted"], bg=C["surface"]).pack(side="left")
        self._update_toggle = PillToggle(upd_row,
            state=self.cfg.get("auto_update_check", True),
            callback=self._on_update_toggle)
        self._update_toggle.pack(side="right")


    # ── Placeholder ────────────────────────────────────────────────────────────
    # Placeholder text — stored so _get_text can detect stale flag
    _PLACEHOLDER = "Paste your text here... Or load a file from your collection."

    def _set_placeholder(self, scroll_top: bool = True):
        supported = getattr(file_extractor, "SUPPORTED_DISPLAY", "PDF DOCX DOC EPUB HTML RTF ODT TXT MD CSV") if file_extractor else "PDF DOCX DOC EPUB HTML RTF ODT TXT MD CSV"
        self._textarea.delete("1.0", "end")
        self._textarea.insert("1.0",
            f"{self._PLACEHOLDER}\n\n"
            f"Supported formats:  {supported}")
        self._textarea.configure(fg=C["muted"])
        self._textarea.mark_set("insert", "1.0")
        # Only scroll to top when explicitly loading placeholder (not on every FocusOut)
        if scroll_top:
            self._textarea.yview_moveto(0.0)
        self._placeholder_active = True

    def _remove_placeholder(self, _=None):
        """Clear placeholder on FocusIn, Button-1, or first KeyPress.
        Only deletes content if the textarea actually contains the placeholder text —
        never wipes real user content even if the flag is stale."""
        if self._placeholder_active:
            raw = self._textarea.get("1.0", "end").strip()
            if raw.startswith(self._PLACEHOLDER[:30]):
                self._textarea.delete("1.0", "end")
            self._textarea.configure(fg=C["textarea_fg"])
            self._placeholder_active = False

    def _maybe_placeholder(self, _=None):
        """Restore placeholder on FocusOut ONLY when the textarea is genuinely
        empty AND the placeholder flag is already set.  Never wipe real user text."""
        if self._placeholder_active:
            # Flag already set — re-render the placeholder but do NOT scroll to top
            # (user may have intentionally scrolled away; scrolling back is jarring)
            self._set_placeholder(scroll_top=False)
            return
        raw = self._textarea.get("1.0", "end").strip()
        if not raw:
            # Truly empty with no user content — safe to show placeholder
            self._set_placeholder(scroll_top=True)

    def _is_placeholder_content(self) -> bool:
        """True if textarea contains only the placeholder (regardless of flag)."""
        raw = self._textarea.get("1.0", "end").strip()
        return not raw or raw.startswith(self._PLACEHOLDER[:30])

    # ── Voice ──────────────────────────────────────────────────────────────────
    def _load_voices(self):
        if voices is None:
            self._voice_name_lbl.configure(text="Loading engines…")
            return
        self._all_voices = voices.get_all_voices()
        if not self._all_voices: self._all_voices=[("No engines found","","")]
        names=[v[0] for v in self._all_voices]
        self._voice_combo["values"]=names
        idx=min(self.cfg.get("voice_idx",0),len(names)-1)
        self._voice_combo.current(idx); self._update_voice_display()

    def _on_voice_change(self,_=None): self._update_voice_display(); self._save_cfg_now()

    # Voice cloning engines removed — only predefined voices supported
    _ZERO_SHOT_ENGINES: set = set()

    def _update_voice_display(self):
        idx=self._voice_combo.current()
        if 0<=idx<len(self._all_voices):
            display,engine,vname=self._all_voices[idx]
            short=display.split(" · ",1)[-1] if " · " in display else display
            self._voice_name_lbl.configure(text=short)
            # Correct license per engine
            ENGINE_LICENSES = {
                "Kokoro ONNX":  "Apache 2.0",
                "espeak-ng":    "GPL 3.0",
                "Chatterbox":   "MIT",
                "OmniVoice":    "Apache 2.0",
                "F5-TTS":       "MIT",
            }
            license_str = ENGINE_LICENSES.get(engine, "Open Source")
            self._voice_engine_lbl.configure(text=f"{engine}  ·  {license_str}")
            # Show reference-audio panel for zero-shot engines; hide for predefined ones
            is_zero_shot = any(e in engine for e in self._ZERO_SHOT_ENGINES)
            try:
                if is_zero_shot:
                    self._ref_frame.pack(fill="x", pady=(4, 0))
                else:
                    self._ref_frame.pack_forget()
            except Exception:
                pass
        self.cfg["voice_idx"]=idx

    def _browse_ref_audio(self):
        """Open audio file browser to pick a reference audio clip for voice cloning."""
        dlg = AudioFileDialog(self.root)
        if dlg.result:
            self._ref_audio_var.set(str(dlg.result))

    def _refresh_engine_status(self):
        if voices is None:
            return
        status=voices.get_engine_status()
        for eng,lbl in self._engine_labels.items():
            lbl.configure(text=f"✓ {eng}" if status.get(eng) else f"✗ {eng}",
                           fg=C["success"] if status.get(eng) else C["muted"])

    # ── Text ───────────────────────────────────────────────────────────────────
    def _get_text(self) -> str:
        """
        Get textarea text, sanitized and placeholder-aware.
        Uses content inspection — does NOT modify _placeholder_active as a side
        effect, to prevent the stale-flag trap where typing after the flag is
        accidentally set wipes real content via _remove_placeholder.
        """
        raw = self._textarea.get("1.0", "end")

        # Step 1: sanitize control chars (null bytes, etc.) before any check
        clean = "".join(ch for ch in raw if ch >= " " or ch in "\n\t")
        stripped = clean.strip()

        # Step 2: content-first placeholder detection — read-only check, no flag write
        if not stripped or stripped.startswith(self._PLACEHOLDER[:30]):
            return ""

        # Step 3: if real content is present but flag is stale, heal the color only
        if self._placeholder_active:
            self._placeholder_active = False
            self._textarea.configure(fg=C["textarea_fg"])

        return stripped

    def _update_wordcount(self, _=None):
        text = self._get_text()   # uses content-aware check
        if not text:
            self._word_lbl.configure(text="0 words · 0 chars")
            return
        words = len(text.split())
        chars = len(text)
        self._word_lbl.configure(text=f"{words:,} words · {chars:,} chars")

    def _clear_text(self):
        # If speaking, stop cleanly first so the worker thread finishes
        # before we clear the buffer — prevents the worker iterating a
        # cleared list and exiting without calling _on_speak_complete,
        # which would leave the SPEAK button stuck grey.
        if self._is_speaking:
            self._on_stop()
        self._set_placeholder(scroll_top=True); self._update_wordcount()
        self._wav_buffer.clear(); self.progress_var.set(0)

    def _load_file(self):
        if not _engines_ready.is_set():
            self._set_status("Still loading engines…", C["warning"])
            return
        dlg = TTSFileDialog(self.root)
        path = dlg.result
        if not path: return
        # Store path immediately on main thread so resume logic can use it
        self._current_file = path
        self._set_status("Loading…", C["warning"])
        def _do():
            try:
                text = file_extractor.extract_text(path)
                self.root.after(0, lambda: self._insert_file_text(text, path))
            except Exception as e:
                self.root.after(0, lambda err=e: self._dialog("Load Error", f"Could not read:\n{err}", "error"))
                self.root.after(0, lambda: self._set_status("READY", C["success"]))
        threading.Thread(target=_do, daemon=True).start()

    def _insert_file_text(self, text, path=""):
        if not text or not text.strip():
            self._set_status("READY", C["success"])
            self._dialog("Empty File",
                "The file was loaded but no text could be extracted.\n"
                "It may be empty, image-only, or the password may be incorrect.",
                "warn")
            return

        # Guard against binary garbage (e.g. failed decryption returning raw zip bytes)
        first = text.strip()[:4]
        if first.startswith("PK") or "\x00" in text[:100]:
            self._set_status("READY", C["success"])
            self._dialog("Extraction Error",
                "The file appears to contain binary data instead of text.\n\n"
                "If this is a password-protected file, the password may be wrong.\n"
                "Try loading it again and entering the correct password.",
                "error")
            return

        # Update _current_file with the path that was just loaded
        if path:
            self._current_file = path

        self._remove_placeholder()
        self._textarea.delete("1.0", "end")
        self._textarea.insert("1.0", text)
        # Keep cursor and view at top — prevents Tk auto-scroll to cursor on insert
        self._textarea.mark_set("insert", "1.0")
        self._textarea.yview_moveto(0.0)
        self._update_wordcount()
        self._set_status("READY", C["success"])
        bug_tracker.info(f"File inserted: {len(text):,} chars  path={self._current_file}")


    # ── Speech ─────────────────────────────────────────────────────────────────
    def _on_speak(self):
        if not _engines_ready.is_set():
            self._set_status("Still loading engines…", C["warning"])
            return
        if self._is_speaking: return
        if self._is_exporting:
            self._dialog("Export in Progress",
                "Please wait for the export to finish before speaking.", "warn")
            return
        text = self._get_text()
        if not text:
            self._dialog("No Text", "Please type or paste some text first,\nor use Load File to open a document.", "warn")
            return
        idx = self._voice_combo.current()
        if idx < 0: return
        _, engine, voice_name = self._all_voices[idx]
        speed = self.speed_var.get()
        pitch = self.pitch_var.get()
        # Reference audio for zero-shot cloning engines (Chatterbox, OmniVoice, etc.)
        ref_audio = getattr(self, "_ref_audio_var", None)
        ref_audio_path = ref_audio.get().strip() if ref_audio else ""

        self._is_speaking = True
        self._fire_plugin_cbs('_plugin_speak_start_cbs')
        self._stop_flag.clear()
        self._wav_buffer.clear()
        self._fallback_warned = False
        self.progress_var.set(0)
        self._speak_btn.set_colors("#333344", "#333344")
        self._cancel_highlights()
        self._clear_highlight()
        chunks = voices.chunk_text(text)
        total  = len(chunks)

        # Build char-position index for all chunks (needed for word highlighting)
        all_positions = []
        search_start  = 0
        for chunk in chunks:
            first_words = " ".join(chunk.split()[:6])
            pos = text.find(first_words, search_start)
            if pos == -1:
                pos = search_start
            all_positions.append(pos)
            search_start = pos + 1
        chunk_positions = all_positions

        self._set_status("SYNTHESIZING...", C["warning"])

        def _worker():
            """
            Phase 1 (0→100%): Synthesize chunks one-by-one, reading speed/pitch
                               fresh each time so slider changes take effect immediately.
            Phase 2 (100%):   Play chunks sequentially with word highlighting.
            """
            # ── Phase 1: Sequential Synthesis (live controls) ─────────────
            try:
                for i, chunk in enumerate(chunks):
                    if self._stop_flag.is_set():
                        # Save the chunk we stopped at so resume is exact
                        self.root.after(0, self._on_speak_complete)
                        return
                    # Track synthesis position so speed-change restart can resume
                    # from the right chunk even if no audio has played yet (Phase 1)
                    try:
                        # Read controls fresh per-chunk — slider changes apply immediately
                        cur_speed = self.speed_var.get()
                        cur_pitch = self.pitch_var.get()
                        wav = voices.synthesize(chunk, engine, voice_name,
                                                cur_speed, cur_pitch)
                        self._wav_buffer.append(wav)
                        if voices.fallback_occurred:
                            if not getattr(self, "_fallback_warned", False):
                                self._fallback_warned = True
                                # Detect if the fallback was likely due to non-English text
                                reason = "non_english" if not voices._is_mostly_ascii(chunk) else "phoneme_limit"
                                self.root.after(500, lambda r=reason: self._show_fallback_warning(r))
                    except Exception as e:
                        bug_tracker.error(f"Chunk {i} synthesis failed: {e}")
                        self.root.after(0, lambda err=e:
                            self._dialog("TTS Error", f"Synthesis failed:\n{err}", "error"))
                        self.root.after(0, self._on_speak_complete)
                        return
                    pct = (i + 1) / total * 100
                    now = time.monotonic()
                    if now - self._last_progress_update > 0.08:
                        self._last_progress_update = now
                        self.root.after(0, lambda p=pct: self.progress_var.set(p))
            except Exception as e:
                bug_tracker.error(f"Synthesis phase failed: {e}")
                self.root.after(0, self._on_speak_complete)
                return

            # ── Phase 2: Play with highlighting ───────────────────────────
            if not self._wav_buffer:
                self.root.after(0, self._on_speak_complete)
                return

            self.root.after(0, lambda: self._set_status("PLAYING  ▶", C["success"]))
            self.root.after(0, self._clear_highlight)
            self.root.after(0, self._setup_highlight_tag)

            self._chunk_highlight_data   = {}

            # Filter out any None entries left by stream-and-discard from a
            # previous session — they crash play_wav with TypeError: NoneType
            # Take a LOCAL snapshot of the buffer before the play loop.
            # This eliminates ALL IndexError / NoneType races:
            # - export thread clearing _wav_buffer mid-iteration → no effect
            # - _wav_buffer[i] = None index-out-of-range → never happens
            # chunk_index maps snapshot position → original chunk index for highlighting
            local_play = [(orig_i, wav)
                          for orig_i, wav in enumerate(self._wav_buffer)
                          if wav is not None]
            # Free the shared buffer now — we have our local copy
            self._wav_buffer.clear()

            for seq_i, (orig_i, wav) in enumerate(local_play):
                if self._stop_flag.is_set():
                    # Stop was pressed before this chunk started — resume from here
                    break

                self._current_chunk_index = orig_i   # on_start callback reads this

                # Record which chunk is playing so stop MID-chunk saves correctly
                self._playing_chunk_abs = orig_i

                # Calculate audio duration from WAV header
                try:
                    import wave as _wave, io as _io
                    with _wave.open(_io.BytesIO(wav)) as _wf:
                        wav_dur = _wf.getnframes() / _wf.getframerate()
                except Exception:
                    wav_dur = len(chunks[orig_i].split()) * 0.12

                if self._highlight_enabled and orig_i < len(chunks) and orig_i < len(chunk_positions):
                    self._prepare_highlight_data(
                        chunks[orig_i], chunk_positions[orig_i], wav_dur, text)

                played = audio_handler.play_wav(wav)
                if self._stop_flag.is_set():
                    # Stop fired DURING play_wav — record this chunk as the
                    # interrupted one so resume replays from its beginning.
                    break
                if not played:
                    bug_tracker.warning(f"play_wav returned False for chunk {orig_i} — skipping")
                # wav local var goes out of scope each iteration → GC frees it

            # Clear highlight when done
            self.root.after(0, self._clear_highlight)
            self.root.after(0, self._on_speak_complete)

        def _worker_safe():
            """Wrap _worker so any unhandled crash still resets UI state."""
            try:
                _worker()
            except Exception as e:
                bug_tracker.error(f"Worker thread crashed: {e}")
                self.root.after(0, self._on_speak_complete)
                self.root.after(0, self._clear_highlight)

        threading.Thread(target=_worker_safe, daemon=True).start()

    def _on_stop(self):
        self._cancel_highlights()
        self._stop_flag.set()
        audio_handler.stop_playback()
        self._on_speak_complete()

    def _on_speak_complete(self):
        self._is_speaking = False
        self._set_status("READY", C["success"])
        self._speak_btn.set_colors(C["speak_bg"], C["speak_hover"])
        self.progress_var.set(0)
        if not self._stop_flag.is_set():
            self._generation = getattr(self, "_generation", 0) + 1
        self._clear_highlight()

    # ── Text Highlighting ──────────────────────────────────────────────────
    def _setup_highlight_tag(self):
        """Configure the highlight tag: vivid blue with solid background for body."""
        try:
            self._textarea.tag_configure(
                "speaking",
                foreground="#00d4ff",        # neon cyan — matches VOICE/CONTROLS headers
                background="#003344",        # dark navy base
                font=("Courier New", 11, "bold"),
            )
        except Exception:
            pass

    def _highlight_word(self, offset):
        """Highlight the single word at offset in the textarea.

        offset may be:
          - a (char_start, char_end) tuple  — from updated _prepare_highlight_data
          - a plain int char_offset         — legacy callers / compatibility
        """
        try:
            self._textarea.tag_remove("speaking", "1.0", "end")
            if isinstance(offset, tuple):
                char_start, char_end = offset
                start_idx = f"1.0+{char_start}c"
                end_idx   = f"1.0+{char_end}c"
            else:
                # Legacy: use wordend but skip any leading whitespace first
                char_start = offset
                start_idx  = f"1.0+{char_start}c"
                # Advance past whitespace so we tag the word, not the space before it
                char_at = self._textarea.get(start_idx)
                while char_at in (" ", "\t", "\n", "\r") and char_start < char_start + 50:
                    char_start += 1
                    start_idx = f"1.0+{char_start}c"
                    char_at   = self._textarea.get(start_idx)
                end_idx = f"{start_idx} wordend"
            self._textarea.tag_add("speaking", start_idx, end_idx)
            # Auto-scroll: only when enabled, actively speaking, AND user hasn't
            # manually scrolled recently. Never auto-scroll when not speaking.
            import time as _t
            elapsed_ms = (_t.monotonic() - self._user_scrolled_at) * 1000
            if (self._auto_scroll_enabled and self._is_speaking
                    and elapsed_ms > self._USER_SCROLL_LOCK_MS):
                self._textarea.see(start_idx)
        except Exception:
            pass

    def _highlight_range(self, char_start: int, char_end: int):
        """Legacy: highlight a range. Now unused — kept for compatibility."""
        self._highlight_word(char_start)

    def _estimate_word_durations(self, words: list, total_s: float) -> list:
        """
        Estimate per-word duration with syllable weighting AND punctuation pauses.

        Kokoro TTS adds natural pauses after punctuation. Without accounting for
        these, the highlight timing drifts forward over long texts because all the
        "extra" time from pauses gets distributed across words evenly — making
        each word appear slightly shorter than it really is, so the highlight
        runs ahead of speech.

        Pause weights (relative units added to the word before the pause):
          sentence end (. ! ?)  → +1.5  units  (~300-500ms in natural speech)
          clause break (, ; :)  → +0.6  units  (~100-200ms)
          dash / ellipsis       → +0.4  units
        """
        import re

        def syllables(word):
            w = re.sub(r"[^a-zA-Z]", "", word.lower())
            if not w:
                return 1
            # Count vowel groups
            count = len(re.findall(r"[aeiouy]+", w))
            # Silent trailing 'e' rule
            if w.endswith("e") and count > 1:
                count -= 1
            # 'le' at end counts as a syllable if preceded by consonant
            if w.endswith("le") and len(w) > 2 and w[-3] not in "aeiouy":
                count = max(count, 1)
            # 'es'/'ed' at end usually silent
            if w.endswith(("es", "ed")) and count > 1:
                count -= 1
            return max(1, count)

        def pause_weight(word):
            """Extra time units for the pause AFTER this word."""
            stripped = word.rstrip()
            if not stripped:
                return 0.0
            last = stripped[-1]
            if last in ".!?":   return 1.5
            if last in ",;:":   return 0.6
            if last in "-—…":   return 0.4
            return 0.0

        # Build weights: syllable count + punctuation pause
        weights = []
        for w in words:
            syl_w = syllables(w)
            pau_w = pause_weight(w)
            weights.append(syl_w + pau_w)

        tot = sum(weights)
        if tot == 0:
            # Fallback: equal distribution
            return [total_s / len(words)] * len(words)

        return [(w / tot) * total_s for w in weights]

    def _setup_audio_callbacks(self):
        """
        Wire audio_handler callbacks for highlight loop timing.

        _on_start fires from the worker thread right after Popen().
        We schedule the highlight loop via root.after(0,...) to switch
        to the main thread. The _LATENCY_COMP in the loop accounts for
        the time between Popen and audio actually reaching the speakers.
        """
        def _on_start():
            # Called from worker thread — must use root.after to touch Tkinter.
            # Capture chunk index immediately (before worker advances it).
            chunk_idx = self._current_chunk_index
            # after(0,...) puts this on the main thread event queue immediately.
            # get_playback_position() uses wall-clock time from Popen, so the
            # loop catches up to real elapsed time on its first iteration.
            self.root.after(0, lambda: self._run_realtime_highlight_loop(chunk_idx))

        def _on_stop():
            # Clear highlight when audio subprocess exits
            self.root.after(0, self._clear_highlight)

        audio_handler.set_callbacks(on_start=_on_start, on_stop=_on_stop)

    def _prepare_highlight_data(self, chunk_text: str, chunk_char_start: int,
                                wav_duration_s: float, full_text: str):
        """
        Prepare word timing data for a chunk. Called synchronously from the
        worker thread BEFORE play_wav() so data is ready when on_start fires.

        Takes full_text as a parameter (the original text variable from _on_speak)
        so this can safely run on the worker thread without touching Tk widgets.
        The highlight loop is started by _setup_audio_callbacks on_start callback.
        """
        raw_words = chunk_text.split()
        if not raw_words or wav_duration_s <= 0:
            return

        # Strip tokens that are pure punctuation / symbols with no alphanumeric
        # content (e.g. "*", "—", "•"). Kokoro produces no audio for these so
        # giving them a time slot shifts all subsequent word highlights early.
        import re as _re
        voiced_pairs = []   # (original_word, voiced_bool)
        words = []
        for w in raw_words:
            is_voiced = bool(_re.search(r'[\w\d]', w))
            voiced_pairs.append((w, is_voiced))
            if is_voiced:
                words.append(w)

        if not words:
            return

        # Map each word to its absolute char offset using the cached full_text.
        # Store BOTH start and end so _highlight_word uses explicit boundaries
        # instead of Tkinter's 'wordend' (which can include trailing whitespace).
        # We walk raw_words so all tokens (voiced or not) advance pos correctly.
        word_offsets = []    # (char_start, char_end) per VOICED word only
        pos = chunk_char_start
        for orig_word, is_voiced in voiced_pairs:
            found = full_text.find(orig_word, pos)
            if found != -1:
                if is_voiced:
                    word_offsets.append((found, found + len(orig_word)))
                pos = found + len(orig_word)
            else:
                if is_voiced:
                    word_offsets.append((pos, pos + len(orig_word)))

        # Build cumulative timestamp list proportional to syllable count
        durations = self._estimate_word_durations(words, wav_duration_s)
        cumulative_times = []
        elapsed = 0.0
        for dur in durations:
            cumulative_times.append(elapsed)
            elapsed += dur

        # Store data keyed by chunk index — loop reads this when it starts
        chunk_idx = self._current_chunk_index
        self._chunk_highlight_data[chunk_idx] = {
            "offsets":  word_offsets,
            "times":    cumulative_times,
            "duration": wav_duration_s,
        }

    def _run_realtime_highlight_loop(self, chunk_index: int):
        """
        Real-time highlight loop. Runs on main thread via root.after.
        Only one loop runs at a time — tracked by self._highlight_after_id.
        """
        # Cancel any previously scheduled loop
        if hasattr(self, "_highlight_after_id") and self._highlight_after_id:
            try: self.root.after_cancel(self._highlight_after_id)
            except Exception: pass
            self._highlight_after_id = None

        _LATENCY_COMP = self.cfg.get("highlight_offset", 150) / 1000.0

        data = self._chunk_highlight_data.get(chunk_index)
        if not data:
            return

        offsets  = data["offsets"]
        times    = data["times"]
        duration = data["duration"]
        if not offsets or not times:
            return

        import bisect
        last_word_idx = [-1]
        # Generation counter — if a new loop starts, old one stops
        gen = id(data)
        self._highlight_gen = gen

        def _update():
            if not self._highlight_enabled:
                return
            if self._stop_flag.is_set():
                self._highlight_active = False
                return
            if getattr(self, "_highlight_gen", None) != gen:
                return   # a newer loop superseded this one

            raw_elapsed = audio_handler.get_playback_position()
            elapsed = max(0.0, raw_elapsed - _LATENCY_COMP)

            word_idx = max(0, bisect.bisect_right(times, elapsed) - 1)
            if word_idx != last_word_idx[0] and word_idx < len(offsets):
                last_word_idx[0] = word_idx
                self._highlight_word(offsets[word_idx])

            # Keep loop running 300ms past audio end to ensure last word is highlighted,
            # then schedule final clear. This prevents last word being skipped.
            TAIL_MS = 0.30
            if not self._stop_flag.is_set() and raw_elapsed < duration + TAIL_MS:
                self._highlight_after_id = self.root.after(20, _update)
            else:
                # Highlight the final word explicitly, then clear after 400ms
                if not self._stop_flag.is_set() and offsets:
                    self._highlight_word(offsets[-1])
                    # Schedule clear so the last word is briefly visible
                    self.root.after(400, self._clear_highlight)
                else:
                    self._clear_highlight()
                self._highlight_active = False
                self._highlight_after_id = None

        self._highlight_active = True
        self._highlight_after_id = self.root.after(0, _update)

    def _on_hl_toggle(self, new_state: bool):
        """Called by PillToggle when Highlight Sync is clicked.
        new_state is True=ON, False=OFF.

        Re-enable logic
        ───────────────
        When re-enabling mid-speech we need to restart the highlight loop for
        the chunk that is currently playing.  Three conditions must all be true:
          1. TTS is actively speaking (_is_speaking)
          2. The current chunk has timing data in _chunk_highlight_data
          3. The chunk's audio hasn't finished yet (elapsed < duration)

        If condition 3 fails (we re-enable after the chunk's audio ended but
        before the worker has incremented _current_chunk_index) we skip — the
        loop will start naturally on the NEXT chunk via _on_start callback.
        Without this guard the loop starts, immediately sees elapsed > duration,
        sets _highlight_active = False, and exits — which is the "doesn't show
        until a certain point" symptom.
        """
        self._highlight_enabled = new_state
        if new_state:
            self._setup_highlight_tag()
            if self._is_speaking:
                chunk_idx = self._current_chunk_index
                data = self._chunk_highlight_data.get(chunk_idx)
                if data:
                    # Only restart if the chunk's audio is still in progress
                    elapsed  = audio_handler.get_playback_position()
                    duration = data.get("duration", 0.0)
                    if elapsed < duration:
                        self._run_realtime_highlight_loop(chunk_idx)
                    # else: next chunk's _on_start will kick off a fresh loop
        else:
            self._cancel_highlights()
            self._clear_highlight()

    def _on_as_toggle(self, new_state: bool):
        """Called by PillToggle when Auto-Scroll is clicked.
        new_state is True=ON, False=OFF."""
        self._auto_scroll_enabled = new_state

    def _on_update_toggle(self, state: bool):
        """Auto-check for updates toggle in bottom of right panel."""
        self.cfg["auto_update_check"] = state
        save_config(self.cfg)
        if state and not self._update_available_version:
            self._check_for_update_bg(manual=False)

    # Keep _toggle_highlight and _toggle_auto_scroll as aliases so any
    # keyboard shortcut or other internal caller still works.
    def _toggle_highlight(self):
        self._hl_toggle.toggle()

    def _toggle_auto_scroll(self):
        self._as_toggle.toggle()

    def _cancel_highlights(self):
        """Cancel real-time highlight loop immediately."""
        self._highlight_active = False
        self._highlight_gen    = None
        if hasattr(self, "_highlight_after_id") and self._highlight_after_id:
            try: self.root.after_cancel(self._highlight_after_id)
            except Exception: pass
            self._highlight_after_id = None
        self._chunk_highlight_data = {}

    
    def _clear_highlight(self):
        """Remove all speech highlighting."""
        try:
            self._textarea.tag_remove("speaking", "1.0", "end")
        except Exception:
            pass

    def _show_fallback_warning(self, reason: str = "phoneme_limit"):
        """Non-blocking toast: Kokoro fell back to espeak on a chunk."""
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.configure(bg=C["surface"],
                            highlightthickness=2,
                            highlightbackground=C["warning"])
            toast.attributes("-topmost", True)

            tk.Label(toast,
                     text="⚠  Voice quality reduced",
                     font=("Courier New", 9, "bold"),
                     fg=C["warning"], bg=C["surface"],
                     padx=14, pady=6).pack()

            if reason == "non_english":
                body = ("Kokoro English voices only support English text.\n"
                        "Non-English characters were detected.\n"
                        "→ espeak-ng fallback was used for that chunk.")
            else:
                body = ("Kokoro hit the phoneme limit on a passage.\n"
                        "espeak-ng fallback was used for that chunk.\n"
                        "→ Try: switch to CPU · lower speed · shorter sentences")

            tk.Label(toast, text=body,
                     font=("Courier New", 8),
                     fg=C["text2"], bg=C["surface"],
                     padx=14, pady=(0, 10)).pack()

            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            toast.update_idletasks()
            tw = toast.winfo_reqwidth()
            th = toast.winfo_reqheight()
            toast.geometry(f"+{sw - tw - 20}+{sh - th - 60}")

            toast.after(7000, lambda: toast.destroy()
                        if toast.winfo_exists() else None)
        except Exception:
            pass

    def _dialog(self, title: str, message: str, kind: str = "info"):
        """Show a themed dialog matching the app style. kind: info | error | warn"""
        icons  = {"info": "ℹ", "error": "✗", "warn": "⚠"}
        colors = {"info": C["accent2"], "error": C["error"], "warn": C["warning"]}
        icon   = icons.get(kind, "ℹ")
        color  = colors.get(kind, C["accent2"])

        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.transient(self.root)

        # Header
        hdr = tk.Frame(win, bg=C["header_bg"])
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"{icon}  {title}",
                 font=("Courier New", 11, "bold"),
                 fg=color, bg=C["header_bg"],
                 padx=20, pady=10).pack(side="left")
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        # Message
        body = tk.Frame(win, bg=C["bg"], padx=28, pady=20)
        body.pack(fill="x")
        tk.Label(body, text=message,
                 font=("Courier New", 10),
                 fg=C["text"], bg=C["bg"],
                 wraplength=380, justify="left").pack(anchor="w")

        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        # Button
        foot = tk.Frame(win, bg=C["surface"], pady=10)
        foot.pack(fill="x")
        tk.Button(foot, text="  OK  ",
                  font=("Courier New", 9, "bold"),
                  bg=C["accent"], fg="white",
                  relief="flat", padx=16, pady=6,
                  cursor="hand2",
                  activebackground=C["speak_hover"],
                  activeforeground="white",
                  command=win.destroy).pack(side="right", padx=16)

        win.update_idletasks()
        w = win.winfo_reqwidth()
        h = win.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        win.grab_set()
        self.root.wait_window(win)

    # ── Export ─────────────────────────────────────────────────────────────────
    def _ensure_wav_buffer(self) -> bool:
        """Return True if wav_buffer has valid (non-None) WAV data.
        Synthesizes in parallel if buffer is empty/stale.
        """
        valid = [c for c in self._wav_buffer if c is not None]
        if valid:
            return True

        text = self._get_text()
        if not text:
            self._dialog("Nothing to Export",
                         "Please type or load a document first.", "warn")
            return False
        idx = self._voice_combo.current()
        if idx < 0:
            return False
        _, engine, voice_name = self._all_voices[idx]
        speed = self.speed_var.get()
        pitch = self.pitch_var.get()
        chunks = voices.chunk_text(text)
        total  = max(len(chunks), 1)
        self._wav_buffer.clear()
        self._set_status("SYNTHESIZING…", C["warning"])

        def _pb(done, tot):
            pct = done / tot * 80
            self.root.after(0, lambda p=pct: self._export_progress_var.set(p))
            self.root.after(0, lambda p=pct: self._set_export_status(
                text=f"Synthesizing… {int(p)}%", fg=C["warning"]))

        wav_results = voices.synthesize_batch(
            chunks, engine, voice_name, speed, pitch,
            stop_flag=self._stop_flag, progress_cb=_pb)

        for wav in wav_results:
            self._wav_buffer.append(wav)

        self._set_status("READY", C["success"])
        return bool([c for c in self._wav_buffer if c is not None])

    def _export_wav(self):
        """Export to WAV. All Tk calls happen on main thread — only I/O in worker."""
        if self._is_speaking:
            self._dialog("Export Blocked",
                "Please press STOP before speaking ends, then export.", "warn")
            return
        if self._is_exporting:
            self._dialog("Already Exporting",
                "An export is already in progress. Please wait.", "warn")
            return
        # ── Pre-validate on main thread before spawning worker ────────────────
        text = self._get_text()
        if not text:
            self._dialog("Nothing to Export",
                "Please type or load a document first.", "warn")
            return
        idx = self._voice_combo.current()
        if idx < 0 or idx >= len(self._all_voices):
            return
        _, engine, voice_name = self._all_voices[idx]
        speed = self.speed_var.get()
        pitch = self.pitch_var.get()

        dlg = TTSSaveDialog(self.root, title="Export as WAV",
                            default_ext=".wav",
                            filetypes=[("WAV Audio", ".wav")],
                            default_name="tts_output")
        path = dlg.result
        if not path: return

        # Show chosen path in Output Path field
        try: self._out_path_var.set(str(path))
        except Exception: pass

        # Reset export bar immediately on main thread
        self._export_progress_var.set(0)
        self._set_export_status("Preparing export…", C["warning"])
        self._is_exporting = True   # prevent concurrent speak

        def _do():
            # ── Always re-synthesize the FULL text for export ─────────────────
            # Use a LOCAL buffer so the export never races with the speak thread's
            # _wav_buffer. Shared buffer access was the root cause of jumbled audio.
            self._stop_flag.clear()
            self._wav_buffer.clear()
            chunks = voices.chunk_text(text)
            total  = max(len(chunks), 1)
            self.root.after(0, lambda: self._set_status("SYNTHESIZING FOR EXPORT...", C["warning"]))

            def _export_progress(done, tot):
                pct = done / tot * 75
                self.root.after(0, lambda p=pct: self._export_progress_var.set(p))
                self.root.after(0, lambda p=pct, d=done, t=tot:
                    self._set_export_status(
                        text=f"Synthesizing {int(p)}% ({d}/{t} chunks)...",
                        fg=C["warning"]))

            wav_results = voices.synthesize_batch(
                chunks, engine, voice_name, speed, pitch,
                stop_flag=self._stop_flag,
                progress_cb=_export_progress)

            if self._stop_flag.is_set():
                self.root.after(0, lambda: self._set_status("READY", C["success"]))
                self.root.after(0, lambda: self._export_progress_var.set(0))
                self.root.after(0, lambda: self._set_export_status("Ready", C["muted"]))
                return

            # Use local list — never touch _wav_buffer so speak thread can't interfere
            valid = [c for c in wav_results if c is not None]

            if not valid:
                self.root.after(0, lambda: self._dialog("Export Failed",
                    "No audio was synthesized. Check the voice engine is working.", "warn"))
                return

            # ── Write the file ────────────────────────────────────────────────
            self.root.after(0, lambda: self._set_status("EXPORTING WAV...", C["warning"]))
            self.root.after(0, lambda: self._set_export_status(
                text="Writing WAV...", fg=C["warning"]))
            self.root.after(0, lambda: self._export_progress_var.set(75))
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            except Exception:
                pass
            # Wire real chunk-by-chunk write progress into the export bar (75→99%)
            total_chunks = max(len(valid), 1)
            def _write_progress(chunk_idx):
                pct = 75 + ((chunk_idx + 1) / total_chunks) * 24
                self.root.after(0, lambda p=pct, i=chunk_idx:
                    (self._export_progress_var.set(p),
                     self._set_export_status(
                         text=f"Writing WAV… chunk {i+1}/{total_chunks}",
                         fg=C["warning"])))
            ok = audio_handler.export_wav(valid, path, progress_cb=_write_progress)
            self.root.after(0, lambda: self._export_progress_var.set(100))

            # ── Verify ────────────────────────────────────────────────────────
            verified   = False
            file_size  = 0
            duration_s = 0.0
            if ok:
                try:
                    import wave as _wave
                    file_size = os.path.getsize(path)
                    with _wave.open(path, "rb") as wf:
                        frames     = wf.getnframes()
                        rate       = wf.getframerate()
                        duration_s = frames / rate if rate else 0
                    verified = frames > 0 and file_size > 44
                except Exception as e:
                    bug_tracker.warning(f"WAV verify: {e}")

            self.root.after(0, lambda: self._set_status("READY", C["success"]))
            if ok and verified:
                sz  = file_size // 1024
                dur = round(duration_s, 1)
                self.root.after(0, lambda: self._set_export_status(
                    text=f"✓ {dur}s · {sz}KB saved", fg=C["success"]))
                self.root.after(0, lambda: self._dialog("Export WAV",
                    f"✓ Saved successfully\n\n"
                    f"File:     {path}\n"
                    f"Size:     {sz} KB\n"
                    f"Duration: {dur}s", "info"))
            elif ok and not verified:
                self.root.after(0, lambda: self._set_export_status(
                    text="⚠ Saved but appears empty", fg=C["warning"]))
                self.root.after(0, lambda: self._dialog("Export WAV — Warning",
                    f"File saved but appears empty:\n{path}\n\n"
                    "Try pressing Speak first, then export.", "warn"))
            else:
                self.root.after(0, lambda: self._export_progress_var.set(0))
                self.root.after(0, lambda: self._set_export_status(
                    text="✗ Export failed", fg=C["error"]))
                self.root.after(0, lambda: self._dialog("Export Failed",
                    f"Could not save WAV to:\n{path}\n\n"
                    "Check the folder is writable.", "error"))
            self.root.after(5000, lambda: self._export_progress_var.set(0))
            self.root.after(5000, lambda: self._set_export_status("Ready", C["muted"]))

        def _run_export():
            try:
                _do()
            finally:
                self._is_exporting = False
        threading.Thread(target=_run_export, daemon=True).start()

    def _export_mp3(self):
        """Export to MP3. All Tk calls on main thread — only synthesis/I/O in worker."""
        if self._is_speaking:
            self._dialog("Export Blocked",
                "Please press STOP before speaking ends, then export.", "warn")
            return
        if self._is_exporting:
            self._dialog("Already Exporting",
                "An export is already in progress. Please wait.", "warn")
            return
        # Pre-check ffmpeg on main thread before doing anything else
        import subprocess as _sp
        if _sp.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
            self._dialog("ffmpeg Not Found",
                "MP3 export requires ffmpeg.\n\n"
                "Install it:\n  sudo apt install ffmpeg\n\n"
                "You can still export as WAV without ffmpeg.",
                "warn")
            return
        # Pre-validate on main thread
        text = self._get_text()
        if not text:
            self._dialog("Nothing to Export",
                "Please type or load a document first.", "warn")
            return
        idx = self._voice_combo.current()
        if idx < 0 or idx >= len(self._all_voices):
            return
        _, engine, voice_name = self._all_voices[idx]
        speed = self.speed_var.get()
        pitch = self.pitch_var.get()

        dlg = TTSSaveDialog(self.root, title="Export as MP3",
                            default_ext=".mp3",
                            filetypes=[("MP3 Audio", ".mp3"), ("WAV Audio", ".wav")],
                            default_name="tts_output")
        path = dlg.result
        if not path: return

        # Show chosen path in Output Path field
        try: self._out_path_var.set(str(path))
        except Exception: pass

        # Reset export bar on main thread immediately
        self._export_progress_var.set(0)
        self._set_export_status("Preparing MP3 export…", C["warning"])
        self._is_exporting = True   # prevent concurrent speak

        def _do():
            try:
                _export_work()
            finally:
                self._is_exporting = False
                self.root.after(0, lambda: self._set_status("READY", C["success"]))

        def _export_work():
            self._stop_flag.clear()
            self._wav_buffer.clear()
            chunks = voices.chunk_text(text)
            total  = max(len(chunks), 1)
            self.root.after(0, lambda: self._set_status("SYNTHESIZING FOR EXPORT...", C["warning"]))

            def _mp3_progress(done, tot):
                pct = done / tot * 70
                self.root.after(0, lambda p=pct: self._export_progress_var.set(p))
                self.root.after(0, lambda p=pct, d=done, t=tot:
                    self._set_export_status(
                        text=f"Synthesizing {int(p)}% ({d}/{t} chunks)...",
                        fg=C["warning"]))

            wav_results = voices.synthesize_batch(
                chunks, engine, voice_name, speed, pitch,
                stop_flag=self._stop_flag,
                progress_cb=_mp3_progress)

            if self._stop_flag.is_set():
                self.root.after(0, lambda: self._set_status("READY", C["success"]))
                self.root.after(0, lambda: self._export_progress_var.set(0))
                self.root.after(0, lambda: self._set_export_status("Ready", C["muted"]))
                return

            for wav in wav_results:
                self._wav_buffer.append(wav)

            valid = [c for c in self._wav_buffer if c is not None]

            if not valid:
                self.root.after(0, lambda: self._dialog("Export Failed",
                    "No audio was synthesized. Check the voice engine is working.", "warn"))
                return

            # ── Encode MP3 ────────────────────────────────────────────────────
            self.root.after(0, lambda: self._set_status("ENCODING MP3...", C["warning"]))
            self.root.after(0, lambda: self._set_export_status(
                text="Writing WAV stage…", fg=C["warning"]))
            self.root.after(0, lambda: self._export_progress_var.set(75))
            try:
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            except Exception:
                pass
            # Wire chunk-by-chunk write progress (75→90% WAV stage, 90→100% ffmpeg)
            total_chunks = max(len(valid), 1)
            def _mp3_write_progress(chunk_idx):
                pct = 75 + ((chunk_idx + 1) / total_chunks) * 15
                self.root.after(0, lambda p=pct, i=chunk_idx:
                    (self._export_progress_var.set(p),
                     self._set_export_status(
                         text=f"Writing chunk {i+1}/{total_chunks}…",
                         fg=C["warning"])))
            self.root.after(0, lambda: self._set_export_status(
                text="Encoding MP3 with ffmpeg…", fg=C["warning"]))
            ok = audio_handler.export_mp3(valid, path, progress_cb=_mp3_write_progress)
            self.root.after(0, lambda: self._export_progress_var.set(100))

            # ── Verify ────────────────────────────────────────────────────────
            verified  = False
            file_size = 0
            if ok:
                try:
                    file_size = os.path.getsize(path)
                    verified  = file_size > 1024
                except Exception:
                    pass

            self.root.after(0, lambda: self._set_status("READY", C["success"]))
            if ok and verified:
                sz = file_size // 1024
                self.root.after(0, lambda: self._set_export_status(
                    text=f"✓ {sz}KB MP3 saved", fg=C["success"]))
                self.root.after(0, lambda: self._dialog("Export MP3",
                    f"✓ Saved successfully\n\n"
                    f"File: {path}\n"
                    f"Size: {sz} KB", "info"))
            elif ok and not verified:
                self.root.after(0, lambda: self._set_export_status(
                    text="⚠ Saved but appears empty", fg=C["warning"]))
                self.root.after(0, lambda: self._dialog("Export Warning",
                    f"File saved but appears empty:\n{path}\n\n"
                    "Check ffmpeg is working: ffmpeg -version", "warn"))
            else:
                self.root.after(0, lambda: self._export_progress_var.set(0))
                self.root.after(0, lambda: self._set_export_status(
                    text="✗ Export failed", fg=C["error"]))
                self.root.after(0, lambda: self._dialog("Export Failed",
                    f"Could not save MP3 to:\n{path}\n\n"
                    "Check ffmpeg: sudo apt install ffmpeg", "error"))
            self.root.after(5000, lambda: self._export_progress_var.set(0))
            self.root.after(5000, lambda: self._set_export_status("Ready", C["muted"]))

        def _run_export():
            try:
                _do()
            finally:
                self._is_exporting = False
        threading.Thread(target=_run_export, daemon=True).start()

    # ── GPU ────────────────────────────────────────────────────────────────────
    def _get_best_provider(self):
        """Return (label, onnxruntime_provider) for the best available GPU."""
        avail = voices.get_available_providers()
        if "CUDAExecutionProvider"      in avail: return ("CUDA (NVIDIA)",  "CUDAExecutionProvider")
        if "ROCMExecutionProvider"      in avail: return ("ROCm (AMD)",     "ROCMExecutionProvider")
        if "TensorrtExecutionProvider"  in avail: return ("TensorRT",       "TensorrtExecutionProvider")
        if "OpenVINOExecutionProvider"  in avail: return ("Intel GPU (OpenVINO)", "OpenVINOExecutionProvider")
        if "DmlExecutionProvider"       in avail: return ("DirectML",       "DmlExecutionProvider")
        return ("CPU", "CPUExecutionProvider")

    def _update_gpu_btn(self):
        prov = voices.get_current_provider()
        if prov == "CPUExecutionProvider":
            label, color, hover = "⚡ CPU", C["accent"], C["accent_dim"]
        elif "CUDA"     in prov: label, color, hover = "⚡ GPU ✓", "#1a7a1a", "#22a022"
        elif "ROCM"     in prov: label, color, hover = "⚡ GPU ✓", "#1a7a1a", "#22a022"
        elif "Tensorrt" in prov: label, color, hover = "⚡ GPU ✓", "#1a7a1a", "#22a022"
        elif "OpenVINO" in prov: label, color, hover = "⚡ GPU ✓", "#1a7a1a", "#22a022"
        else:                    label, color, hover = "⚡ GPU ✓", "#1a7a1a", "#22a022"
        self._gpu_btn.set_text(label)
        self._gpu_btn.set_colors(color, hover)
        self._gpu_btn._lbl.configure(fg="white")

    def _toggle_gpu(self):
        """Cycle CPU ↔ best available GPU. Stops synthesis first."""
        if self._is_speaking:
            self._stop_flag.set()
            audio_handler.stop_playback()
            import time; time.sleep(0.15)
            self._is_speaking = False
            self._wav_buffer.clear()
            self._set_status("READY", C["success"])
            self._speak_btn.set_colors(C["speak_bg"], C["speak_hover"])
            self.progress_var.set(0)

        prov = voices.get_current_provider()
        best_label, best_prov = self._get_best_provider()

        if prov == "CPUExecutionProvider":
            if best_prov != "CPUExecutionProvider":
                voices.set_provider(best_prov)
                self.cfg["provider"] = best_label
                # Show the switch dialog only the first time per session
                first_switch = not self.cfg.get("gpu_switch_shown", False)
                self.cfg["gpu_switch_shown"] = True
                save_config(self.cfg)
                self._update_gpu_btn()
                if first_switch:
                    self._dialog("Compute Device",
                        f"Switched to {best_label} acceleration.\n"
                        "Model cache cleared — reloads on next Speak.", "info")
            else:
                avail = voices.get_available_providers()
                # Filter out cloud/non-GPU providers for the user-facing message
                cloud = {"AzureExecutionProvider", "CPUExecutionProvider"}
                local_gpu = [p for p in avail if p not in cloud]
                note = ""
                if "AzureExecutionProvider" in avail:
                    note = "\n(AzureExecutionProvider detected — this is cloud inference,\nnot a local GPU)"
                self._show_gpu_install_dialog(avail, note)
        else:
            voices.set_provider("CPUExecutionProvider")
            self.cfg["provider"] = "CPU"
            save_config(self.cfg)
            self._update_gpu_btn()


    def _show_gpu_install_dialog(self, avail: list, note: str = ""):
        """Show a dialog that explains GPU status and offers one-click install."""
        import subprocess as _sp, sys as _sys
        win = tk.Toplevel(self.root)
        win.title("GPU Acceleration")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        win.geometry("480x380")

        # Header
        hdr = tk.Frame(win, bg=C["header_bg"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="⚡  GPU Acceleration",
                 font=("Courier New", 11, "bold"),
                 fg=C["accent2"], bg=C["header_bg"],
                 padx=20, pady=10).pack(side="left")
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=C["bg"], padx=20, pady=12)
        body.pack(fill="both", expand=True)

        cloud = {"AzureExecutionProvider", "CPUExecutionProvider"}
        local_gpu = [p for p in avail if p not in cloud]

        if note:
            tk.Label(body, text=note.strip(), font=("Courier New", 8),
                     fg=C["warning"], bg=C["bg"], wraplength=420,
                     justify="left").pack(anchor="w", pady=(0,8))

        tk.Label(body, text="No local GPU provider found. Install one for your hardware:",
                 font=("Courier New", 9), fg=C["text"], bg=C["bg"],
                 justify="left").pack(anchor="w", pady=(0,12))

        status_var = tk.StringVar(value="")
        status_lbl = tk.Label(body, textvariable=status_var,
                               font=("Courier New", 8, "bold"),
                               fg=C["success"], bg=C["bg"])
        status_lbl.pack(anchor="w", pady=(0,8))

        pip_path = str(Path(_sys.executable).parent / "pip")

        def _install(pkg, label):
            status_var.set(f"Installing {label}...")
            status_lbl.configure(fg=C["warning"])
            win.update()
            def _do():
                try:
                    r = _sp.run([pip_path, "install", pkg],
                                capture_output=True, text=True, timeout=180)
                    if r.returncode == 0:
                        self.root.after(0, lambda: status_var.set(
                            f"✓ {label} installed! Restart the app to use it."))
                        self.root.after(0, lambda: status_lbl.configure(fg=C["success"]))
                        bug_tracker.info(f"GPU package installed: {pkg}")
                    else:
                        err = r.stderr.strip()[-120:]
                        self.root.after(0, lambda: status_var.set(f"✗ Failed: {err}"))
                        self.root.after(0, lambda: status_lbl.configure(fg=C["error"]))
                        bug_tracker.error(f"GPU install failed {pkg}: {r.stderr[:300]}")
                except Exception as e:
                    self.root.after(0, lambda: status_var.set(f"✗ Error: {e}"))
                    self.root.after(0, lambda: status_lbl.configure(fg=C["error"]))
            threading.Thread(target=_do, daemon=True).start()

        GPU_OPTIONS = [
            ("NVIDIA GPU",    "onnxruntime-gpu",      "CUDA"),
            ("Intel GPU",    "onnxruntime-openvino",  "OpenVINO"),
            ("AMD GPU",       "onnxruntime-rocm",      "ROCm"),
        ]
        for hw_label, pkg, _ in GPU_OPTIONS:
            row = tk.Frame(body, bg=C["surface"], padx=10, pady=6,
                           highlightthickness=1, highlightbackground=C["border"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=hw_label, font=("Courier New", 9, "bold"),
                     fg=C["text"], bg=C["surface"], width=14, anchor="w").pack(side="left")
            tk.Label(row, text=f"pip install {pkg}",
                     font=("Courier New", 8), fg=C["muted"],
                     bg=C["surface"]).pack(side="left", padx=(0,8))
            tk.Button(row, text="Install",
                      font=("Courier New", 8, "bold"),
                      bg=C["accent"], fg="white", relief="flat",
                      padx=8, pady=3, cursor="hand2",
                      activebackground=C["speak_hover"],
                      command=lambda p=pkg, l=hw_label: _install(p, l)
                      ).pack(side="right")

        tk.Label(body, text=f"Current providers: {', '.join(avail)}",
                 font=("Courier New", 7), fg=C["muted"],
                 bg=C["bg"], wraplength=420).pack(anchor="w", pady=(10,0))

        foot = tk.Frame(win, bg=C["surface"], pady=8)
        foot.pack(fill="x", side="bottom")
        tk.Button(foot, text="  Close  ",
                  font=("Courier New", 9, "bold"),
                  bg=C["surface2"], fg=C["text2"], relief="flat",
                  padx=12, pady=5, command=win.destroy,
                  activebackground=C["border"]).pack(side="right", padx=16)
        win.grab_set()

    def _show_gpu_dialog(self):
        import subprocess
        win = tk.Toplevel(self.root)
        win.title("Compute Device")
        win.configure(bg=C["bg"])
        win.geometry("540x360")
        win.resizable(False, False)
        win.transient(self.root)
        win.update()
        win.grab_set()

        tk.Label(win, text="⚡  Compute Device Status",
                 font=("Courier New",12,"bold"),
                 fg=C["accent2"], bg=C["bg"], pady=16).pack()
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=20)

        avail = voices.get_available_providers()
        prov  = voices.get_current_provider()

        def prov_label(p):
            if "CUDA"    in p: return "NVIDIA CUDA GPU"
            if "OpenVINO" in p: return "Intel GPU (OpenVINO)"
            if "Dml"      in p: return "DirectML GPU"
            return "CPU"

        rows = [
            ("Active Provider",  prov_label(prov)),
            ("CUDA (NVIDIA)",    "✓ Available" if "CUDAExecutionProvider"     in avail else "✗ Not found"),
            ("OpenVINO (Intel)", "✓ Available" if "OpenVINOExecutionProvider" in avail else "✗ Not found"),
            ("All Providers",    ", ".join(avail)),
        ]
        try:
            r = subprocess.run(["nvidia-smi","--query-gpu=name,memory.total",
                                 "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=3)
            rows.append(("NVIDIA GPU", r.stdout.strip() if r.returncode==0 else "Not detected"))
        except Exception:
            rows.append(("NVIDIA GPU", "nvidia-smi unavailable"))

        for label, val in rows:
            color = C["success"] if val.startswith("✓") else C["text"]
            row = tk.Frame(win, bg=C["surface"], padx=16, pady=7,
                           highlightthickness=1, highlightbackground=C["border"])
            row.pack(fill="x", padx=20, pady=3)
            tk.Label(row, text=label+":", font=("Courier New",9),
                     fg=C["text2"], bg=C["surface"],
                     width=20, anchor="w").pack(side="left")
            tk.Label(row, text=val, font=("Courier New",9,"bold"),
                     fg=color, bg=C["surface"],
                     wraplength=280, anchor="w").pack(side="left")

        # Intel GPU explanation
        hint = tk.Frame(win, bg=C["surface2"], padx=14, pady=10)
        hint.pack(fill="x", padx=20, pady=(8,0))
        tk.Label(hint, text="Why is GPU barely faster than CPU?",
                 font=("Courier New",8,"bold"),
                 fg=C["accent2"], bg=C["surface2"]).pack(anchor="w")
        tk.Label(hint,
                 text="Intel Iris Plus is an integrated GPU sharing system RAM.\n"
                      "Kokoro-82M is already fast on CPU (0.3s/chunk). The bottleneck\n"
                      "is the ONNX inference itself, not memory bandwidth.\n"
                      "Dedicated NVIDIA GPU would show 3-8× speedup.",
                 font=("Courier New",8),
                 fg=C["text2"], bg=C["surface2"],
                 justify="left").pack(anchor="w", pady=(2,6))
        tk.Label(hint, text="Enable Intel GPU (OpenVINO):",
                 font=("Courier New",8,"bold"),
                 fg=C["accent2"], bg=C["surface2"]).pack(anchor="w")
        tk.Label(hint,
                 text="pip install onnxruntime-openvino",
                 font=("Courier New",8),
                 fg=C["text2"], bg=C["surface2"]).pack(anchor="w", pady=(2,6))

        install_status = tk.StringVar(value="")
        install_lbl = tk.Label(hint, textvariable=install_status,
                               font=("Courier New",8,"bold"),
                               fg=C["success"], bg=C["surface2"])
        install_lbl.pack(anchor="w")

        def _install_openvino():
            import subprocess, sys
            install_status.set("Installing... please wait")
            install_lbl.configure(fg=C["warning"])
            win.update()
            try:
                # Find the venv pip or use sys.executable
                pip = str(Path(sys.executable).parent / "pip")
                result = subprocess.run(
                    [pip, "install", "onnxruntime-openvino"],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    install_status.set("✓ Installed! Restart TTS Voices to use Intel GPU.")
                    install_lbl.configure(fg=C["success"])
                    bug_tracker.info("onnxruntime-openvino installed successfully")
                else:
                    install_status.set("✗ Failed: " + result.stderr.strip()[:80])
                    install_lbl.configure(fg=C["error"])
                    bug_tracker.error("openvino install failed: " + result.stderr[:200])
            except Exception as e:
                install_status.set(f"✗ Error: {e}")
                install_lbl.configure(fg=C["error"])

        btn_row = tk.Frame(hint, bg=C["surface2"])
        btn_row.pack(anchor="w", pady=(4,0))
        tk.Button(btn_row, text="⬇ Install OpenVINO Now",
                  font=("Courier New",9,"bold"),
                  bg=C["accent"], fg="white", relief="flat",
                  padx=12, pady=5, cursor="hand2",
                  activebackground=C["speak_hover"],
                  activeforeground="white",
                  command=lambda: threading.Thread(
                      target=_install_openvino, daemon=True).start()
                  ).pack(side="left", padx=(0,8))
        tk.Label(btn_row, text="(runs in your venv)",
                 font=("Courier New",7), fg=C["muted"],
                 bg=C["surface2"]).pack(side="left")

        foot_row = tk.Frame(win, bg=C["bg"])
        foot_row.pack(pady=10)
        tk.Button(foot_row, text="Close",
                  font=("Courier New",9,"bold"),
                  bg=C["surface2"], fg=C["text2"],
                  relief="flat", padx=16, pady=6,
                  command=win.destroy,
                  activebackground=C["border"],
                  activeforeground=C["text"]).pack()

    # ── Theme ──────────────────────────────────────────────────────────────────
    def _open_theme_picker(self):
        ThemePickerDialog(self.root, self._theme_key, self._apply_theme)

    def _apply_theme(self, theme_key: str):
        """Apply theme via fast in-place widget recolor. ~15ms, no widget rebuild."""
        self._theme_key = theme_key
        C.update(THEMES[theme_key])
        self.cfg["theme"] = theme_key
        save_config(self.cfg)

        # Build a UNIVERSAL reverse map: any_hex_from_any_theme → palette slot name.
        # This handles multi-step theme switches correctly even when intermediate
        # colors differ, because we look up the slot by hex rather than by "previous" theme.
        universal_slot: dict = {}
        for pal in THEMES.values():
            for slot, hex_val in pal.items():
                if hex_val not in universal_slot:
                    universal_slot[hex_val] = slot

        new_palette = THEMES[theme_key]

        def _map_color(hex_val: str) -> str:
            """Return the new theme color for this hex, or empty string if unknown."""
            slot = universal_slot.get(hex_val)
            return new_palette.get(slot, "") if slot else ""

        def _recolor(widget):
            try:
                # For GlowButtons: update internal _nbg/_hbg Python state so
                # that hover (_enter/_leave) uses the new theme colors.
                # This MUST happen before the visual attr loop below so that
                # _recolor_glow's _leave() call later applies the correct color.
                if isinstance(widget, GlowButton):
                    new_nbg = _map_color(widget._nbg)
                    new_hbg = _map_color(widget._hbg)
                    if new_nbg:
                        widget._nbg = new_nbg
                    if new_hbg:
                        widget._hbg = new_hbg

                for attr in ("bg", "fg", "highlightbackground",
                             "troughcolor", "insertbackground",
                             "selectbackground", "selectforeground",
                             "activebackground", "activeforeground"):
                    try:
                        val = widget.cget(attr)
                        mapped = _map_color(val)
                        if mapped:
                            widget.configure(**{attr: mapped})
                    except Exception:
                        pass
                for child in widget.winfo_children():
                    _recolor(child)
            except Exception:
                pass

        self.root.configure(bg=C["bg"])
        self._style_ttk()
        _recolor(self.root)

        # Force-set named widgets with dynamic / computed colors first,
        # so that _recolor_glow below applies the CORRECT new _nbg/_hbg.
        try:
            self._speak_btn.set_colors(C["speak_bg"], C["speak_hover"])
            self._speak_btn._lbl.configure(fg="white")
        except Exception: pass
        try:
            self._stop_btn.set_colors(C["stop_bg"], C["stop_hover"])
            self._stop_btn._lbl.configure(fg="white")
        except Exception: pass
        try:
            self._hl_toggle.configure(bg=C["surface"])
            self._hl_toggle._on_col  = C.get("accent2", "#00c8ff")
            self._hl_toggle._off_col = C.get("border2", "#223055")
            self._hl_toggle._draw()
        except Exception: pass
        try:
            self._as_toggle.configure(bg=C["surface"])
            self._as_toggle._on_col  = C.get("accent2", "#00c8ff")
            self._as_toggle._off_col = C.get("border2", "#223055")
            self._as_toggle._draw()
        except Exception: pass
        # Explicitly recolor toolbar buttons (Clear / Load File).
        except Exception: pass
        try:
            self._load_btn.set_colors(C["accent_dim"], C["accent"])
            self._load_btn._lbl.configure(fg=C["accent2"])
        except Exception: pass
        try:
            self._update_gpu_btn()
        except Exception: pass
        # Update all regular nav buttons with new theme colors before _recolor_glow
        try:
            for btn in getattr(self, "_nav_btns", []):
                if getattr(btn, "_is_accent", False):
                    continue  # GPU button handled by _update_gpu_btn above
                btn.set_colors(C["nav_btn"], C["nav_hover"])
                btn._lbl.configure(fg=C["text"])
        except Exception: pass

        # Force GlowButtons to re-apply their (now-correct) normal-state color.
        # Must run AFTER set_colors calls above so _nbg/_hbg are already updated.
        def _recolor_glow(widget):
            try:
                if isinstance(widget, GlowButton):
                    widget._leave()   # applies updated _nbg to frame + label
                for child in widget.winfo_children():
                    _recolor_glow(child)
            except Exception:
                pass
        _recolor_glow(self.root)

        try:
            self._textarea.configure(
                bg=C["textarea_bg"], fg=C["textarea_fg"],
                insertbackground=C["cursor"],
                selectbackground=C["sel_bg"],
                selectforeground=C["text"])
            if self._placeholder_active:
                self._textarea.configure(fg=C["muted"])
        except Exception: pass
        try:
            self._status_pill.configure(fg=C["success"],
                                        highlightbackground=C["success"])
        except Exception: pass
        try:
            self._voice_combo.configure(style="TCombobox")
        except Exception: pass
        # Force-redraw export buttons so their drawn rectangles update to new theme.
        # _snap_colors() inside _redraw() ensures hover uses the new palette immediately.
        try:
            self._wav_btn._redraw()
            self._mp3_btn._redraw()
        except Exception: pass

        if bug_tracker:
            bug_tracker.info(f"Theme → {theme_key}")


    # ── Audio-to-Text window ───────────────────────────────────────────────────
    def _open_audio_to_text(self):
        AudioToTextWindow(self.root, self._on_att_transcript)

    def _on_att_transcript(self, text: str):
        """Called when Audio-to-Text window produces a transcript."""
        if not text:
            return
        self._remove_placeholder()
        self._textarea.delete("1.0", "end")
        self._textarea.insert("1.0", text)
        self._update_wordcount()
        self._set_status("✓ Transcript loaded — press Speak", C["success"])

    # ── Nav helpers ────────────────────────────────────────────────────────────
    def _open_voice_library(self):
        from voice_library import VoiceLibraryWindow; VoiceLibraryWindow(self.root)

    def _open_bug_tracker(self):
        win=tk.Toplevel(self.root); win.title("Bug Tracker / Session Log")
        win.geometry("720x540"); win.configure(bg=C["bg"])
        win.transient(self.root)
        # Header
        hdr = tk.Frame(win, bg=C["header_bg"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="⚐  Bug Tracker / Session Log",
                 font=("Courier New",11,"bold"), fg=C["accent2"], bg=C["header_bg"],
                 padx=16, pady=10).pack(side="left")
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")
        tk.Label(win, text=f"Log: {bug_tracker.get_log_path()}",
                 font=("Courier New",8), fg=C["muted"], bg=C["bg"],
                 padx=16, pady=4).pack(anchor="w")
        # Text + scrollbar
        ta_frame = tk.Frame(win, bg=C["bg"])
        ta_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        ta_frame.columnconfigure(0, weight=1); ta_frame.rowconfigure(0, weight=1)
        txt = tk.Text(ta_frame, bg=C["surface"], fg=C["text"], font=("Courier New",9),
                      relief="flat", padx=12, pady=10, wrap="word")
        txt.grid(row=0, column=0, sticky="nsew")
        vsc = tk.Scrollbar(ta_frame, command=txt.yview, bg=C["surface2"],
                           troughcolor=C["bg"], width=10, relief="flat")
        vsc.grid(row=0, column=1, sticky="ns")
        txt.configure(yscrollcommand=vsc.set)
        # Insert log — then position view at bottom (most recent entries)
        txt.insert("1.0", bug_tracker.get_report())
        txt.configure(state="disabled")
        # Smart scroll: go to bottom only if user hasn't scrolled up
        txt.after(50, lambda: txt.see("end") if txt.yview()[1] >= 0.95 else None)
        # Mousewheel
        def _log_scroll(e):
            if e.num == 4 or getattr(e, "delta", 0) > 0:
                txt.yview_scroll(-3, "units")
            else:
                txt.yview_scroll(3, "units")
        txt.bind("<Button-4>", _log_scroll)
        txt.bind("<Button-5>", _log_scroll)
        # Refresh button
        foot = tk.Frame(win, bg=C["surface"], pady=6)
        foot.pack(fill="x", side="bottom")
        def _refresh_log():
            # Clear both the in-memory deque and the session log file.
            # New entries will only appear after fresh activity (speak/load/theme).
            try:
                bug_tracker.clear_log()
            except Exception:
                pass
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", "[Log cleared — new entries will appear after next activity]\n")
            txt.see("end")
            txt.configure(state="disabled")
        tk.Button(foot, text="  Refresh  ", font=("Courier New",9,"bold"),
                  bg=C["surface2"], fg=C["text2"], relief="flat", padx=10, pady=4,
                  activebackground=C["border"],
                  command=_refresh_log
                  ).pack(side="left", padx=12)
        tk.Button(foot, text="  Close  ", font=("Courier New",9,"bold"),
                  bg=C["accent"], fg="white", relief="flat", padx=10, pady=4,
                  activebackground=C["speak_hover"],
                  command=win.destroy).pack(side="right", padx=12)



    def _dark_confirm(self, parent, title: str, message: str,
                      confirm_text: str = "Yes", cancel_text: str = "No") -> bool:
        """Themed confirm dialog — replaces messagebox.askyesno."""
        result = [False]
        d = tk.Toplevel(parent)
        d.title(title)
        d.configure(bg=C["bg"])
        d.transient(parent)
        d.resizable(False, False)
        d.grab_set()

        tk.Frame(d, bg=C["surface"]).pack(fill="x")
        body = tk.Frame(d, bg=C["bg"], padx=24, pady=18); body.pack(fill="x")
        tk.Label(body, text=message,
                 font=("Courier New", 10), fg=C["text"],
                 bg=C["bg"], wraplength=340, justify="left").pack(anchor="w")

        tk.Frame(d, bg=C["border"], height=1).pack(fill="x")
        foot = tk.Frame(d, bg=C["surface2"], pady=8); foot.pack(fill="x")

        def _yes():
            result[0] = True; d.destroy()
        def _no():
            result[0] = False; d.destroy()

        tk.Button(foot, text=f"  {cancel_text}  ",
                  font=("Courier New", 9), bg=C["surface"], fg=C["muted"],
                  relief="flat", padx=10, pady=5, cursor="hand2",
                  activebackground=C["border"],
                  command=_no).pack(side="right", padx=(4,12))
        tk.Button(foot, text=f"  {confirm_text}  ",
                  font=("Courier New", 9, "bold"), bg=C["error"], fg="white",
                  relief="flat", padx=10, pady=5, cursor="hand2",
                  activebackground="#c04040",
                  command=_yes).pack(side="right", padx=4)

        d.bind("<Return>", lambda _: _yes())
        d.bind("<Escape>", lambda _: _no())

        d.update_idletasks()
        sw = parent.winfo_screenwidth(); sh = parent.winfo_screenheight()
        w = max(d.winfo_reqwidth(), 360); h = d.winfo_reqheight()
        d.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        parent.wait_window(d)
        return result[0]

    def _dark_error(self, parent, title: str, message: str):
        """Themed error dialog — replaces messagebox.showerror."""
        d = tk.Toplevel(parent)
        d.title(title)
        d.configure(bg=C["bg"])
        d.transient(parent)
        d.resizable(False, False)
        d.grab_set()

        body = tk.Frame(d, bg=C["bg"], padx=24, pady=18); body.pack(fill="x")
        tk.Label(body, text="✗  " + title,
                 font=("Courier New", 10, "bold"),
                 fg=C["error"], bg=C["bg"]).pack(anchor="w", pady=(0,8))
        tk.Label(body, text=message,
                 font=("Courier New", 9), fg=C["text2"],
                 bg=C["bg"], wraplength=360, justify="left").pack(anchor="w")

        tk.Frame(d, bg=C["border"], height=1).pack(fill="x")
        foot = tk.Frame(d, bg=C["surface2"], pady=8); foot.pack(fill="x")
        tk.Button(foot, text="  OK  ",
                  font=("Courier New", 9), bg=C["surface"], fg=C["text"],
                  relief="flat", padx=12, pady=5, cursor="hand2",
                  command=d.destroy).pack(side="right", padx=12)

        d.bind("<Return>", lambda _: d.destroy())
        d.bind("<Escape>", lambda _: d.destroy())
        d.update_idletasks()
        sw = parent.winfo_screenwidth(); sh = parent.winfo_screenheight()
        w = max(d.winfo_reqwidth(), 380); h = d.winfo_reqheight()
        d.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        d.grab_set()

    def _pick_plugin_file(self, parent_win) -> str:
        """
        Dark-themed file picker for .py plugin files.
        Returns the selected file path, or "" if cancelled.
        Searches HOME, Downloads, and common dev folders.
        """
        import os

        picker = tk.Toplevel(parent_win)
        picker.title("Select Plugin (.py)")
        picker.configure(bg=C["bg"])
        picker.transient(parent_win)
        picker.resizable(True, True)
        picker.geometry("620x440")
        picker.grab_set()

        result = [""]

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(picker, bg=C["surface"]); hdr.pack(fill="x")
        tk.Label(hdr, text="⊕  Select Plugin (.py)",
                 font=("Courier New", 10, "bold"),
                 fg=C["accent2"], bg=C["surface"],
                 padx=16, pady=10).pack(side="left")
        tk.Frame(picker, bg=C["border"], height=1).pack(fill="x")

        # ── Path bar ──────────────────────────────────────────────────────────
        path_bar = tk.Frame(picker, bg=C["surface2"], padx=8, pady=4)
        path_bar.pack(fill="x")
        path_var = tk.StringVar(value=str(Path.home()))
        path_entry = tk.Entry(path_bar, textvariable=path_var,
                              font=("Courier New", 8),
                              bg=C["surface"], fg=C["text"],
                              insertbackground=C["accent2"],
                              relief="flat", bd=4)
        path_entry.pack(side="left", fill="x", expand=True)

        # ── File list ─────────────────────────────────────────────────────────
        list_outer = tk.Frame(picker, bg=C["bg"])
        list_outer.pack(fill="both", expand=True, padx=0, pady=0)
        sb = tk.Scrollbar(list_outer, orient="vertical",
                          bg=C["surface2"], troughcolor=C["bg"],
                          width=10, relief="flat")
        sb.pack(side="right", fill="y")
        listbox = tk.Listbox(list_outer,
                             font=("Courier New", 9),
                             bg=C["surface"], fg=C["text"],
                             selectbackground=C["accent"],
                             selectforeground="white",
                             activestyle="none",
                             relief="flat", bd=0,
                             yscrollcommand=sb.set)
        listbox.pack(side="left", fill="both", expand=True)
        sb.config(command=listbox.yview)

        _entries = []   # list of (display_name, full_path, is_dir)

        def _populate(directory: str):
            listbox.delete(0, "end")
            _entries.clear()
            try:
                items = sorted(os.scandir(directory), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                return
            # Parent directory entry
            parent = str(Path(directory).parent)
            if parent != directory:
                _entries.append(("..", parent, True))
                listbox.insert("end", "  📁  ..")
                listbox.itemconfig(0, fg=C["text2"])
            for entry in items:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    _entries.append((entry.name, entry.path, True))
                    listbox.insert("end", f"  📁  {entry.name}")
                    listbox.itemconfig("end", fg=C["text2"])
                elif entry.name.endswith(".py"):
                    _entries.append((entry.name, entry.path, False))
                    listbox.insert("end", f"  🔌  {entry.name}")
            path_var.set(directory)

        _populate(str(Path.home()))

        def _on_double_click(_e=None):
            sel = listbox.curselection()
            if not sel: return
            name, fpath, is_dir = _entries[sel[0]]
            if is_dir:
                _populate(fpath)
            else:
                result[0] = fpath
                picker.destroy()

        def _on_path_enter(_e=None):
            p = path_var.get().strip()
            if Path(p).is_dir():
                _populate(p)

        listbox.bind("<Double-Button-1>", _on_double_click)
        listbox.bind("<Return>",          _on_double_click)
        path_entry.bind("<Return>",       _on_path_enter)

        # ── Quick-access sidebar ──────────────────────────────────────────────
        shortcuts = [
            ("🏠 Home",      str(Path.home())),
            ("⬇ Downloads", str(Path.home() / "Downloads")),
            ("📄 Documents", str(Path.home() / "Documents")),
        ]
        # Also add plugins dir
        shortcuts.append(("⊕ Plugins", str(PLUGINS_DIR)))

        side = tk.Frame(picker, bg=C["surface2"], width=130)
        side.pack(side="left", fill="y", before=list_outer)
        tk.Label(side, text="Quick Access",
                 font=("Courier New", 7, "bold"),
                 fg=C["muted"], bg=C["surface2"],
                 pady=6).pack(fill="x", padx=8)
        for label, spath in shortcuts:
            tk.Button(side, text=label,
                      font=("Courier New", 8),
                      bg=C["surface2"], fg=C["text2"],
                      relief="flat", anchor="w", padx=8, pady=3,
                      cursor="hand2", activebackground=C["border"],
                      command=lambda p=spath: _populate(p)).pack(fill="x")

        # ── Footer ────────────────────────────────────────────────────────────
        tk.Frame(picker, bg=C["border"], height=1).pack(fill="x")
        foot = tk.Frame(picker, bg=C["surface2"], pady=8)
        foot.pack(fill="x")
        selected_var = tk.StringVar(value="No file selected")
        tk.Label(foot, textvariable=selected_var,
                 font=("Courier New", 8), fg=C["muted"],
                 bg=C["surface2"]).pack(side="left", padx=12)

        def _on_select(_e=None):
            sel = listbox.curselection()
            if not sel: return
            name, fpath, is_dir = _entries[sel[0]]
            if not is_dir:
                selected_var.set(name)

        listbox.bind("<<ListboxSelect>>", _on_select)

        def _confirm():
            sel = listbox.curselection()
            if sel:
                name, fpath, is_dir = _entries[sel[0]]
                if not is_dir:
                    result[0] = fpath
                    picker.destroy()
                    return
            # Try path entry directly
            p = path_var.get().strip()
            if p.endswith(".py") and Path(p).is_file():
                result[0] = p
                picker.destroy()

        tk.Button(foot, text="  Cancel  ",
                  font=("Courier New", 9),
                  bg=C["surface"], fg=C["muted"],
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=picker.destroy).pack(side="right", padx=(4,12))
        tk.Button(foot, text="  Open  ",
                  font=("Courier New", 9, "bold"),
                  bg=C["accent"], fg="white",
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  activebackground=C["speak_hover"],
                  command=_confirm).pack(side="right", padx=4)

        picker.update_idletasks()
        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
        picker.geometry(f"620x440+{(sw-620)//2}+{(sh-440)//2}")
        parent_win.wait_window(picker)
        return result[0]

    def _open_settings(self):
        win = tk.Toplevel(self.root); win.title("Settings")
        win.geometry("500x560"); win.configure(bg=C["bg"]); win.transient(self.root)
        win.resizable(False, True); win.minsize(500, 400)

        # ── Fixed header ────────────────────────────────────────────────────
        tk.Label(win, text="⚙  Settings", font=("Courier New",12,"bold"),
                 fg=C["accent2"], bg=C["bg"], pady=14).pack()

        # ── Scrollable body ─────────────────────────────────────────────────
        _scroll_outer = tk.Frame(win, bg=C["bg"]); _scroll_outer.pack(fill="both", expand=True)
        _sb = tk.Scrollbar(_scroll_outer, orient="vertical", bg=C["surface2"],
                           troughcolor=C["bg"], width=10, relief="flat", bd=0)
        _sb.pack(side="right", fill="y")
        _sc = tk.Canvas(_scroll_outer, bg=C["bg"], highlightthickness=0,
                        yscrollcommand=_sb.set)
        _sc.pack(side="left", fill="both", expand=True)
        _sb.config(command=_sc.yview)
        win_inner = tk.Frame(_sc, bg=C["bg"])
        _sc_win_id = _sc.create_window((0, 0), window=win_inner, anchor="nw")

        def _on_inner_configure(_e=None):
            _sc.configure(scrollregion=_sc.bbox("all"))
        def _on_canvas_resize(e):
            _sc.itemconfig(_sc_win_id, width=e.width)
        win_inner.bind("<Configure>", _on_inner_configure)
        _sc.bind("<Configure>", _on_canvas_resize)

        def _bind_mousewheel(widget):
            for seq in ("<MouseWheel>","<Button-4>","<Button-5>"):
                widget.bind(seq, _on_settings_scroll, add="+")
            for child in widget.winfo_children():
                _bind_mousewheel(child)
        def _on_settings_scroll(e):
            if e.num == 4 or (hasattr(e,"delta") and e.delta > 0):
                _sc.yview_scroll(-1, "units")
            else:
                _sc.yview_scroll(1, "units")
        _sc.bind("<MouseWheel>", _on_settings_scroll)
        _sc.bind("<Button-4>",   _on_settings_scroll)
        _sc.bind("<Button-5>",   _on_settings_scroll)
        win_inner.after(200, lambda: _bind_mousewheel(win_inner))

        # Keep a reference to the real Toplevel for destroy/centering
        _toplevel = win
        # Now all content packs into win_inner instead of win
        win = win_inner   # rebind win so all existing pack() calls go to the scrollable frame

        frm=tk.Frame(win,bg=C["surface"],padx=16,pady=12)
        frm.pack(fill="x",padx=16,pady=4)
        tk.Label(frm,text=f"Config:  {CONFIG_DIR}",
                 font=("Courier New",9),fg=C["text2"],bg=C["surface"]).pack(anchor="w")
        tk.Label(frm,text=f"Models:  {voices.MODELS_DIR if voices else _MODELS_DIR}",
                 font=("Courier New",9),fg=C["text2"],bg=C["surface"]).pack(anchor="w",pady=(4,0))

        frm2=tk.Frame(win,bg=C["surface"],padx=16,pady=12)
        frm2.pack(fill="x",padx=16,pady=4)
        tk.Label(frm2,text="Chunk size (words):",
                 font=("Courier New",9),fg=C["text2"],bg=C["surface"]).pack(anchor="w")
        cv=tk.IntVar(value=self.cfg.get("chunk_words",200))
        tk.Scale(frm2,variable=cv,from_=50,to=500,orient="horizontal",
                 bg=C["surface"],fg=C["text"],troughcolor=C["surface2"],
                 highlightthickness=0,activebackground=C["accent"]).pack(fill="x")

        frm3=tk.Frame(win,bg=C["surface"],padx=16,pady=12)
        frm3.pack(fill="x",padx=16,pady=4)
        offset_lbl = tk.Label(frm3,
                 text=f"Highlight sync offset:  {self.cfg.get('highlight_offset',150)} ms",
                 font=("Courier New",9),fg=C["text2"],bg=C["surface"])
        offset_lbl.pack(anchor="w")
        tk.Label(frm3,
                 text="Higher = highlights wait longer (speech ahead of highlight)  |  Lower = highlights fire sooner (highlight ahead of speech)",
                 font=("Courier New",7),fg=C["muted"],bg=C["surface"],justify="left").pack(anchor="w")
        ov=tk.IntVar(value=self.cfg.get("highlight_offset",150))
        def _on_offset(*_):
            offset_lbl.configure(text=f"Highlight sync offset:  {ov.get()} ms")
        ov.trace_add("write", _on_offset)
        tk.Scale(frm3,variable=ov,from_=-200,to=500,orient="horizontal",
                 bg=C["surface"],fg=C["text"],troughcolor=C["surface2"],
                 highlightthickness=0,activebackground=C["accent"]).pack(fill="x")

        # ── Auto-update toggle ─────────────────────────────────────────────────
        frm4 = tk.Frame(win, bg=C["surface"], padx=16, pady=10)
        frm4.pack(fill="x", padx=16, pady=4)
        tk.Label(frm4, text="Updates",
                 font=("Courier New", 9, "bold"),
                 fg=C["text2"], bg=C["surface"]).pack(anchor="w", pady=(0,6))

        upd_row = tk.Frame(frm4, bg=C["surface"]); upd_row.pack(fill="x", pady=(0,4))
        tk.Label(upd_row, text="Auto-check for updates on startup",
                 font=("Courier New", 9), fg=C["text2"],
                 bg=C["surface"]).pack(side="left")
        _upd_state = [self.cfg.get("auto_update_check", True)]
        def _on_upd_toggle(s):
            _upd_state[0] = s
        _upd_pill = PillToggle(upd_row, state=_upd_state[0], callback=_on_upd_toggle)
        _upd_pill.pack(side="right")

        # Dep checker row
        dep_sep = tk.Frame(frm4, bg=C["border2"], height=1)
        dep_sep.pack(fill="x", pady=(6,6))
        dep_hdr = tk.Frame(frm4, bg=C["surface"]); dep_hdr.pack(fill="x")
        tk.Label(dep_hdr, text="Check for package updates (venv pip):",
                 font=("Courier New", 9), fg=C["text2"],
                 bg=C["surface"]).pack(side="left")
        dep_status_var = tk.StringVar(value="")
        dep_status_lbl = tk.Label(frm4, textvariable=dep_status_var,
                                   font=("Courier New", 8), fg=C["muted"],
                                   bg=C["surface"], wraplength=380, justify="left")
        dep_status_lbl.pack(anchor="w", pady=(2,0))

        def _run_dep_check():
            dep_status_var.set("⟳ Checking (may take a few seconds)…")
            try: dep_status_lbl.configure(fg=C["warning"])
            except Exception: pass
            def _worker():
                outdated = self._check_deps_outdated()
                def _show():
                    # Guard: window may have been closed before thread finished
                    try:
                        if not dep_status_lbl.winfo_exists(): return
                    except Exception: return
                    if not outdated:
                        dep_status_var.set("✓ All packages are up to date.")
                        dep_status_lbl.configure(fg=C["success"])
                    else:
                        _outdated_list[0] = outdated
                        lines = [f"⬆ {len(outdated)} update(s) available:"]
                        for p in outdated:
                            lines.append(f"  {p['name']}  {p['version']} → {p['latest_version']}")
                        dep_status_var.set("\n".join(lines))
                        dep_status_lbl.configure(fg=C["warning"])
                        try: _install_btn.configure(state="normal")
                        except Exception: pass
                try: win.after(0, _show)
                except Exception: pass
            import threading as _thr
            _thr.Thread(target=_worker, daemon=True).start()

        _install_btn_var = tk.StringVar(value=" Install All ")
        _install_btn = tk.Button(dep_hdr, textvariable=_install_btn_var,
                  font=("Courier New", 8, "bold"), bg=C["success"], fg="white",
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  activebackground="#1a6a30",
                  state="disabled")
        _install_btn.pack(side="right", padx=(4,0))

        def _install_updates():
            _install_btn.configure(state="disabled")
            dep_status_var.set("⟳ Installing updates…")
            try: dep_status_lbl.configure(fg=C["warning"])
            except Exception: pass
            import sys as _sys, subprocess as _sp2, threading as _thr2
            venv_pip = str(Path(_sys.prefix) / "bin" / "pip")
            if not Path(venv_pip).exists(): venv_pip = "pip"
            pkgs = [p["name"] for p in _outdated_list[0]]
            def _worker2():
                try:
                    r2 = _sp2.run(
                        [venv_pip, "install", "--upgrade"] + pkgs,
                        capture_output=True, text=True, timeout=120
                    )
                    def _done():
                        try:
                            if not dep_status_lbl.winfo_exists(): return
                        except Exception: return
                        if r2.returncode == 0:
                            dep_status_var.set(f"✓ {len(pkgs)} package(s) updated successfully.")
                            dep_status_lbl.configure(fg=C["success"])
                            _install_btn.configure(state="disabled")
                            _outdated_list[0] = []
                        else:
                            dep_status_var.set(f"✗ Install failed. Check Bug Log.")
                            dep_status_lbl.configure(fg=C["error"])
                            if bug_tracker: bug_tracker.error(f"pip upgrade failed:\n{r2.stderr}")
                    try: win.after(0, _done)
                    except Exception: pass
                except Exception as e:
                    try: win.after(0, lambda: dep_status_var.set(f"✗ Error: {e}"))
                    except Exception: pass
            _thr2.Thread(target=_worker2, daemon=True).start()

        _install_btn.configure(command=_install_updates)
        _outdated_list = [[]]  # mutable container so _install_updates can read it

        tk.Button(dep_hdr, text=" Check now ",
                  font=("Courier New", 8), bg=C["surface2"], fg=C["accent2"],
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  highlightthickness=1, highlightbackground=C["border2"],
                  activebackground=C["border"],
                  command=_run_dep_check).pack(side="right")


        # ── Plugins Section (read-only — manage via ⊕ Plugins in the nav bar) ─
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x", padx=16, pady=(4,2))
        plg_outer = tk.Frame(win, bg=C["surface"], padx=16, pady=10)
        plg_outer.pack(fill="x", padx=16, pady=(0,6))

        plg_hdr = tk.Frame(plg_outer, bg=C["surface"]); plg_hdr.pack(fill="x")
        tk.Label(plg_hdr, text="Plugins",
                 font=("Courier New", 9, "bold"),
                 fg=C["text2"], bg=C["surface"]).pack(side="left")
        tk.Label(plg_hdr, text="Manage via  ⊕ Plugins  in the nav bar",
                 font=("Courier New", 7), fg=C["muted"],
                 bg=C["surface"]).pack(side="left", padx=(10,0))

        # Status summary only — no Add/Remove here
        n = len(self._loaded_plugins)
        errors = sum(1 for p in self._loaded_plugins if "error" in p)
        if n == 0:
            summary = "No plugins loaded.  Drop .py files into  ~/.ttsvoices/plugins/"
            summary_col = C["muted"]
        elif errors:
            summary = f"{n} plugin(s) loaded  ·  {errors} error(s) — see ⊕ Plugins"
            summary_col = C["warning"]
        else:
            summary = f"{n} plugin(s) active  ·  ✓ all loaded successfully"
            summary_col = C["success"]

        tk.Label(plg_outer, text=summary,
                 font=("Courier New", 8), fg=summary_col,
                 bg=C["surface"], wraplength=400, justify="left",
                 pady=4).pack(anchor="w")


        def _save():
            self.cfg["chunk_words"] = cv.get()
            self.cfg["highlight_offset"] = ov.get()
            self.cfg["auto_update_check"] = _upd_state[0]
            save_config(self.cfg)
            # Sync the right-panel toggle immediately
            try: self._update_toggle._set(self.cfg["auto_update_check"])
            except Exception: pass
            _toplevel.destroy()   # destroy the real Toplevel, not win_inner
        GlowButton(_toplevel, text="Save", command=_save, normal_bg=C["accent"],
                   hover_bg=C["speak_hover"], fg="white").pack(pady=12)

    # ── Misc ───────────────────────────────────────────────────────────────────
    def _set_status(self, text, color=None):
        color = color or C["success"]
        self.status_var.set(text)
        self._status_pill.configure(fg=color, highlightbackground=color)

    def _set_export_status(self, text, color=None, fg=None):
        """Route export status text + colour into the export status label.
        Accepts both positional color= and keyword fg= so old call-sites work."""
        c = color or fg
        try:
            self._play_progress_str.set(text)
            if c:
                try: self._play_progress_lbl.configure(fg=c)
                except Exception: pass
        except Exception:
            pass

    def _apply_volume(self, *_):
        """Push current 0-100 volume to audio handler AND system mixer."""
        try:
            vol_pct = max(0, min(100, self.volume_var.get()))
            if audio_handler:
                audio_handler.set_volume_level(int(vol_pct * 327.67))
                # Real-time system volume — takes effect on current chunk instantly
                audio_handler.set_system_volume(vol_pct)
        except Exception:
            pass

    def _on_volume_change(self, *_):
        """Volume slider changed — update config and audio level, never re-synthesize."""
        self.cfg["volume"] = max(0, min(100, self.volume_var.get()))
        self._apply_volume()
        if hasattr(self, "_cfg_save_after_id") and self._cfg_save_after_id:
            try: self.root.after_cancel(self._cfg_save_after_id)
            except Exception: pass
        self._cfg_save_after_id = self.root.after(800, lambda: save_config(self.cfg))


    # ══════════════════════════════════════════════════════════════════════════
    #  AUTO-UPDATE CHECKER
    # ══════════════════════════════════════════════════════════════════════════
    _VERSION_URL = (
        "https://raw.githubusercontent.com/"
        "jspgamer0503-coder/TTSVoices/main/VERSION"
    )
    _update_available_version: str = ""
    _update_glow_job = None
    _update_glow_phase = 0

    def _start_update_check_if_enabled(self):
        """Called from _finish_init: run update check in background if toggled on."""
        if self.cfg.get("auto_update_check", True):
            self.root.after(800, self._check_for_update_bg)   # 0.8s delay so UI settles

    def _check_for_update_bg(self, manual: bool = False):
        """Fire a background thread to check the remote VERSION file."""
        if manual:
            try:
                self._update_btn._lbl.configure(text="⟳ Checking…", fg=C["text2"])
                self._update_btn.set_colors(C["nav_btn"], C["nav_hover"])
            except Exception:
                pass

        def _worker():
            try:
                import urllib.request as _ur, re as _re, socket as _sock
                req = _ur.Request(
                    self._VERSION_URL,
                    headers={"User-Agent": f"TTSVoices/{__version__}"}
                )
                with _ur.urlopen(req, timeout=1.5) as resp:
                    if resp.status != 200:
                        return
                    latest = resp.read(32).decode("utf-8", errors="ignore").strip()
                if not latest or not _re.match(r"^\d+\.\d+", latest):
                    return
                if latest != __version__:
                    self.root.after(0, lambda: self._show_update_available(latest))
                else:
                    self.root.after(0, lambda: self._show_update_current(manual))
            except Exception as e:
                if manual:
                    self.root.after(0, lambda: self._show_update_error(str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_available(self, latest: str):
        """Glow the update button and show 'Update now' text."""
        self._update_available_version = latest
        try:
            self._update_btn._lbl.configure(
                text=f"⬆ Update now  ({latest})",
                fg=C["warning"]
            )
            self._update_btn.set_colors("#5a3800", "#7a5000")
        except Exception:
            pass
        self._pulse_update_btn()
        if bug_tracker:
            bug_tracker.info(f"Update available: {latest} (current: {__version__})")

    def _show_update_current(self, manual: bool = False):
        """Restore button to quiet state when up to date."""
        self._update_available_version = ""
        try:
            self._update_btn._lbl.configure(
                text="⟳ Up to date" if manual else "⟳ Updates",
                fg=C["text2"]
            )
            self._update_btn.set_colors(C["nav_btn"], C["nav_hover"])
        except Exception:
            pass
        if manual:
            self.root.after(4000, lambda: self._reset_update_btn_label())

    def _show_update_error(self, err: str):
        """Show brief error on manual check failure."""
        try:
            self._update_btn._lbl.configure(text="⟳ Updates", fg=C["text2"])
            self._update_btn.set_colors(C["nav_btn"], C["nav_hover"])
        except Exception:
            pass
        if bug_tracker:
            bug_tracker.warning(f"Update check failed: {err}")

    def _reset_update_btn_label(self):
        try:
            if not self._update_available_version:
                self._update_btn._lbl.configure(text="⟳ Updates", fg=C["text2"])
        except Exception:
            pass

    def _pulse_update_btn(self):
        """Subtle pulse animation on the update button border."""
        if self._update_glow_job:
            try: self.root.after_cancel(self._update_glow_job)
            except Exception: pass
        colours = ["#7a5000", "#b07800", "#f0a800", "#b07800", "#7a5000"]
        phase = [0]
        def _step():
            if not self._update_available_version:
                return
            try:
                c = colours[phase[0] % len(colours)]
                self._update_btn.configure(highlightbackground=c)
                phase[0] += 1
            except Exception:
                return
            self._update_glow_job = self.root.after(600, _step)
        _step()


    def _open_plugins_manager(self):
        """Standalone Plugins Manager window — also accessible from the nav bar."""
        win = tk.Toplevel(self.root)
        win.title("Plugin Manager")
        win.geometry("520x480")
        win.configure(bg=C["bg"])
        win.transient(self.root)
        win.resizable(True, True)

        # Header
        hdr = tk.Frame(win, bg=C["surface"]); hdr.pack(fill="x")
        tk.Label(hdr, text="⊕  Plugin Manager",
                 font=("Courier New", 12, "bold"),
                 fg=C["accent2"], bg=C["surface"],
                 padx=20, pady=12).pack(side="left")
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        # Info bar
        info = tk.Frame(win, bg=C["bg"], padx=16, pady=6); info.pack(fill="x")
        tk.Label(info, text="Plugins live in:", font=("Courier New", 8),
                 fg=C["muted"], bg=C["bg"]).pack(side="left")
        dir_lbl = tk.Label(info, text=str(PLUGINS_DIR),
                            font=("Courier New", 8, "bold"),
                            fg=C["accent2"], bg=C["bg"], cursor="hand2")
        dir_lbl.pack(side="left", padx=(4,0))
        def _open_dir():
            import subprocess as _sp
            try: _sp.Popen(["xdg-open", str(PLUGINS_DIR)])
            except Exception: pass
        dir_lbl.bind("<Button-1>", lambda _: _open_dir())

        # + Add Plugin button
        def _add_plugin():
            import shutil as _sh
            fp = self._pick_plugin_file(win)
            if not fp: return
            PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
            dest = PLUGINS_DIR / Path(fp).name
            try:
                _sh.copy2(fp, dest)
                self._load_plugins()
                _refresh()
            except Exception as e:
                self._dark_error(win, "Plugin Manager", f"Could not install:\n{e}")

        tk.Button(info, text=" + Add Plugin ",
                  font=("Courier New", 9, "bold"),
                  bg=C["accent"], fg="white",
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  activebackground=C["speak_hover"],
                  command=_add_plugin).pack(side="right")

        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        # Plugin list (scrollable)
        list_outer = tk.Frame(win, bg=C["bg"]); list_outer.pack(fill="both", expand=True, padx=16, pady=8)
        sb = tk.Scrollbar(list_outer, orient="vertical", bg=C["surface2"],
                          troughcolor=C["bg"], width=10, relief="flat")
        sb.pack(side="right", fill="y")
        canvas = tk.Canvas(list_outer, bg=C["bg"], highlightthickness=0,
                           yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.config(command=canvas.yview)
        list_frame = tk.Frame(canvas, bg=C["bg"])
        _cw_id = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        list_frame.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(_cw_id, width=e.width))
        for seq in ("<Button-4>","<Button-5>"):
            canvas.bind(seq, lambda e: canvas.yview_scroll(-1 if e.num==4 else 1, "units"))

        def _refresh():
            for w in list_frame.winfo_children():
                w.destroy()
            plugins = self._loaded_plugins
            if not plugins:
                empty = tk.Frame(list_frame, bg=C["bg"]); empty.pack(fill="x", pady=20)
                tk.Label(empty, text="No plugins installed.",
                         font=("Courier New", 10, "bold"),
                         fg=C["muted"], bg=C["bg"]).pack()
                tk.Label(empty,
                         text='Click  + Add Plugin  to install a .py file.\nOr drop a .py into the plugins folder and reopen this window.',
                         font=("Courier New", 8), fg=C["muted"], bg=C["bg"],
                         justify="center").pack(pady=4)
                return

            for p in plugins:
                has_error = "error" in p
                card = tk.Frame(list_frame, bg=C["surface"],
                                highlightthickness=1,
                                highlightbackground=C["border2"])
                card.pack(fill="x", pady=3)

                # Left status strip
                strip_col = C["error"] if has_error else C["success"]
                tk.Frame(card, bg=strip_col, width=4).pack(side="left", fill="y")

                body = tk.Frame(card, bg=C["surface"], padx=10, pady=8)
                body.pack(side="left", fill="both", expand=True)

                name_row = tk.Frame(body, bg=C["surface"]); name_row.pack(fill="x")
                tk.Label(name_row,
                         text=f"⊕ {p['name']}",
                         font=("Courier New", 10, "bold"),
                         fg=C["accent2"] if not has_error else C["error"],
                         bg=C["surface"]).pack(side="left")

                status = "✓ Active" if not has_error else f"✗ Error"
                tk.Label(name_row,
                         text=status,
                         font=("Courier New", 8),
                         fg=C["success"] if not has_error else C["error"],
                         bg=C["surface"]).pack(side="left", padx=(8,0))

                path_lbl = tk.Label(body, text=p["path"],
                                    font=("Courier New", 7), fg=C["muted"],
                                    bg=C["surface"], anchor="w")
                path_lbl.pack(fill="x", pady=(2,0))

                if has_error:
                    tk.Label(body, text=p["error"],
                             font=("Courier New", 8), fg=C["error"],
                             bg=C["surface"], wraplength=380, anchor="w").pack(fill="x", pady=(2,0))
                else:
                    reg_text = "register(app) ✓" if p.get("has_register") else "loaded — no register() found"
                    tk.Label(body, text=reg_text,
                             font=("Courier New", 8), fg=C["muted"],
                             bg=C["surface"], anchor="w").pack(fill="x")

                # Remove button
                def _make_remove(plugin_path, card_widget):
                    def _remove():
                        if not self._dark_confirm(win, "Remove Plugin", f"Remove  {Path(plugin_path).name}?"):
                            return
                        try:
                            Path(plugin_path).unlink(missing_ok=True)
                            self._load_plugins()
                            _refresh()
                        except Exception as e:
                            self._dark_error(win, "Plugin Manager", f"Could not remove:\n{e}")
                    return _remove

                btn_frame = tk.Frame(card, bg=C["surface"], padx=8)
                btn_frame.pack(side="right", fill="y")
                tk.Button(btn_frame, text="Remove",
                          font=("Courier New", 8),
                          bg=C["surface2"], fg=C["error"],
                          relief="flat", padx=8, pady=4, cursor="hand2",
                          highlightthickness=1,
                          highlightbackground=C["border"],
                          activebackground=C["border"],
                          command=_make_remove(p["path"], card)).pack(pady=4)

        _refresh()

        # Footer
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")
        foot = tk.Frame(win, bg=C["surface2"], pady=8); foot.pack(fill="x")
        tk.Label(foot,
                 text="Each plugin is a .py file with a register(app) function.",
                 font=("Courier New", 7), fg=C["muted"],
                 bg=C["surface2"]).pack(side="left", padx=16)
        tk.Button(foot, text=" Reload All ",
                  font=("Courier New", 8),
                  bg=C["surface"], fg=C["accent2"],
                  relief="flat", padx=8, pady=3, cursor="hand2",
                  highlightthickness=1, highlightbackground=C["border2"],
                  command=lambda: [self._load_plugins(), _refresh()]).pack(side="right", padx=12)

        win.update_idletasks()
        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
        ww = win.winfo_reqwidth(); wh = win.winfo_reqheight()
        win.geometry(f"520x{min(600,max(480,wh))}+{(sw-520)//2}+{(sh-min(600,max(480,wh)))//2}")

    def _on_update_btn_click(self):
        """Update button clicked: offer to run update.sh or do a manual check."""
        if self._update_available_version:
            self._show_update_dialog(self._update_available_version)
        else:
            self._check_for_update_bg(manual=True)

    def _show_update_dialog(self, latest: str):
        """Dialog offering to run update.sh or visit the repo."""
        import subprocess as _sp, webbrowser as _wb
        win = tk.Toplevel(self.root)
        win.title("Update Available")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        win.attributes("-topmost", True)

        hdr = tk.Frame(win, bg=C["surface"]); hdr.pack(fill="x")
        tk.Label(hdr, text="⬆  Update Available",
                 font=("Courier New", 12, "bold"),
                 fg=C["warning"], bg=C["surface"],
                 padx=20, pady=12).pack(side="left")
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=C["bg"], padx=24, pady=16); body.pack(fill="x")
        tk.Label(body, text=f"TTS Voices  {latest}  is available.",
                 font=("Courier New", 11, "bold"),
                 fg=C["text"], bg=C["bg"]).pack(anchor="w")
        tk.Label(body, text=f"You are running  {__version__}",
                 font=("Courier New", 9), fg=C["muted"], bg=C["bg"]).pack(anchor="w", pady=(2,12))
        tk.Label(body, text="Run update.sh to install, or visit GitHub for release notes.",
                 font=("Courier New", 9), fg=C["text2"], bg=C["bg"], wraplength=380).pack(anchor="w")

        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")
        foot = tk.Frame(win, bg=C["surface2"], pady=10); foot.pack(fill="x")

        def _run_update():
            win.destroy()
            script = str(Path(_APP_DIR) / "update.sh")
            if Path(script).exists():
                try:
                    _sp.Popen(["bash", script], cwd=_APP_DIR)
                    self._set_status("Updating…", C["warning"])
                except Exception as e:
                    self._dark_error(self.root, "Update", f"Could not run update.sh:\n{e}")
            else:
                self._dark_error(self.root, "Update", "update.sh not found.\nDownload from GitHub.")

        def _open_github():
            win.destroy()
            try: _wb.open("https://github.com/jspgamer0503-coder/TTSVoices")
            except Exception: pass

        tk.Button(foot, text="  Later  ",
                  font=("Courier New", 9), bg=C["surface"], fg=C["muted"],
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  command=win.destroy).pack(side="right", padx=(4,12))
        tk.Button(foot, text="  View on GitHub  ",
                  font=("Courier New", 9), bg=C["surface2"], fg=C["accent2"],
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  highlightthickness=1, highlightbackground=C["border2"],
                  command=_open_github).pack(side="right", padx=4)
        tk.Button(foot, text="  Run update.sh  ",
                  font=("Courier New", 9, "bold"), bg=C["warning"], fg="black",
                  relief="flat", padx=12, pady=6, cursor="hand2",
                  activebackground="#e09000",
                  command=_run_update).pack(side="right", padx=4)

        win.update_idletasks()
        w = win.winfo_reqwidth(); h = win.winfo_reqheight()
        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
        win.geometry(f"{max(w,420)}x{h}+{(sw-max(w,420))//2}+{(sh-h)//2}")
        win.grab_set()

    def _check_deps_outdated(self):
        """Return list of outdated pip packages using the venv pip.
        Called from the Settings window's dep checker tab.
        Returns list of dicts: [{name, version, latest_version}, ...]
        """
        import subprocess as _sp, json as _json, sys as _sys
        # Always use the venv pip, never the system pip
        venv_pip = str(Path(_sys.prefix) / "bin" / "pip")
        if not Path(venv_pip).exists():
            venv_pip = "pip"  # fallback if not in a venv
        try:
            r = _sp.run(
                [venv_pip, "list", "--outdated", "--format=json"],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0 and r.stdout.strip():
                return _json.loads(r.stdout)
            return []
        except Exception as e:
            if bug_tracker:
                bug_tracker.warning(f"Dep check failed: {e}")
            return []


    # ══════════════════════════════════════════════════════════════════════════
    #  PLUGIN SYSTEM
    # ══════════════════════════════════════════════════════════════════════════
    _loaded_plugins: list = []   # list of {"name": str, "module": module, "path": str}

    def _load_plugins(self):
        """
        Scan PLUGINS_DIR (~/.ttsvoices/plugins/) for .py files.
        For each, import it and call register(app) if it exists.
        Errors are logged but never crash the app.
        """
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        self._loaded_plugins = []
        import importlib.util as _ilu
        for py_file in sorted(PLUGINS_DIR.glob("*.py")):
            name = py_file.stem
            try:
                spec = _ilu.spec_from_file_location(f"ttsvoices_plugin_{name}", py_file)
                mod  = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(self)
                self._loaded_plugins.append({
                    "name":   name,
                    "module": mod,
                    "path":   str(py_file),
                    "has_register": hasattr(mod, "register"),
                })
                if bug_tracker:
                    bug_tracker.info(f"Plugin loaded: {name}")
            except Exception as e:
                if bug_tracker:
                    bug_tracker.error(f"Plugin failed to load: {name}: {e}", exc_info=True)
                self._loaded_plugins.append({
                    "name":   name,
                    "module": None,
                    "path":   str(py_file),
                    "error":  str(e),
                })

    # ── Plugin API (callable from plugin register() functions) ────────────────

    def add_nav_button(self, label: str, command, accent: bool = False):
        """Plugin API: Add a button to the header nav bar."""
        try:
            nav = self._nav_frame
            btn = self._make_nav_btn(nav, label, command)
            if accent:
                btn.set_colors(C["accent"], C["accent_dim"])
                btn._is_accent = True
        except Exception as e:
            if bug_tracker:
                bug_tracker.warning(f"add_nav_button failed: {e}")

    def on_speak_start(self, callback):
        """Plugin API: Register a callback fired when speech starts. callback()"""
        if not hasattr(self, "_plugin_speak_start_cbs"):
            self._plugin_speak_start_cbs = []
        self._plugin_speak_start_cbs.append(callback)

    def on_speak_stop(self, callback):
        """Plugin API: Register a callback fired when speech stops. callback()"""
        if not hasattr(self, "_plugin_speak_stop_cbs"):
            self._plugin_speak_stop_cbs = []
        self._plugin_speak_stop_cbs.append(callback)

    def get_current_text(self) -> str:
        """Plugin API: Return the full text currently in the textarea."""
        try:
            return self._textarea.get("1.0", "end-1c")
        except Exception:
            return ""

    def set_status(self, text: str, color: str = ""):
        """Plugin API: Set the status pill text. color is an optional hex string."""
        self._set_status(text, color or C["success"])

    def _fire_plugin_cbs(self, attr: str):
        """Internal: fire all registered plugin callbacks for a given event."""
        for cb in getattr(self, attr, []):
            try:
                cb()
            except Exception as e:
                if bug_tracker:
                    bug_tracker.warning(f"Plugin callback error: {e}")

    def _on_cfg_change(self, *_):
        """Speed or pitch changed — save config and re-synthesize from current chunk if speaking.

        Kokoro bakes speed/pitch into the WAV at synthesis time, so there is
        no way to apply them to already-generated audio.  We stop and restart
        from the current chunk so the new value takes effect immediately.
        Volume is handled separately by _on_volume_change and never reaches here.
        """
        self.cfg["speed"] = round(self.speed_var.get(), 2)
        self.cfg["pitch"] = round(self.pitch_var.get(), 2)

        if hasattr(self, "_cfg_save_after_id") and self._cfg_save_after_id:
            try: self.root.after_cancel(self._cfg_save_after_id)
            except Exception: pass
        self._cfg_save_after_id = self.root.after(800, lambda: save_config(self.cfg))

    def _save_cfg_now(self): self._on_cfg_change(); save_config(self.cfg)

    # ── Resource-adaptive UI ───────────────────────────────────────────────────
    _LAST_RES_LEVEL = None   # class-level sentinel so first tick always fires

    def _on_resources(self, snap: dict):
        """Called by ResourceMonitor on the main thread every ~3 s.

        Writes live CPU / RAM figures into the subtitle label next to the logo.
        Colour of the subtitle shifts: normal accent2 → amber at medium load
        → red at high load, so the user gets a visual cue without any extra widget.
        """
        cpu   = snap["cpu"]
        ram   = snap["ram"]
        level = snap["level"]

        # Always update the subtitle text so numbers stay current
        gen_part = ""
        try:
            g = getattr(self, "_generation", 0)
            if g:
                gen_part = f"  ·  ▶{g}"
        except Exception:
            pass
        try:
            cpu_color = "#f87171" if cpu >= 75 else ("#fbbf24" if cpu >= 40 else C["accent2"])
            lbl_color = "#f87171" if level == "high" else (
                        "#fbbf24" if level == "medium" else C["accent2"])
            self._subtitle_lbl.configure(
                text=f"v{__version__}  ·  Unlimited Audio Generation{gen_part}"
                     f"  ·  CPU {cpu:.0f}%  RAM {ram:.0f}%",
                fg=lbl_color)
        except Exception:
            pass

        # Reconfigure animations only on level transition
        if level == TTSVoicesApp._LAST_RES_LEVEL:
            return
        TTSVoicesApp._LAST_RES_LEVEL = level

        if level == "low":
            PillToggle.STEPS = 12
            PillToggle.DELAY = 10
            try: self._engine_frame.pack(fill="x", padx=8, pady=4)
            except Exception: pass
        elif level == "medium":
            PillToggle.STEPS = 6
            PillToggle.DELAY = 10
            try: self._engine_frame.pack(fill="x", padx=8, pady=4)
            except Exception: pass
        else:  # high
            PillToggle.STEPS = 1
            PillToggle.DELAY = 10
            try: self._engine_frame.pack_forget()
            except Exception: pass

    def _on_close(self):
        """Shut down completely — kill all child processes so the terminal returns."""
        self._stop_flag.set()
        _resource_monitor.stop()

        # 1. Stop audio playback subprocess cleanly
        try:
            audio_handler.stop_playback()
        except Exception:
            pass

        # 2. Force-kill any lingering audio player process
        try:
            import audio_handler as _ah
            if _ah._current_proc and _ah._current_proc.poll() is None:
                try:
                    _ah._current_proc.kill()
                except Exception:
                    pass
        except Exception:
            pass

        # 3. Kill the entire process group so any pip installs, pw-play,
        #    ffmpeg, or vosk threads spawned as children are all reaped.
        try:
            import os as _os, signal as _sig
            pgid = _os.getpgid(_os.getpid())
            try:
                _os.killpg(pgid, _sig.SIGTERM)
            except Exception:
                pass
        except Exception:
            pass

        save_config(self.cfg)

        # 4. Destroy the Tk window
        try:
            self.root.destroy()
        except Exception:
            pass

        # 5. os._exit() is an immediate unconditional exit that bypasses all
        #    Python cleanup (atexit, __del__, gc) and daemon thread joins.
        #    This is intentional — it guarantees the terminal returns control
        #    even if Vosk's C++ threads or a pip download are still running.
        import os as _os
        _os._exit(0)

    def run(self): self.root.mainloop()


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Catch any unhandled non-Tk exception and log it before crash."""
    import traceback as _tb
    tb_str = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
    if bug_tracker is not None:
        try:
            bug_tracker.critical(f"Unhandled crash: {exc_value}", tb_str)
        except Exception:
            pass
    else:
        # bug_tracker not yet loaded — write directly to stderr
        import sys as _sys
        print(f"CRASH (bug_tracker not loaded): {exc_value}\n{tb_str}",
              file=_sys.stderr)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _ensure_single_instance():
    """
    Prevent duplicate app windows when the icon is double-clicked.
    Uses an abstract Unix socket as a lock — automatically released
    when the process exits, no stale lock files left behind.
    Returns the socket (must stay alive) or None if already running.
    """
    import socket as _sock
    lock = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
    try:
        # Abstract namespace socket — starts with null byte, no file on disk
        lock.bind("\0ttsvoices_instance_lock")
        return lock   # We are the first instance — keep socket alive
    except OSError:
        # Another instance is already running — focus it via wmctrl if available
        import subprocess as _sp
        try:
            _sp.run(["wmctrl", "-a", "TTS Voices"], capture_output=True)
        except Exception:
            pass
        return None   # Signal caller to exit


if __name__ == "__main__":
    # ── First-run dependency installer (only runs when packages are missing) ──
    try:
        import dep_installer
        dep_installer.ensure_deps()
    except Exception:
        pass  # Never block startup

    # ── Wire Ctrl+C so the terminal returns immediately ──────────────────────
    import signal as _signal
    def _sigint_handler(sig, frame):
        """Ctrl+C: save config and hard-exit so the terminal prompt returns."""
        try:
            save_config(load_config())
        except Exception:
            pass
        import os as _os
        _os._exit(0)
    _signal.signal(_signal.SIGINT, _sigint_handler)

    sys.excepthook = _global_exception_handler
    _lock = _ensure_single_instance()
    if _lock is None:
        sys.exit(0)   # Second instance — quit silently, first instance focused
    TTSVoicesApp().run()
