#!/usr/bin/env python3
"""TTS Voices 2.5.1 – Unlimited Text-to-Speech Engine for Linux

Maintained by the opencode AI assistant under the direction of the project
owner (overseer). See README.md "Development & Maintenance" for full
attribution, or CHANGELOG.md for the development history.
"""
import os, sys, json, threading, queue, time, re, tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# ── Semantic versioning ────────────────────────────────────────────────────
__version__   = "2.5.1"
VERSION_TUPLE = (2, 5, 1)
VERSION_DATE  = "2026-06-14"
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


def _set_window_zoomed(win):
    """Maximise a Tk window cross-platform.

    Linux (X11/Wayland): `win.attributes("-zoomed", True)` is supported on
    most WMs but not all — some reject it silently.
    Windows / macOS: `win.state("zoomed")` is the supported path.

    We try both. Whichever succeeds first sticks. A future improvement
    could detect Wayland vs X11 and pick the right call up front.
    """
    try:
        win.attributes("-zoomed", True)
    except tk.TclError:
        pass
    try:
        win.state("zoomed")
    except tk.TclError:
        pass


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
# Plugins dir: owner-only (0700) so other local users cannot drop malicious
# .py files that would execute with the victim's privileges on next launch.
PLUGINS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
try:
    import os as _os; _os.chmod(PLUGINS_DIR, 0o700)
except Exception:
    pass

DEFAULT_CONFIG = {
    "speed": 1.3, "pitch": 1.0, "volume": 63, "voice_idx": 0,
    "theme": "dark", "provider": "CPU", "highlight_offset": 150,
    "auto_update_check": True,
    "cloud_tts_enabled": False,  # local-first: Edge TTS (Cloud) hidden by default
    "startup_maximised": True,  # maximise window on launch
}

# ── Engine name constants (mirrors voices.py — defined here so UI builds
#    before voices module is imported on the background thread) ──────────────
_ENGINE_KOKORO   = "Kokoro ONNX"
_ENGINE_ESPEAK   = "espeak-ng"
_ENGINE_EDGE_TTS = "Edge TTS (Cloud)"
_MODELS_DIR      = Path.home() / ".ttsvoices" / "models"


# ══════════════════════════════════════════════════════════════════════════════
#  RESOURCE MONITOR
# ══════════════════════════════════════════════════════════════════════════════
class ResourceMonitor:
    """Background system resource poller that drives adaptive UI behaviour.

    Architecture
    ────────────
    A daemon thread calls psutil every POLL_INTERVAL_S seconds and stores the
    latest CPU % (overall + per-core), virtual-memory %, disk %, and network
    I/O deltas in thread-safe attributes (plain float/int assignments are
    atomic under CPython's GIL).

    The main thread's _tick() method is registered with root.after() at
    TICK_MS cadence. Each tick reads those attributes and calls any registered
    callback with a dict containing all metrics.

    Thresholds
    ──────────
    CPU:   LOW < 40 %   MEDIUM 40–75 %   HIGH > 75 %
    RAM:   LOW < 60 %   MEDIUM 60–85 %   HIGH > 85 %

    The combined pressure level is max(cpu_level, ram_level) and is exposed as
    snapshot["level"] ∈ {"low", "medium", "high"}.

    Adaptive UI decisions made by TTSVoicesApp._on_resources():
      low    → full animations enabled, right panel fully expanded
      medium → animations throttled (GlowButton hover still works, no extras)
      high   → animations suspended, right panel hints compacted, warning shown

    Display format (subtitle label under the logo):
      v2.5.1 · ▶0 · CPU ▁▃▅▂▁▃▅▂ 23% · RAM 4.2/7.4G · DSK 73% · ▲12M ▼3M
            ▲▲    ▲▲▲▲▲▲▲▲▲▲▲▲▲  ▲▲▲ ▲▲▲▲▲▲▲▲ ▲▲▲ ▲▲▲  ▲▲▲▲▲▲▲▲

    If psutil is not installed the monitor falls back to /proc parsing —
    works on any Linux kernel, zero additional dependencies.
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
        self._ram_used   = 0     # bytes used
        self._ram_total  = 0     # bytes total
        self._per_cpu    = []    # list[float], one per logical core
        self._disk_pct   = 0.0
        self._net_up     = 0.0   # bytes/sec, delta from last poll
        self._net_down   = 0.0   # bytes/sec
        self._cbs        = []
        self._root       = None
        self._after      = None
        self._prev_idle  = [0] * 64  # per-core idle counters (max 64 cores)
        self._prev_total = [0] * 64
        self._prev_net   = (0, 0)   # (bytes_sent, bytes_recv)
        self._prev_time  = None

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
        """Daemon thread: poll all metrics every POLL_INTERVAL_S seconds.

        Uses psutil if available, otherwise reads /proc/stat, /proc/meminfo,
        and /proc/net/dev directly — zero additional dependencies.
        """
        # psutil warmup (establishes baseline for non-blocking calls)
        if not self._use_proc:
            try:
                self._ps.cpu_percent(interval=0.1, percpu=True)
            except Exception: pass

        while True:
            t0 = time.monotonic()
            try:
                if self._use_proc:
                    per_cpu = self._read_proc_per_cpu()
                    ram_pct, ram_used, ram_total = self._read_proc_ram_full()
                    disk_pct = self._read_proc_disk()
                    net_up, net_down = self._read_proc_net_delta()
                else:
                    per_cpu = self._ps.cpu_percent(interval=None, percpu=True)
                    vm = self._ps.virtual_memory()
                    ram_pct   = vm.percent
                    ram_used  = vm.used
                    ram_total = vm.total
                    disk_pct  = self._ps.disk_usage('/').percent
                    net_up, net_down = self._read_psutil_net_delta()
                self._per_cpu   = per_cpu
                self._cpu       = sum(per_cpu) / len(per_cpu) if per_cpu else 0.0
                self._ram       = ram_pct
                self._ram_used  = ram_used
                self._ram_total = ram_total
                self._disk_pct  = disk_pct
                self._net_up    = net_up
                self._net_down  = net_down
            except Exception:
                pass
            # Sleep the rest of the poll interval (capped to allow responsive stop)
            elapsed = time.monotonic() - t0
            time.sleep(max(0.1, self.POLL_INTERVAL_S - elapsed))

    def _read_proc_per_cpu(self):
        """Read per-CPU % from /proc/stat (one 'cpu0', 'cpu1', … line each)."""
        try:
            results = []
            with open("/proc/stat") as f:
                for line in f:
                    if not line.startswith("cpu"):
                        break
                    parts = line.split()
                    if parts[0] == "cpu":  # aggregate line, skip
                        continue
                    idx = int(parts[0][3:])  # "cpu0" -> 0
                    vals = [int(x) for x in parts[1:]]
                    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                    total = sum(vals)
                    d_total = total - self._prev_total[idx]
                    d_idle  = idle  - self._prev_idle[idx]
                    self._prev_total[idx] = total
                    self._prev_idle[idx]  = idle
                    if d_total == 0:
                        results.append(0.0)
                    else:
                        results.append(round(100.0 * (1.0 - d_idle / d_total), 1))
            return results
        except Exception:
            return self._per_cpu

    def _read_proc_cpu(self) -> float:
        """Compute aggregate CPU % from /proc/stat (kept for backward compat)."""
        try:
            with open("/proc/stat") as f:
                parts = f.readline().split()
            vals  = [int(x) for x in parts[1:]]
            idle  = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
            d_total = total - self._prev_total[0]
            d_idle  = idle  - self._prev_idle[0]
            self._prev_total[0] = total
            self._prev_idle[0]  = idle
            if d_total == 0:
                return self._cpu
            return round(100.0 * (1.0 - d_idle / d_total), 1)
        except Exception:
            return self._cpu

    def _read_proc_ram_full(self):
        """Return (percent, used_bytes, total_bytes) from /proc/meminfo."""
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":")
                    info[k.strip()] = int(v.split()[0])
            total_kb = info.get("MemTotal", 0)
            avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
            used_kb  = total_kb - avail_kb
            pct = round(100.0 * used_kb / total_kb, 1) if total_kb else 0.0
            return pct, used_kb * 1024, total_kb * 1024
        except Exception:
            return self._ram, self._ram_used, self._ram_total

    def _read_proc_ram(self) -> float:
        """Backward-compat wrapper — return RAM percent only."""
        pct, _, _ = self._read_proc_ram_full()
        return pct

    def _read_proc_disk(self) -> float:
        """Return root filesystem used % from os.statvfs (no shell out to df)."""
        try:
            st = os.statvfs('/')
            total = st.f_blocks * st.f_frsize
            free  = st.f_bavail * st.f_frsize
            if total == 0:
                return self._disk_pct
            return round(100.0 * (total - free) / total, 1)
        except Exception:
            return self._disk_pct

    def _read_proc_net_delta(self):
        """Return (bytes_sent_per_sec, bytes_recv_per_sec) from /proc/net/dev."""
        try:
            with open("/proc/net/dev") as f:
                lines = f.readlines()
            tx = rx = 0
            for line in lines[2:]:  # skip header
                parts = line.split()
                iface = parts[0].rstrip(":")
                if iface == "lo":  # skip loopback
                    continue
                tx += int(parts[9])   # bytes sent
                rx += int(parts[1])   # bytes received
            now = time.monotonic()
            if self._prev_time is None:
                self._prev_net = (tx, rx)
                self._prev_time = now
                return 0.0, 0.0
            dt = now - self._prev_time
            if dt <= 0:
                return 0.0, 0.0
            d_tx = tx - self._prev_net[0]
            d_rx = rx - self._prev_net[1]
            self._prev_net = (tx, rx)
            self._prev_time = now
            return max(0.0, d_tx / dt), max(0.0, d_rx / dt)
        except Exception:
            return self._net_up, self._net_down

    def _read_psutil_net_delta(self):
        """Return (bytes_sent_per_sec, bytes_recv_per_sec) from psutil."""
        try:
            now = time.monotonic()
            io = self._ps.net_io_counters()
            if self._prev_time is None:
                self._prev_net = (io.bytes_sent, io.bytes_recv)
                self._prev_time = now
                return 0.0, 0.0
            dt = now - self._prev_time
            if dt <= 0:
                return 0.0, 0.0
            d_tx = io.bytes_sent - self._prev_net[0]
            d_rx = io.bytes_recv - self._prev_net[1]
            self._prev_net = (io.bytes_sent, io.bytes_recv)
            self._prev_time = now
            return max(0.0, d_tx / dt), max(0.0, d_rx / dt)
        except Exception:
            return self._net_up, self._net_down

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
                "ram_used":  self._ram_used,
                "ram_total": self._ram_total,
                "per_cpu":   list(self._per_cpu),
                "disk":      self._disk_pct,
                "net_up":    self._net_up,
                "net_down":  self._net_down,
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
        "nav_hover":"#14142a","hover":"#14142a","textarea_bg":"#000000","textarea_fg":"#e2eaff",
        "scrollbar":"#0a0a10","cursor":"#00c8ff","sel_bg":"#0a2a6e","pill_bg":"#000000",
    },
    "light": {
        "label":"☀ Light",
        "bg":"#f0f4f8","surface":"#ffffff","surface2":"#e8edf4","border":"#c8d4e4",
        "border2":"#b0c0d8","accent":"#1a6cf5","accent2":"#0d5cd4","accent_dim":"#d6e4ff",
        "text":"#0d1526","text2":"#3a5070","muted":"#7a94b0","success":"#1a9e5a",
        "warning":"#c47a00","error":"#c0392b","speak_bg":"#1553d0","speak_hover":"#1d6aff",
        "stop_bg":"#c0392b","stop_hover":"#e74c3c","header_bg":"#1a2a45","nav_btn":"#dce6f5",
        "nav_hover":"#c8d8f0","hover":"#c8d8f0","textarea_bg":"#ffffff","textarea_fg":"#0d1526",
        "scrollbar":"#dce6f5","cursor":"#1a6cf5","sel_bg":"#c8d8f0","pill_bg":"#f0fff4",
    },
    "red": {
        "label":"🔴 Red",
        "bg":"#0f0608","surface":"#1a0a0e","surface2":"#240d14","border":"#3d1520",
        "border2":"#5c1f2e","accent":"#e53e3e","accent2":"#ff6b6b","accent_dim":"#7a1a1a",
        "text":"#f5dde0","text2":"#c49098","muted":"#7a4a50","success":"#00d97e",
        "warning":"#f59e0b","error":"#ff3333","speak_bg":"#c0392b","speak_hover":"#e53e3e",
        "stop_bg":"#3d1520","stop_hover":"#6b2030","header_bg":"#0a0406","nav_btn":"#1a0a0e",
        "nav_hover":"#2d0e16","hover":"#2d0e16","textarea_bg":"#1a0a0e","textarea_fg":"#f5dde0",
        "scrollbar":"#240d14","cursor":"#ff6b6b","sel_bg":"#5c1f2e","pill_bg":"#0f0608",
    },
    "blue": {
        "label":"🔵 Blue",
        "bg":"#040c1a","surface":"#071428","surface2":"#0a1c38","border":"#0e2d5c",
        "border2":"#133a78","accent":"#2980f5","accent2":"#5bb8ff","accent_dim":"#0d3080",
        "text":"#d8e8ff","text2":"#7aaad4","muted":"#3a6090","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#0d3d99","speak_hover":"#1d6aff",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020810","nav_btn":"#071428",
        "nav_hover":"#0e2d5c","hover":"#0e2d5c","textarea_bg":"#071428","textarea_fg":"#d8e8ff",
        "scrollbar":"#0a1c38","cursor":"#5bb8ff","sel_bg":"#0d3d99","pill_bg":"#020810",
    },
    "teal": {
        "label":"🩵 Teal",
        "bg":"#040f0f","surface":"#081a1a","surface2":"#0c2424","border":"#0e3838",
        "border2":"#155050","accent":"#00b8a9","accent2":"#00e5d4","accent_dim":"#006060",
        "text":"#d0f0ee","text2":"#70b8b0","muted":"#306860","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#007a70","speak_hover":"#00b8a9",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020a0a","nav_btn":"#081a1a",
        "nav_hover":"#0e3838","hover":"#0e3838","textarea_bg":"#081a1a","textarea_fg":"#d0f0ee",
        "scrollbar":"#0c2424","cursor":"#00e5d4","sel_bg":"#0e3838","pill_bg":"#020a0a",
    },
    "orange": {
        "label":"🟠 Orange",
        "bg":"#0f0900","surface":"#1a1000","surface2":"#261700","border":"#402800",
        "border2":"#5c3a00","accent":"#f57c00","accent2":"#ffaa33","accent_dim":"#7a3a00",
        "text":"#fff0d8","text2":"#c4a060","muted":"#806040","success":"#00d97e",
        "warning":"#ffcc00","error":"#ef4444","speak_bg":"#c06000","speak_hover":"#f57c00",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a0600","nav_btn":"#1a1000",
        "nav_hover":"#2d1e00","hover":"#2d1e00","textarea_bg":"#1a1000","textarea_fg":"#fff0d8",
        "scrollbar":"#261700","cursor":"#ffaa33","sel_bg":"#5c3a00","pill_bg":"#0a0600",
    },
    "purple": {
        "label":"🟣 Purple",
        "bg":"#0a0615","surface":"#110920","surface2":"#180d2e","border":"#2a1550",
        "border2":"#3d2070","accent":"#8b5cf6","accent2":"#c084fc","accent_dim":"#4a1fa0",
        "text":"#eddeff","text2":"#a880d0","muted":"#604888","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#5b21b6","speak_hover":"#7c3aed",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#07040f","nav_btn":"#110920",
        "nav_hover":"#2a1550","hover":"#2a1550","textarea_bg":"#110920","textarea_fg":"#eddeff",
        "scrollbar":"#180d2e","cursor":"#c084fc","sel_bg":"#3d2070","pill_bg":"#07040f",
    },
    "pink": {
        "label":"🩷 Pink",
        "bg":"#0f0610","surface":"#1a0c1c","surface2":"#241228","border":"#3d1545",
        "border2":"#5a2060","accent":"#ec4899","accent2":"#f9a8d4","accent_dim":"#831843",
        "text":"#ffe0f0","text2":"#c480a0","muted":"#804868","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#9d174d","speak_hover":"#be185d",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a040c","nav_btn":"#1a0c1c",
        "nav_hover":"#3d1545","hover":"#3d1545","textarea_bg":"#1a0c1c","textarea_fg":"#ffe0f0",
        "scrollbar":"#241228","cursor":"#f9a8d4","sel_bg":"#5a2060","pill_bg":"#0a040c",
    },
    "golden": {
        "label":"✨ Golden",
        "bg":"#000000","surface":"#110a00","surface2":"#1a1000","border":"#5a3a00",
        "border2":"#7a5200","accent":"#ffb300","accent2":"#ffd700","accent_dim":"#3d2800",
        "text":"#fff5cc","text2":"#ffcc55","muted":"#907040","success":"#00d97e",
        "warning":"#ffcc00","error":"#ef4444","speak_bg":"#ffaa00","speak_hover":"#ffc533",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a0600","nav_btn":"#1a1000",
        "nav_hover":"#2a1a00","hover":"#2a1a00","textarea_bg":"#0d0800","textarea_fg":"#fff5cc",
        "scrollbar":"#1a1000","cursor":"#ffd700","sel_bg":"#3d2800","pill_bg":"#0a0600",
    },
    "green": {
        "label":"🟢 Green",
        "bg":"#040f06","surface":"#081a0c","surface2":"#0c2414","border":"#0e3818",
        "border2":"#155024","accent":"#22c55e","accent2":"#4ade80","accent_dim":"#0a5c28",
        "text":"#d8ffe0","text2":"#70b880","muted":"#306840","success":"#4ade80",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#166534","speak_hover":"#16a34a",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020a04","nav_btn":"#081a0c",
        "nav_hover":"#0e3818","hover":"#0e3818","textarea_bg":"#081a0c","textarea_fg":"#d8ffe0",
        "scrollbar":"#0c2414","cursor":"#4ade80","sel_bg":"#0e3818","pill_bg":"#020a04",
    },
    "studio": {
        "label":"🎨 Studio",
        "bg":"#04060c","surface":"#080e1c","surface2":"#0d1428","border":"#00c8b4",
        "border2":"#0a8f82","accent":"#00c8b4","accent2":"#29dfd0","accent_dim":"#0a4a44",
        "text":"#e8eef5","text2":"#8fa8c4","muted":"#4a5a70","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#007a6e","speak_hover":"#00c8b4",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#020408","nav_btn":"#080e1c",
        "nav_hover":"#0d1428","hover":"#0d1428","textarea_bg":"#020609","textarea_fg":"#e8eef5",
        "scrollbar":"#0d1428","cursor":"#29dfd0","sel_bg":"#0a4a44","pill_bg":"#020408",
    },
    "midnight": {
        "label":"🌙 Midnight",
        "bg":"#000000","surface":"#050510","surface2":"#0a0a1a","border":"#1a1a3a",
        "border2":"#252550","accent":"#6c63ff","accent2":"#a78bfa","accent_dim":"#2d2a6e",
        "text":"#e8e0ff","text2":"#9d8fff","muted":"#4a4580","success":"#00d97e",
        "warning":"#f59e0b","error":"#ef4444","speak_bg":"#4c3fcf","speak_hover":"#6c63ff",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#000000","nav_btn":"#050510",
        "nav_hover":"#0f0f25","hover":"#0f0f25","textarea_bg":"#020208","textarea_fg":"#e8e0ff",
        "scrollbar":"#0a0a1a","cursor":"#a78bfa","sel_bg":"#1a1a3a","pill_bg":"#000000",
    },
    "crimson": {
        "label":"🩸 Crimson",
        "bg":"#000000","surface":"#100008","surface2":"#180010","border":"#3a0018",
        "border2":"#580025","accent":"#dc143c","accent2":"#ff4d70","accent_dim":"#5a0018",
        "text":"#ffe0e8","text2":"#d080a0","muted":"#6a3050","success":"#00d97e",
        "warning":"#f59e0b","error":"#ff3333","speak_bg":"#b01030","speak_hover":"#dc143c",
        "stop_bg":"#3a0018","stop_hover":"#5a0025","header_bg":"#000000","nav_btn":"#100008",
        "nav_hover":"#200010","hover":"#200010","textarea_bg":"#080005","textarea_fg":"#ffe0e8",
        "scrollbar":"#180010","cursor":"#ff4d70","sel_bg":"#3a0018","pill_bg":"#000000",
    },
    "yellow": {
        "label":"🟡 Yellow",
        "bg":"#000000","surface":"#111100","surface2":"#1c1c00","border":"#4a4800",
        "border2":"#6a6600","accent":"#ffee00","accent2":"#ffff55","accent_dim":"#3a3400",
        "text":"#fffce0","text2":"#ffe066","muted":"#888840","success":"#00d97e",
        "warning":"#ffcc00","error":"#ef4444","speak_bg":"#ffe000","speak_hover":"#ffff44",
        "stop_bg":"#7a1515","stop_hover":"#c0392b","header_bg":"#0a0a00","nav_btn":"#1a1a00",
        "nav_hover":"#2a2a00","hover":"#2a2a00","textarea_bg":"#0d0d00","textarea_fg":"#fffce0",
        "scrollbar":"#1c1c00","cursor":"#ffff55","sel_bg":"#3a3400","pill_bg":"#0a0a00",
    },
}

C = dict(THEMES["dark"])   # live colour dict – mutated on theme switch

FONT_LABEL = ("Courier New", 8, "bold")
FONT_BTN   = ("Courier New", 9, "bold")

def load_config():
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as _f:
                data = json.load(_f)
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
    try:
        with open(CONFIG_FILE, "w") as _f:
            json.dump(cfg, _f, indent=2)
    except Exception as e:
        # bug_tracker is None during early startup; guard so a config save
        # failure (e.g. disk full) can't crash the Tk var trace callback.
        if bug_tracker is not None:
            bug_tracker.warning(f"Config save: {e}")


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


class Tooltip:
    """
    Lightweight dark-themed tooltip for any Tkinter widget.

    Usage:
        Tooltip(widget, "What this button does")
        # or via the helper:
        attach_tooltip(widget, "What this button does")

    Features:
    - 450 ms hover delay (no flicker on accidental mouse-overs)
    - Follows the cursor and repositions away from screen edges
    - Matches the app's dark theme (reads from global C palette)
    - Destroyed automatically when the widget is destroyed
    - Safe to call on GlowButton, tk.Button, tk.Canvas, ttk.*, etc.
    - Cancels correctly if the mouse leaves before the delay fires
    """

    _DELAY_MS   = 450     # ms before tooltip appears
    _PAD_X      = 10      # internal horizontal padding
    _PAD_Y      = 6       # internal vertical padding
    _OFFSET_Y   = 22      # pixels below cursor to appear

    def __init__(self, widget, text: str):
        self._widget  = widget
        self._text    = text
        self._tip_win = None
        self._after_id = None

        widget.bind("<Enter>",   self._on_enter,   add="+")
        widget.bind("<Leave>",   self._on_leave,   add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

        # For compound widgets (GlowButton has an inner Label), also bind children
        for child in widget.winfo_children():
            child.bind("<Enter>",   self._on_enter,   add="+")
            child.bind("<Leave>",   self._on_leave,   add="+")
            child.bind("<ButtonPress>", self._on_leave, add="+")

    # ── Internal ─────────────────────────────────────────────────────────────

    def _on_enter(self, event):
        self._cancel()
        self._after_id = self._widget.after(self._DELAY_MS, lambda: self._show(event))

    def _on_leave(self, _event=None):
        self._cancel()
        self._hide()

    def _on_destroy(self, _event=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self, event):
        self._hide()  # clear any stale window first
        if not self._text:
            return

        # ── Build tooltip window ──────────────────────────────────────────
        self._tip_win = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)   # no title bar / decorations
        tw.wm_attributes("-topmost", True)
        tw.withdraw()                  # keep hidden until positioned

        bg  = C.get("surface2",  "#0d1a2e")
        fg  = C.get("text2",     "#c8d8f0")
        bdr = C.get("accent2",   "#00c8ff")

        frame = tk.Frame(tw, bg=bdr, bd=1)
        frame.pack()
        inner = tk.Frame(frame, bg=bg, padx=self._PAD_X, pady=self._PAD_Y)
        inner.pack()
        tk.Label(
            inner,
            text=self._text,
            font=("Courier New", 8),
            fg=fg,
            bg=bg,
            justify="left",
            wraplength=260,
        ).pack()

        tw.update_idletasks()
        tw_w = tw.winfo_reqwidth()
        tw_h = tw.winfo_reqheight()

        # ── Position: below cursor, nudge away from screen edges ─────────
        sx, sy = event.x_root, event.y_root
        sw = self._widget.winfo_screenwidth()
        sh = self._widget.winfo_screenheight()

        x = sx + 12
        y = sy + self._OFFSET_Y

        if x + tw_w > sw - 4:
            x = sx - tw_w - 8
        if y + tw_h > sh - 4:
            y = sy - tw_h - 8

        tw.wm_geometry(f"+{max(0, x)}+{max(0, y)}")
        tw.deiconify()                 # show at final position

    def _hide(self):
        if self._tip_win is not None:
            try:
                self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None

    # ── Public ───────────────────────────────────────────────────────────────
    def update_text(self, text: str):
        """Change the tooltip text (e.g. when button state changes)."""
        self._text = text


def attach_tooltip(widget, text: str) -> "Tooltip":
    """
    Attach a hover tooltip to any widget.  Returns the Tooltip so callers
    can call .update_text() later if the label needs to change dynamically
    (e.g. CPU ↔ GPU button).
    """
    return Tooltip(widget, text)


class GlowButton(tk.Frame):
    def __init__(self, parent, text, command=None, fg="white",
                 normal_bg=None, hover_bg=None, font=FONT_BTN,
                 tooltip=None, **kw):
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
        if tooltip:
            self._tooltip = Tooltip(self, tooltip)
        else:
            self._tooltip = None

    def _enter(self,_=None): self.configure(bg=self._hbg); self._lbl.configure(bg=self._hbg)
    def _leave(self,_=None): self.configure(bg=self._nbg); self._lbl.configure(bg=self._nbg)
    def _click(self,_=None):
        if self._cmd: self._cmd()
    def set_text(self, t):   self._lbl.configure(text=t)
    def set_colors(self, n, h=None):
        self._nbg=n; self._hbg=h or n
        self.configure(bg=n); self._lbl.configure(bg=n)

class NumericControl(tk.Frame):
    def __init__(self, parent, label, var, mn, mx, step, fmt="{:.1f}", tooltip=None, **kw):
        super().__init__(parent, bg=C["surface"], **kw)
        self._var=var; self._mn=mn; self._mx=mx; self._step=step; self._fmt=fmt
        self._dvar = tk.StringVar(value=fmt.format(var.get()))
        lbl = tk.Label(self, text=label, font=FONT_LABEL, fg=C["text2"], bg=C["surface"],
                 width=9, anchor="w")
        lbl.pack(side="left")
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
        if tooltip:
            for w in (self, lbl, self._dec_btn, self._inc_btn):
                attach_tooltip(w, tooltip)
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
            has_sr      = importlib.util.find_spec("speech_recognition") is not None

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
                        # tiny may also be missing — fail fast instead of trying
                        # a network download that wasn't asked for.
                        model_cached = (
                            _os.path.exists(_os.path.join(
                                cache, "Systran--faster-whisper-tiny".replace("/", "--"))) or
                            _os.path.exists(_os.path.expanduser("~/.cache/faster_whisper/tiny"))
                        )
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
                    import wave as _wave
                    with _wave.open(tmp, "rb") as _wf:
                        _sample_rate = _wf.getframerate()
                        _total_frames = _wf.getnframes()
                        # Re-initialise recogniser with actual sample rate from header
                        if _sample_rate != 16000:
                            rec = KaldiRecognizer(vosk_model, _sample_rate)
                            rec.SetWords(True)
                        while True:
                            if self._cancel_requested:
                                break
                            # 4000 frames ≈ 0.25 s @ 16 kHz — fine-grained progress
                            data = _wf.readframes(4000)
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
                        duration = src.duration if hasattr(src, "duration") else None
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
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
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
        self._session_start_chunk    = 0    # first chunk of this speak session (for save point)
        # SavePointManager — lazy: may still be None during early startup if import is slow.
        # _ensure_save_mgr() creates it on first use.
        self._save_mgr = None
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
            icon_path = Path(_APP_DIR) / "ttsvoices_icon.png"
            if icon_path.is_file():
                self._icon_img = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass

        # ── Maximise on launch (cross-platform) ────────────────────────
        # Default behaviour is to maximise when the user has asked for it
        # in config. On Linux use -zoomed attribute; on Windows/macOS
        # use state("zoomed"). Try both so a fallback exists on WM that
        # rejects either. The user can override with a CLI flag or
        # shift-click in the future.
        _startup_maximised = self.cfg.get("startup_maximised", True)
        if _startup_maximised:
            _set_window_zoomed(self.root)

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

        # Poll every 80 ms until engines are ready (or until 30 s timeout),
        # then finish init. The timeout prevents an indefinite "LOADING…"
        # hang if voices.py fails to import (e.g. broken onnxruntime install).
        self._engine_poll_t0 = _STARTUP_T0
        self.root.after(80, self._poll_engines_ready)

    def _poll_engines_ready(self):
        """Called every 80 ms until _engines_ready is set, then completes init."""
        if not _engines_ready.is_set():
            if time.monotonic() - self._engine_poll_t0 > 30.0:
                # 30 s is plenty for Kokoro to load on a cold start. If we
                # get here the engine thread probably crashed silently —
                # surface a critical bug and let the user see the import
                # error rather than staring at a stuck "LOADING…" pill.
                err = ""
                try:
                    import traceback
                    err = traceback.format_exc(limit=1)
                except Exception:
                    pass
                if bug_tracker:
                    bug_tracker.critical(f"Engine load timed out after 30s. {err}")
                _engines_ready.set()
                self._finish_init()
                return
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
                      "TensorrtExecutionProvider", "DmlExecutionProvider"):
                if p in avail: return p
            return None

        best_gpu = _best_gpu()
        if saved_prov == "CPU":
            voices.set_provider("CPUExecutionProvider")
        elif saved_prov in ("GPU", "CUDA", "CUDA (NVIDIA)") and "CUDAExecutionProvider" in avail:
            voices.set_provider("CUDAExecutionProvider")
        elif saved_prov in ("ROCm (AMD)",) and "ROCMExecutionProvider" in avail:
            voices.set_provider("ROCMExecutionProvider")
        elif saved_prov in ("OpenVINO", "Intel GPU (OpenVINO)"):
            # OpenVINO is intentionally not auto-selected. If the user had
            # this in their saved config from a previous version, downgrade
            # to the best available GPU/CPU and surface a one-time notice.
            self._openvino_downgrade_notice()
            if best_gpu:
                voices.set_provider(best_gpu)
            else:
                voices.set_provider("CPUExecutionProvider")
        elif best_gpu:
            voices.set_provider(best_gpu)
        else:
            voices.set_provider("CPUExecutionProvider")

        # One-time warning if OpenVINO is installed but will not be used.
        # OpenVINO EP cannot run Kokoro's STFT node (dynamic rank op) and
        # trying it produces an 8+ s freeze + a noisy stack trace. We
        # therefore skip it in auto-detection. Surfacing this in the UI
        # saves the user from wondering "why doesn't the GPU button say
        # OpenVINO when onnxruntime clearly has it?".
        if ("OpenVINOExecutionProvider" in avail
            and "OpenVINOExecutionProvider" in voices._KOKORO_INCOMPATIBLE_PROVIDERS
            and not self.cfg.get("_openvino_notice_shown", False)):
            self.cfg["_openvino_notice_shown"] = True
            save_config(self.cfg)
            self.root.after(
                1500,
                lambda: self._set_status(
                    "OpenVINO EP skipped (incompatible with Kokoro's STFT) — using CPU",
                    C["warning"]
                )
            )

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

        # One-shot hardware optimisation (runs silently unless HW changed)
        self._optimize_for_hardware(silent=True)
        save_config(self.cfg)



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
        self._local_badge = tk.Label(tf, text="🛡 LOCAL-FIRST",
                 font=("Courier New", 7, "bold"),
                 fg=C["success"], bg=C["header_bg"])
        self._local_badge.pack(anchor="w")
        self._subtitle_lbl = tk.Label(tf,
                 text=f"v{__version__}  ·  Unlimited Audio Generation  ·  CPU 0%  RAM 0%",
                 font=("Courier New", 7, "bold"),
                 fg=C["accent2"], bg=C["header_bg"])
        self._subtitle_lbl.pack(anchor="w")

        nav = tk.Frame(self._hdr, bg=C["header_bg"])
        nav.pack(side="right", padx=10, pady=6)
        self._nav_frame = nav   # exposed for plugin add_nav_button() API

        self._nav_btns = []
        self._make_nav_btn(nav, "⚙ Settings",      self._open_settings,
                           tooltip="Open settings (fonts, audio device, themes…)")
        self._make_nav_btn(nav, "◑ Theme",           self._open_theme_picker,
                           tooltip="Switch colour theme")
        self._gpu_btn = self._make_nav_btn(nav, "⚡ CPU", self._toggle_gpu, accent=True,
                           tooltip="Toggle CPU / GPU (ONNX provider) for Kokoro synthesis")
        self._make_nav_btn(nav, "🎙 Audio→Text",     self._open_audio_to_text,
                           tooltip="Transcribe audio or microphone input to text (Whisper / Vosk / Google STT)")
        self._make_nav_btn(nav, "📚 Voice Library",  self._open_voice_library,
                           tooltip="Download and manage TTS voice models")
        self._make_nav_btn(nav, "🐞 Bug Log",        self._open_bug_tracker,
                           tooltip="View session log and error reports")
        self._make_nav_btn(nav, "⊕ Plugins", self._open_plugins_manager,
                           tooltip="Install, enable or disable plugins")
        self._make_nav_btn(nav, "ℹ About",    self._show_about_dialog,
                           tooltip="Version info, credits, and logo")
        self._update_btn = self._make_nav_btn(nav, "⟳ Updates", self._on_update_btn_click,
                           tooltip="Check for app updates on GitHub")
        self._update_btn._lbl.configure(fg=C["text2"])  # quiet by default
        self._update_available_version = ""

        self._status_pill = tk.Label(nav, textvariable=self.status_var,
                                      font=("Courier New", 9, "bold"),
                                      fg=C["success"], bg=C["pill_bg"],
                                      padx=10, pady=4,
                                      highlightthickness=1,
                                      highlightbackground=C["success"])
        self._status_pill.pack(side="left", padx=(6, 0))

    def _make_nav_btn(self, parent, text, command, accent=False, tooltip=None):
        nbg = C["accent"]     if accent else C["nav_btn"]
        hbg = C["accent_dim"] if accent else C["nav_hover"]
        btn = GlowButton(parent, text=text, command=command,
                         normal_bg=nbg, hover_bg=hbg,
                         fg="white" if accent else C["text"],
                         font=("Courier New",8,"bold"),
                         tooltip=tooltip)
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
                   fg=C["text2"], font=("Courier New",8),
                   tooltip="Clear all text in the editor")
        self._clear_btn.pack(side="left", padx=2)
        self._load_btn = GlowButton(bf, text="⬆ Load File", command=self._load_file,
                   normal_bg=C["accent_dim"], hover_bg=C["accent"],
                   fg=C["accent2"], font=("Courier New",8,"bold"),
                   tooltip="Load a file (PDF, DOCX, EPUB, ODT, TXT, MD, HTML, RTF…)")
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

        # Header row: Voice Name + Rename Button
        voice_hdr = tk.Frame(vf, bg=C["surface"])
        voice_hdr.pack(fill="x")
        self._voice_name_lbl = tk.Label(voice_hdr, text="Loading voices...",
                                        font=("Courier New",10,"bold"),
                                        fg=C["text"], bg=C["surface"],
                                        wraplength=210, justify="left")
        self._voice_name_lbl.pack(side="left", fill="x", expand=True)
        self._rename_btn = tk.Button(voice_hdr, text="✎", font=("Courier New", 11),
                                     fg=C["accent2"], bg=C["surface"], relief="flat",
                                     cursor="hand2", command=self._rename_voice,
                                     activebackground=C["surface"], activeforeground=C["accent"])
        self._rename_btn.pack(side="right", padx=(4,0))
        attach_tooltip(self._rename_btn, "Rename this voice (saves a custom display name)")

        self._voice_engine_lbl = tk.Label(vf, text="",
                                          font=("Courier New",7), fg=C["muted"], bg=C["surface"])
        self._voice_engine_lbl.pack(anchor="w", pady=(1,4))

        # Dropdown row: Combobox + Preview Button
        combo_row = tk.Frame(vf, bg=C["surface"])
        combo_row.pack(fill="x")
        self._voice_combo = ttk.Combobox(combo_row, textvariable=self.voice_var,
                                         state="readonly", font=("Courier New",9), width=22)
        self._voice_combo.pack(side="left", fill="x", expand=True)
        self._voice_combo.bind("<<ComboboxSelected>>", self._on_voice_change)
        self._preview_btn = tk.Button(combo_row, text="▶", font=("Courier New", 10, "bold"),
                                      fg="white", bg=C["accent"], relief="flat",
                                      cursor="hand2", command=self._preview_voice, width=2,
                                      activebackground=C["speak_hover"], activeforeground="white")
        self._preview_btn.pack(side="right", padx=(6,0))
        attach_tooltip(self._preview_btn, "Preview selected voice — plays a short test sentence")

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
        _ref_browse_btn = tk.Button(ref_row, text="…", font=("Courier New", 8),
                  bg=C["surface2"], fg=C["accent2"], relief="flat",
                  cursor="hand2", padx=4,
                  activebackground=C["border"],
                  command=self._browse_ref_audio)
        _ref_browse_btn.pack(side="left", padx=(2, 0))
        attach_tooltip(_ref_browse_btn, "Browse for a reference audio clip for voice cloning")

        tk.Label(self._ref_frame,
                 text="Leave blank to use Chatterbox default voice.",
                 font=("Courier New", 6), fg=C["muted"], bg=C["surface"]
                 ).pack(anchor="w", pady=(1, 0))

        SectionHeader(self._right, "CONTROLS", "◆").pack(fill="x", pady=(6,0))
        cf = tk.Frame(self._right, bg=C["surface"]); cf.pack(fill="x", padx=8, pady=4)
        NumericControl(cf,"Speed ×",self.speed_var,0.5,3.0,0.1,"{:.1f}",
                       tooltip="Playback speed multiplier (0.5 = half speed, 2.0 = double)").pack(anchor="w",pady=2)
        NumericControl(cf,"Pitch",  self.pitch_var,0.5,2.0,0.1,"{:.1f}",
                       tooltip="Voice pitch adjustment (espeak-ng only)").pack(anchor="w",pady=2)
        NumericControl(cf,"Volume %",self.volume_var,0,100,5,"{:d}",
                       tooltip="Output volume (0–100 %)").pack(anchor="w",pady=2)

        SectionHeader(self._right, "PLAYBACK", "▶").pack(fill="x", pady=(8,0))
        pf = tk.Frame(self._right, bg=C["surface"]); pf.pack(fill="x", padx=10, pady=6)
        self._speak_btn = GlowButton(pf, text="▶  SPEAK", command=self._on_speak,
                                      normal_bg=C["speak_bg"], hover_bg=C["speak_hover"],
                                      fg="white", font=("Courier New",12,"bold"),
                                      tooltip="Start reading the text aloud (or resume from save point)")
        self._speak_btn.pack(fill="x", pady=(0,5))
        self._speak_btn._lbl.configure(pady=11)
        self._stop_btn = GlowButton(pf, text="■  STOP", command=self._on_stop,
                                     normal_bg=C["stop_bg"], hover_bg=C["stop_hover"],
                                     fg="white", font=("Courier New",9,"bold"),
                                     tooltip="Stop playback and save position as a resume point")
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
        for eng in [_ENGINE_KOKORO, _ENGINE_EDGE_TTS, _ENGINE_ESPEAK]:
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
    def _load_voices(self, preserve_selection: bool = False):
        if voices is None:
            self._voice_name_lbl.configure(text="Loading engines…")
            return
        old_idx = self._voice_combo.current() if preserve_selection else -1
        self._all_voices = voices.get_all_voices()
        # Privacy filter: respect the "Use cloud TTS" toggle in Settings.
        # When off, Edge TTS voices are hidden (the user is opting out of
        # sending text to api.edge.microsoft.com / speech.platform.bing.com).
        if not self.cfg.get("cloud_tts_enabled", False):
            self._all_voices = [
                (d, e, n) for (d, e, n) in self._all_voices
                if e != _ENGINE_EDGE_TTS
            ]
        if not self._all_voices: self._all_voices=[("No engines found","","")]
        aliases = self.cfg.get("voice_aliases", {})
        names = []
        for display, engine, vname in self._all_voices:
            key = f"{engine}::{vname}"
            alias = aliases.get(key)
            names.append(f"{alias}  ★" if alias else display)
        self._voice_combo["values"] = names
        if preserve_selection and 0 <= old_idx < len(names):
            self._voice_combo.current(old_idx)
        else:
            idx = min(self.cfg.get("voice_idx", 0), len(names)-1)
            self._voice_combo.current(idx)
        self._update_voice_display()


    # ══════════════════════════════════════════════════════════════════════════
    #  VOICE PREVIEW & ALIAS
    # ══════════════════════════════════════════════════════════════════════════

    def _preview_voice(self):
        """Synthesise a short test sentence in the selected voice.

        The preview text is hardcoded and NOT in the textarea, so the
        highlight loop must be suppressed during preview — otherwise the
        loop reads stale chunk data from a previous Speak run and
        highlights random words in the user's input (the "skeleton"
        bug from the v2.5.1 screenshot).

        The status pill shows both the engine and the voice name so
        the user can verify which engine is actually being used
        (matters for quality — Edge TTS > Kokoro > espeak on most
        hardware).
        """
        if self._is_speaking:
            self._on_stop(); return
        idx = self._voice_combo.current()
        if idx < 0 or idx >= len(self._all_voices): return
        display, engine, voice_name = self._all_voices[idx]
        speed = self.speed_var.get()
        pitch = self.pitch_var.get()
        preview_text = "Hello. This is a preview of my voice. I hope you like how I sound."
        # Show engine + voice in the status so the user can see whether
        # they are previewing Edge TTS (high quality) vs Kokoro (good
        # but slow on this CPU) vs espeak (very robotic).
        engine_short = {
            "Kokoro ONNX":      "Kokoro",
            "Edge TTS (Cloud)": "Edge (cloud)",
            "espeak-ng":        "espeak",
        }.get(engine, engine)
        self._set_status(f"PREVIEWING · {engine_short}", C["warning"])
        try:
            self._preview_btn.configure(state="disabled", bg=C["surface2"])
        except Exception:
            pass
        # ── Suppress highlight loop during preview ─────────────────────
        # _on_start (in _setup_audio_callbacks) checks this flag and
        # skips _run_realtime_highlight_loop while it is set. Cleared
        # in finally so a later Speak resumes normal highlighting.
        self._is_previewing = True
        # Clear any leftover highlight from a previous Speak so the user
        # doesn't see a stale word highlighted during the preview audio.
        self._clear_highlight()
        def _worker():
            try:
                wav = voices.synthesize(preview_text, engine, voice_name, speed, pitch)
                if wav:
                    audio_handler.play_wav(wav)
            except Exception as e:
                if bug_tracker: bug_tracker.warning(f"Preview failed: {e}")
            finally:
                self._is_previewing = False
                self.root.after(0, lambda: self._set_status("READY", C["success"]))
                self.root.after(0, lambda: self._preview_btn.configure(
                    state="normal", bg=C["accent"]))
        threading.Thread(target=_worker, daemon=True).start()

    def _rename_voice(self):
        """Assign a custom display alias to the selected voice."""
        idx = self._voice_combo.current()
        if idx < 0 or idx >= len(self._all_voices): return
        display, engine, vname = self._all_voices[idx]
        aliases = self.cfg.setdefault("voice_aliases", {})
        key = f"{engine}::{vname}"
        current = aliases.get(key, display)
        new_name = self._dark_prompt(self.root,
                                     "Rename Voice",
                                     f"Custom display name for:\n{display}",
                                     default=current)
        if new_name is None: return
        if not new_name.strip() or new_name.strip() == display:
            aliases.pop(key, None)
        else:
            aliases[key] = new_name.strip()
        save_config(self.cfg)
        self._load_voices(preserve_selection=True)

    def _dark_prompt(self, parent, title: str, message: str, default: str = "") -> "str|None":
        """Themed single-line text input dialog. Returns the string or None if cancelled."""
        result = [None]
        d = tk.Toplevel(parent)
        d.title(title)
        d.configure(bg=C["bg"])
        d.transient(parent)
        d.resizable(False, False)
        d.grab_set()
        body = tk.Frame(d, bg=C["bg"], padx=24, pady=18); body.pack(fill="x")
        tk.Label(body, text=message, font=("Courier New", 9), fg=C["text2"],
                 bg=C["bg"], justify="left").pack(anchor="w", pady=(0, 8))
        var = tk.StringVar(value=default)
        entry = tk.Entry(body, textvariable=var, font=("Courier New", 11),
                         bg=C["surface"], fg=C["text"], insertbackground=C["cursor"],
                         relief="flat", bd=4, highlightthickness=1,
                         highlightbackground=C["accent"])
        entry.pack(fill="x", ipady=4)
        entry.select_range(0, "end")
        entry.focus_set()
        foot = tk.Frame(d, bg=C["surface2"], pady=10); foot.pack(fill="x")
        def _ok(_=None):   result[0] = var.get(); d.destroy()
        def _cancel(_=None): result[0] = None;    d.destroy()
        entry.bind("<Return>",  _ok)
        entry.bind("<Escape>",  _cancel)
        d.bind("<Escape>",      _cancel)
        tk.Button(foot, text="  Cancel  ", font=("Courier New", 9),
                  bg=C["surface"], fg=C["muted"], relief="flat", padx=10, pady=5,
                  command=_cancel).pack(side="right", padx=(4,12))
        tk.Button(foot, text="  Save  ", font=("Courier New", 9, "bold"),
                  bg=C["accent"], fg="white", relief="flat", padx=10, pady=5,
                  command=_ok).pack(side="right", padx=4)
        d.update_idletasks()
        sw = parent.winfo_screenwidth(); sh = parent.winfo_screenheight()
        w = max(d.winfo_reqwidth(), 380); h = d.winfo_reqheight()
        d.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//2)}")
        parent.wait_window(d)
        return result[0]

    # ══════════════════════════════════════════════════════════════════════════
    #  HIGHLIGHT CHUNK FINDER (robust whitespace-aware)
    # ══════════════════════════════════════════════════════════════════════════

    def _find_chunk_start(self, text: str, chunk: str, search_start: int) -> int:
        """
        Robustly find the start position of a chunk in the original text.
        3-tier fallback:
          1. Exact match of first 6 words joined by single spaces
          2. Regex match allowing flexible whitespace (\\s+) between words
          3. First word match
        Fixes off-by-one highlight when document contains newlines or
        multiple spaces between the words at the start of a chunk.
        """
        import re as _re
        words = chunk.split()
        if not words:
            return search_start
        # Tier 1: exact match
        first = " ".join(words[:6])
        pos = text.find(first, search_start)
        if pos != -1:
            return pos
        # Tier 2: flexible whitespace — bounded window prevents catastrophic backtracking
        pattern = r'\s+'.join(_re.escape(w) for w in words[:4])
        try:
            m = _re.search(pattern, text[search_start:search_start + 2000])
            if m:
                return search_start + m.start()
        except Exception:
            pass
        # Tier 3: first word only
        pos = text.find(words[0], search_start)
        return pos if pos != -1 else search_start

    def _on_voice_change(self,_=None): self._update_voice_display(); self._save_cfg_now()

    # Voice cloning engines removed — only predefined voices supported
    _ZERO_SHOT_ENGINES: set = set()

    def _update_voice_display(self):
        idx = self._voice_combo.current()
        if 0 <= idx < len(self._all_voices):
            display, engine, vname = self._all_voices[idx]
            aliases = self.cfg.get("voice_aliases", {})
            key = f"{engine}::{vname}"
            alias = aliases.get(key)
            short = alias if alias else (display.split(" · ",1)[-1] if " · " in display else display)
            self._voice_name_lbl.configure(text=short)
            ENGINE_LICENSES = {
                "Kokoro ONNX":    "Apache 2.0",
                "espeak-ng":      "GPL 3.0",
                "Edge TTS (Cloud)": "Microsoft (cloud)",
                "Chatterbox":     "MIT",
                "OmniVoice":      "Apache 2.0",
                "F5-TTS":         "MIT",
            }
            license_str = ENGINE_LICENSES.get(engine, "Open Source")
            self._voice_engine_lbl.configure(text=f"{engine}  ·  {license_str}")
            is_zero_shot = any(e in engine for e in self._ZERO_SHOT_ENGINES)
            try:
                if is_zero_shot: self._ref_frame.pack(fill="x", pady=(4, 0))
                else: self._ref_frame.pack_forget()
            except Exception:
                pass
        self.cfg["voice_idx"] = idx

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
            # Load any existing save point for this file
            mgr = self._ensure_save_mgr()
            if mgr:
                mgr.load_for_file(path)
                if mgr.has_save_point():
                    chunk_n = mgr.get_start_chunk() + 1
                    self._set_status(
                        f"Save point found — ▶ SPEAK will resume from chunk {chunk_n}",
                        C.get("accent2", "#00c8ff")
                    )
                    # Auto-clear after 6 s so the status bar doesn't stay forever
                    self.root.after(6000, lambda: self._set_status("READY", C["success"]))

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
        audio_handler.begin_session()   # arm stop-flag BEFORE worker starts
        self._stop_flag.clear()
        self._wav_buffer.clear()
        self._fallback_warned = False
        self.progress_var.set(0)
        self._speak_btn.set_colors("#333344", "#333344")
        self._cancel_highlights()
        self._clear_highlight()
        all_chunks = voices.chunk_text(text)

        # ── Resume from save point ────────────────────────────────────────────
        mgr = self._ensure_save_mgr()
        start_chunk = mgr.get_start_chunk() if mgr else 0
        start_chunk = max(0, min(start_chunk, len(all_chunks) - 1))
        self._session_start_chunk = start_chunk   # saved so _on_stop can compute abs index

        if start_chunk > 0:
            self._set_status(f"Resuming from chunk {start_chunk + 1} / {len(all_chunks)}…",
                             C.get("accent2", "#00c8ff"))

        chunks = all_chunks[start_chunk:]
        total  = len(chunks)

        # Build char-position index for all chunks (needed for word highlighting)
        # Uses _find_chunk_start for robust whitespace-tolerant matching.
        # Index over the FULL text, then slice to match the resumed chunks.
        all_positions = []
        search_start  = 0
        for chunk in all_chunks:
            pos = self._find_chunk_start(text, chunk, search_start)
            all_positions.append(pos)
            first_word = chunk.split()[0] if chunk.split() else ""
            search_start = pos + max(1, len(first_word))
        chunk_positions = all_positions[start_chunk:]

        engine_tag = "(cloud) " if engine == _ENGINE_EDGE_TTS else ""
        self._set_status(f"SYNTHESIZING {engine_tag}...", C["warning"])

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

    # ── Save-Point / Resume helpers ────────────────────────────────────────────

    def _ensure_save_mgr(self):
        """
        Return the SavePointManager instance, creating it on first call.

        SavePointManager is imported in a background thread at startup; this
        lazy getter means we never block the UI waiting for the import, and we
        also never crash if the module failed to load.
        """
        if self._save_mgr is None and SavePointManager is not None:
            self._save_mgr = SavePointManager()
        return self._save_mgr

    def _on_stop(self):
        self._cancel_highlights()
        self._stop_flag.set()
        audio_handler.stop_playback()

        # ── Save resume point ─────────────────────────────────────────────────
        # Persist the absolute chunk index so the next Speak resumes from here.
        # _playing_chunk_abs is the 0-based index within the current session's
        # slice; adding _session_start_chunk gives the absolute position in the
        # full text.
        mgr = self._ensure_save_mgr()
        if mgr and self._is_speaking:
            abs_chunk = self._session_start_chunk + self._playing_chunk_abs
            mgr.set_save_point(abs_chunk)
            if abs_chunk > 0:
                self._set_status(f"Saved at chunk {abs_chunk + 1} — click ▶ SPEAK to resume",
                                 C.get("accent2", "#00c8ff"))
                self.root.after(5000, lambda: self._set_status("READY", C["success"]))

        self._on_speak_complete()

    def _on_speak_complete(self):
        self._is_speaking = False
        self._set_status("READY", C["success"])
        self._speak_btn.set_colors(C["speak_bg"], C["speak_hover"])
        self.progress_var.set(0)
        if not self._stop_flag.is_set():
            self._generation = getattr(self, "_generation", 0) + 1
            # Finished naturally (not stopped) — clear the save point so the
            # next Speak starts from the beginning rather than a stale position.
            mgr = self._ensure_save_mgr()
            if mgr:
                mgr.clear_save_point()
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
                char_start  = offset
                start_idx   = f"1.0+{char_start}c"
                # Advance past whitespace so we tag the word, not the space before it
                char_at       = self._textarea.get(start_idx)
                _ws_limit     = offset + 50          # fixed upper bound, not tautology
                while char_at in (" ", "\t", "\n", "\r") and char_start < _ws_limit:
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
            # During a voice preview the audio plays a hardcoded test
            # sentence that is NOT in the textarea, so highlighting
            # would either (a) pick up stale offsets from a previous
            # Speak run or (b) read missing offsets and no-op. Skip
            # the loop entirely; the status pill "PREVIEWING…" is the
            # only visual feedback the user needs.
            if getattr(self, "_is_previewing", False):
                return
            # after(0,...) puts this on the main thread event queue immediately.
            # get_playback_position() uses wall-clock time from Popen, so the
            # loop catches up to real elapsed time on its first iteration.
            self.root.after(0, lambda: self._run_realtime_highlight_loop(chunk_idx))

        def _on_stop():
            # During preview, the highlight is already cleared (and
            # there's no Speak to resume from) so don't touch it here.
            if getattr(self, "_is_previewing", False):
                return
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
        import re as _re2
        word_offsets = []    # (char_start, char_end) per VOICED word only
        pos = chunk_char_start
        for orig_word, is_voiced in voiced_pairs:
            # Bounded 300-char window prevents drift if text was edited mid-session
            window = full_text[pos:pos + 300]
            found_rel = window.find(orig_word)
            if found_rel != -1:
                found = pos + found_rel
                if is_voiced:
                    word_offsets.append((found, found + len(orig_word)))
                pos = found + len(orig_word)
            else:
                # Fallback: regex match stripping punctuation differences
                clean = _re2.sub(r"[^\w\d]", "", orig_word)
                if clean:
                    try:
                        m = _re2.search(r"\b" + _re2.escape(clean) + r"\b", window)
                        if m:
                            actual_start = pos + m.start()
                            actual_end   = pos + m.end()
                            if is_voiced:
                                word_offsets.append((actual_start, actual_end))
                            pos = actual_end
                            continue
                    except Exception:
                        pass
                if is_voiced:
                    word_offsets.append((pos, pos + len(orig_word)))
                pos += len(orig_word) + 1

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
                    # Schedule clear so the last word is briefly visible.
                    # Re-check the stop flag inside the 400 ms callback so a
                    # Stop click in that window doesn't yank the last word
                    # off the screen before the user sees it.
                    def _delayed_clear():
                        if self._stop_flag.is_set():
                            return
                        self._clear_highlight()
                    self.root.after(400, _delayed_clear)
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
            toast.withdraw()
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
            toast.geometry(f"+{max(0, sw - tw - 20)}+{max(0, sh - th - 60)}")
            toast.deiconify()

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
        win.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//2)}")
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
        engine_tag = "(cloud) " if engine == _ENGINE_EDGE_TTS else ""
        self._set_status(f"SYNTHESIZING {engine_tag}…", C["warning"])

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

        def _do(engine=engine):
            # ── Always re-synthesize the FULL text for export ─────────────────
            # Use a LOCAL buffer so the export never races with the speak thread's
            # _wav_buffer. Shared buffer access was the root cause of jumbled audio.
            self._stop_flag.clear()
            self._wav_buffer.clear()
            chunks = voices.chunk_text(text)
            total  = max(len(chunks), 1)
            engine_tag = "(cloud) " if engine == _ENGINE_EDGE_TTS else ""
            self.root.after(0, lambda: self._set_status(
                f"EXPORTING {engine_tag}…", C["warning"]))

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

        def _export_work(engine=engine):
            self._stop_flag.clear()
            self._wav_buffer.clear()
            chunks = voices.chunk_text(text)
            total  = max(len(chunks), 1)
            engine_tag = "(cloud) " if engine == _ENGINE_EDGE_TTS else ""
            self.root.after(0, lambda: self._set_status(
                f"EXPORTING {engine_tag}…", C["warning"]))

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
        """Return (label, onnxruntime_provider) for the best available GPU.

        OpenVINO is intentionally NOT included in the auto-pick list — see
        voices._KOKORO_INCOMPATIBLE_PROVIDERS for the technical reason.
        """
        avail = voices.get_available_providers()
        if "CUDAExecutionProvider"      in avail: return ("CUDA (NVIDIA)",  "CUDAExecutionProvider")
        if "ROCMExecutionProvider"      in avail: return ("ROCm (AMD)",     "ROCMExecutionProvider")
        if "TensorrtExecutionProvider"  in avail: return ("TensorRT",       "TensorrtExecutionProvider")
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

    def _openvino_downgrade_notice(self):
        """Show a one-time dark-themed info dialog when an old config had
        OpenVINO selected and we had to downgrade to CPU/GPU."""
        text = (
            "OpenVINO is no longer auto-selected for Kokoro TTS.\n\n"
            "Reason: Kokoro's STFT op uses dynamic-rank tensors. "
            "OpenVINO's CPU plugin (the default backend) cannot compile "
            "dynamic-rank Parameter ops, so the first synthesis attempt "
            "with OpenVINO hangs for 8-15 seconds before failing.\n\n"
            "We now skip OpenVINO automatically and fall back to the next "
            "available GPU provider, or CPU if none is available.\n\n"
            "To re-enable OpenVINO manually, you would need a Kokoro model "
            "with static STFT shapes — not available upstream yet.\n"
            "Tracking: huggingface/optimum-intel#1653"
        )
        if hasattr(self, "_dark_info"):
            self._dark_info("OpenVINO skipped", text)
        else:
            try:
                from tkinter import messagebox
                messagebox.showinfo("OpenVINO skipped", text)
            except Exception:
                pass

    def _toggle_gpu(self):
        """Cycle CPU ↔ best available GPU. Stops synthesis first."""
        if self._is_speaking:
            self._stop_flag.set()
            audio_handler.stop_playback()
            # Yield to the event loop so the audio thread can detect
            # the stop flag and flush its buffer before we switch providers.
            # Was: import time; time.sleep(0.15)  — froze the main thread.
            self.after(150, self._finish_toggle_gpu)
            return

        self._do_toggle_gpu()

    def _finish_toggle_gpu(self):
        """Resume the GPU toggle after the audio thread has had time to stop."""
        try:
            if not self.winfo_exists(): return
        except Exception: return
        self._is_speaking = False
        self._wav_buffer.clear()
        self._set_status("READY", C["success"])
        self._speak_btn.set_colors(C["speak_bg"], C["speak_hover"])
        self.progress_var.set(0)
        self._do_toggle_gpu()

    def _do_toggle_gpu(self):
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

        def _install(pkg, label):
            status_var.set(f"Installing {label}...")
            status_lbl.configure(fg=C["warning"])
            win.update()
            def _do():
                try:
                    r = _sp.run([_sys.executable, "-m", "pip", "install", pkg],
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
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "onnxruntime-openvino"],
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
            badge = getattr(self, "_local_badge", None)
            if badge is not None:
                badge.configure(fg=C["success"], bg=C["header_bg"])
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
        # Auto-install whisper deps in the background the first time the
        # user opens the audio-to-text window. The window itself handles the
        # case where the package isn't ready yet.
        try:
            import dep_installer
            for feat in ("whisper", "vosk", "google_stt"):
                if not dep_installer.feature_available(feat):
                    dep_installer.install_in_background(feat)
                    break   # only kick off one install at a time
        except Exception:
            pass
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
        from voice_library import VoiceLibraryWindow
        VoiceLibraryWindow(self.root, on_engine_change=lambda: self._load_voices(preserve_selection=True))

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
        d.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//2)}")
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
        d.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//2)}")
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
        picker.geometry(f"620x440+{max(0,(sw-620)//2)}+{max(0,(sh-440)//2)}")
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

        # ── Cloud TTS privacy toggle ──────────────────────────────────────────
        frm_cloud = tk.Frame(win, bg=C["surface"], padx=16, pady=10)
        frm_cloud.pack(fill="x", padx=16, pady=4)
        tk.Label(frm_cloud, text="Cloud TTS",
                 font=("Courier New", 9, "bold"),
                 fg=C["text2"], bg=C["surface"]).pack(anchor="w", pady=(0,6))

        cloud_row = tk.Frame(frm_cloud, bg=C["surface"]); cloud_row.pack(fill="x", pady=(0,4))
        tk.Label(cloud_row, text="Edge TTS (Microsoft cloud) — opt-in only",
                 font=("Courier New", 9), fg=C["text2"],
                 bg=C["surface"], wraplength=320, justify="left").pack(side="left")
        _cloud_state = [self.cfg.get("cloud_tts_enabled", False)]
        def _on_cloud_toggle(s):
            _cloud_state[0] = s
        _cloud_pill = PillToggle(cloud_row, state=_cloud_state[0], callback=_on_cloud_toggle)
        _cloud_pill.pack(side="right")

        tk.Label(frm_cloud,
                 text=("✓ OFF by default — TTS runs fully on this machine, no text leaves your PC.  "
                       "Turn ON only if you need higher-quality voices and accept that your text "
                       "is sent to api.edge.microsoft.com for synthesis."),
                 font=("Courier New", 7), fg=C["muted"],
                 bg=C["surface"], wraplength=420, justify="left").pack(anchor="w", pady=(2,0))

        # ── Window: maximise on launch ─────────────────────────────────────────
        frm_max = tk.Frame(win, bg=C["surface"], padx=16, pady=10)
        frm_max.pack(fill="x", padx=16, pady=4)
        tk.Label(frm_max, text="Window",
                 font=("Courier New", 9, "bold"),
                 fg=C["text2"], bg=C["surface"]).pack(anchor="w", pady=(0,6))

        max_row = tk.Frame(frm_max, bg=C["surface"]); max_row.pack(fill="x", pady=(0,4))
        tk.Label(max_row, text="Maximise window on launch",
                 font=("Courier New", 9), fg=C["text2"],
                 bg=C["surface"], wraplength=320, justify="left").pack(side="left")
        _max_state = [self.cfg.get("startup_maximised", True)]
        def _on_max_toggle(s):
            _max_state[0] = s
        _max_pill = PillToggle(max_row, state=_max_state[0], callback=_on_max_toggle)
        _max_pill.pack(side="right")

        tk.Label(frm_max,
                 text=("When ON, the window opens maximised. Turn OFF if you "
                       "prefer the previous size, or have a layout that gets "
                       "clipped on maximise."),
                 font=("Courier New", 7), fg=C["muted"],
                 bg=C["surface"], wraplength=420, justify="left").pack(anchor="w", pady=(2,0))

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
                        try:
                            _install_btn_var.set(f" Install {len(outdated)} ")
                            _install_btn.configure(state="normal")
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
            pkgs = [p["name"] for p in _outdated_list[0]]
            def _worker2():
                try:
                    r2 = _sp2.run(
                        [_sys.executable, "-m", "pip", "install", "--upgrade"] + pkgs,
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
            cloud_was_off = not self.cfg.get("cloud_tts_enabled", False)
            self.cfg["cloud_tts_enabled"] = _cloud_state[0]
            self.cfg["startup_maximised"] = _max_state[0]
            save_config(self.cfg)
            # Sync the right-panel toggle immediately
            try: self._update_toggle.set(self.cfg["auto_update_check"])
            except Exception: pass
            # Reload voice list to apply the cloud TTS privacy filter
            self._load_voices(preserve_selection=True)
            # If user just turned cloud ON, auto-install edge_tts in background
            if cloud_was_off and _cloud_state[0]:
                try:
                    import dep_installer
                    if not dep_installer.feature_available("edge_tts"):
                        dep_installer.install_in_background(
                            "edge_tts",
                            on_done=lambda ok, fn=self._load_voices:
                                self.root.after(0, lambda: fn(preserve_selection=True))
                        )
                        try: self._set_status("Installing Edge TTS…", C["warning"])
                        except Exception: pass
                except Exception:
                    pass
            _toplevel.destroy()   # destroy the real Toplevel, not win_inner
        GlowButton(_toplevel, text="Save", command=_save, normal_bg=C["accent"],
                   hover_bg=C["speak_hover"], fg="white").pack(pady=12)

    # ── Misc ───────────────────────────────────────────────────────────────────
    def _set_status(self, text, color=None):
        color = color or C["success"]
        if len(text) > 32:
            text = text[:29] + "…"
        try:
            self.status_var.set(text)
            self._status_pill.configure(fg=color, highlightbackground=color)
            badge = getattr(self, "_local_badge", None)
            if badge is not None:
                try: badge.configure(fg=color)
                except tk.TclError: pass
        except (tk.TclError, AttributeError):
            pass

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
    # Default update URL. Overridable by the user via cfg["update_url"] so this
    # can be repointed at a self-hosted mirror, a fork, or a private release
    # endpoint without editing source. The default is a raw GitHub file that
    # must contain only a version string (e.g. "2.4.2\n").
    _DEFAULT_VERSION_URL = (
        "https://raw.githubusercontent.com/"
        "jspgamer0503-coder/TTSVoices/main/VERSION"
    )

    @property
    def _VERSION_URL(self) -> str:
        return self.cfg.get("update_url") or self._DEFAULT_VERSION_URL

    _update_available_version: str = ""
    _update_glow_job = None
    _update_glow_phase = 0

    def _start_update_check_if_enabled(self):
        """Called from _finish_init: run update check in background if toggled on."""
        if self.cfg.get("auto_update_check", True):
            self.root.after(800, self._check_for_update_bg)   # 0.8s delay so UI settles

    @staticmethod
    def _parse_version(s: str) -> tuple:
        """Parse a 'X.Y.Z' string into a comparable tuple. Tolerates suffixes
        like '2.4.1-rc1' and missing patch numbers. Returns () on garbage so
        any unparseable result is treated as 'no update' rather than crashing
        or rolling the version back."""
        s = s.strip()
        if not s:
            return ()
        # Strip suffix: take everything up to the first '-' or '+' (PEP 440
        # pre-release / local-version separators). Keep the dots so we can
        # split into version components.
        cut = len(s)
        for sep in ("-", "+"):
            i = s.find(sep)
            if 0 <= i < cut:
                cut = i
        s = s[:cut]
        parts = []
        for p in s.split("."):
            if p.isdigit():
                parts.append(int(p))
            else:
                # Strip trailing non-digits ("1a" -> 1) then stop
                digits = ""
                for ch in p:
                    if ch.isdigit():
                        digits += ch
                    else:
                        break
                if digits:
                    parts.append(int(digits))
                break
        return tuple(parts)

    def _check_for_update_bg(self, manual: bool = False):
        """Fire a background thread to check the remote VERSION file."""
        if manual:
            try:
                self._update_btn._lbl.configure(text="⟳ Checking…", fg=C["text2"])
                self._update_btn.set_colors(C["nav_btn"], C["nav_hover"])
            except Exception:
                pass

        url = self._VERSION_URL
        def _worker():
            try:
                import urllib.request as _ur, re as _re, socket as _sock
                req = _ur.Request(
                    url,
                    headers={"User-Agent": f"TTSVoices/{__version__}"}
                )
                with _ur.urlopen(req, timeout=1.5) as resp:
                    # urlopen already raises HTTPError for 4xx/5xx, so by the
                    # time we get here the response is a successful 2xx.
                    latest = resp.read(64).decode("utf-8", errors="ignore").strip()
                cur_t  = self._parse_version(__version__)
                new_t  = self._parse_version(latest)
                if not new_t or not cur_t:
                    return
                if new_t > cur_t:
                    self.root.after(0, lambda: self._show_update_available(latest))
                else:
                    self.root.after(0, lambda: self._show_update_current(manual))
            except Exception as e:
                if manual:
                    _err = str(e)   # capture by value — Python 3.12 deletes 'e' after except
                    self.root.after(0, lambda: self._show_update_error(_err))

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
            PLUGINS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            try: import os as _os; _os.chmod(PLUGINS_DIR, 0o700)
            except Exception: pass
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
        win.geometry(f"520x{min(600,max(480,wh))}+{max(0,(sw-520)//2)}+{(sh-min(600,max(480,wh)))//2}")

    # ── GitHub release endpoints used by the in-app updater ──────────────────
    _RELEASES_URL  = "https://api.github.com/repos/jspgamer0503-coder/TTSVoices/releases/latest"
    _RELEASES_PAGE = "https://github.com/jspgamer0503-coder/TTSVoices/releases"
    _CHANGELOG_URL = (
        "https://raw.githubusercontent.com/"
        "jspgamer0503-coder/TTSVoices/main/CHANGELOG.md"
    )
    _last_release_meta: dict = {}   # cached /releases/latest response (asset URLs etc.)

    def _on_update_btn_click(self):
        """Update button clicked: always open the updates menu dialog."""
        # Kick off a fresh release-metadata fetch in the background so the
        # dialog can show the latest version + asset URLs without blocking.
        self._fetch_release_meta_bg()
        self._show_update_menu_dialog()

    def _show_update_menu_dialog(self):
        """Always-open Updates dialog. Offers check, changelog, reinstall,
        and a link to the GitHub releases page. Works even when the local
        version is already the latest — pressing Update should never feel
        like a no-op."""
        import webbrowser as _wb
        win = tk.Toplevel(self.root)
        win.title("TTS Voices — Updates")
        win.configure(bg=C["bg"])
        win.resizable(True, True)
        win.minsize(480, 360)
        win.transient(self.root)
        win.attributes("-topmost", True)

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=C["surface"]); hdr.pack(fill="x")
        tk.Label(hdr, text="⟳  Updates & Maintenance",
                 font=("Courier New", 12, "bold"),
                 fg=C["accent2"], bg=C["surface"],
                 padx=20, pady=12).pack(side="left")
        # State badge on the right
        state_lbl = tk.Label(hdr, text="  checking…  ",
                             font=("Courier New", 8, "bold"),
                             fg=C["muted"], bg=C["surface"],
                             padx=8, pady=4)
        state_lbl.pack(side="right", padx=12)
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        # ── Body ──────────────────────────────────────────────────────────
        body = tk.Frame(win, bg=C["bg"], padx=22, pady=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        def _kv(row, label, value, val_fg=None):
            tk.Label(body, text=label, font=("Courier New", 9, "bold"),
                     fg=C["text2"], bg=C["bg"], anchor="w", width=18
                     ).grid(row=row, column=0, sticky="w", pady=2)
            v = tk.Label(body, text=value, font=("Courier New", 9),
                         fg=val_fg or C["text"], bg=C["bg"], anchor="w")
            v.grid(row=row, column=1, sticky="w", pady=2)
            return v

        ver_lbl  = _kv(0, "Current version:",
                       __version__, val_fg=C["accent2"])
        latest_lbl = _kv(1, "Latest known:",
                          self._update_available_version or __version__,
                          val_fg=C["warning"] if self._update_available_version
                                 else C["muted"])
        locs = self._detect_install_locations()
        loc_text = "\n".join(f"  • {x}" for x in locs[:3]) if locs else "  (none detected)"
        tk.Label(body, text="Install location:",
                 font=("Courier New", 9, "bold"),
                 fg=C["text2"], bg=C["bg"], anchor="nw"
                 ).grid(row=2, column=0, sticky="nw", pady=(6, 2))
        loc_lbl = tk.Label(body, text=loc_text,
                           font=("Courier New", 8),
                           fg=C["text"], bg=C["bg"], anchor="w",
                           justify="left")
        loc_lbl.grid(row=2, column=1, sticky="w", pady=(6, 2))

        # Status / progress strip
        status_var = tk.StringVar(value="Ready.")
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")
        status_bar = tk.Frame(win, bg=C["surface2"], pady=6, padx=14)
        status_bar.pack(fill="x")
        status_lbl = tk.Label(status_bar, textvariable=status_var,
                              font=("Courier New", 8),
                              fg=C["text2"], bg=C["surface2"], anchor="w")
        status_lbl.pack(side="left", fill="x", expand=True)
        prog = ttk.Progressbar(status_bar, length=140, mode="determinate",
                               maximum=100)
        # Hidden initially; shown only during downloads
        # (packed on demand via .pack / .pack_forget)

        def _set_status(text, color=None):
            status_var.set(text)
            if color:
                try: status_lbl.configure(fg=color)
                except Exception: pass
            win.update_idletasks()

        def _set_state_badge(text, color):
            try:
                state_lbl.configure(text=f"  {text}  ", fg=color)
            except Exception: pass

        # ── Buttons ───────────────────────────────────────────────────────
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")
        foot = tk.Frame(win, bg=C["surface"], pady=12, padx=14)
        foot.pack(fill="x")
        foot.columnconfigure(0, weight=1); foot.columnconfigure(1, weight=1)
        foot.columnconfigure(2, weight=1); foot.columnconfigure(3, weight=1)

        def _btn(parent, text, cmd, col, row, bold=False, fg=None, bg=None):
            b = tk.Button(parent, text=text,
                          font=("Courier New", 9, "bold" if bold else "normal"),
                          bg=bg or C["surface2"], fg=fg or C["text"],
                          activebackground=C["hover"],
                          activeforeground=C["text"],
                          relief="flat", padx=10, pady=8, cursor="hand2",
                          command=cmd)
            b.grid(row=row, column=col, padx=4, pady=2, sticky="ew")
            return b

        def _do_check():
            _set_status("Checking GitHub for the latest release…")
            _set_state_badge("checking…", C["muted"])
            self._fetch_release_meta_bg(on_done=lambda m: win.after(0, _apply_meta, m))

        def _apply_meta(meta: dict):
            if not meta:
                _set_status("Could not reach GitHub. Try again later.", C["error"])
                _set_state_badge("offline", C["error"])
                return
            self._last_release_meta = meta
            tag = meta.get("tag_name", "").lstrip("v") or "?"
            try:
                latest_lbl.configure(
                    text=tag,
                    fg=C["warning"] if self._parse_version(tag) >
                                      self._parse_version(__version__)
                       else C["success"])
            except Exception: pass
            new = self._parse_version(tag)
            cur = self._parse_version(__version__)
            if new and cur and new > cur:
                _set_state_badge(f"v{tag} available", C["warning"])
                _set_status(f"Version {tag} is available. Click 'Reinstall' to upgrade.",
                            C["warning"])
            else:
                _set_state_badge("up to date", C["success"])
                _set_status("You are running the latest version.", C["success"])

        def _do_changelog():
            _show_changelog(parent=win, set_status=_set_status)

        def _do_reinstall():
            meta = self._last_release_meta
            if not meta:
                _set_status("Checking release first…", C["text2"])
                self._fetch_release_meta_bg(
                    on_done=lambda m: win.after(0, lambda: _start_reinstall(m)))
            else:
                _start_reinstall(meta)

        def _start_reinstall(meta: dict):
            if not meta:
                _set_status("Could not reach GitHub. Try again later.", C["error"])
                return
            deb_asset = next((a for a in meta.get("assets", [])
                              if a.get("name", "").endswith(".deb")), None)
            if not deb_asset:
                _set_status("No .deb asset in the latest release.", C["error"])
                return
            self._reinstall_from_deb(
                url=deb_asset["browser_download_url"],
                filename=deb_asset["name"],
                size=deb_asset.get("size", 0),
                parent=win, set_status=_set_status,
                progress=prog,
            )

        def _do_github():
            win.destroy()
            try: _wb.open(self._RELEASES_PAGE)
            except Exception: pass

        _btn(foot, "  Check for updates  ", _do_check,     0, 0, bold=True,
             fg="white", bg=C["accent"])
        _btn(foot, "  Show changelog  ",    _do_changelog, 1, 0)
        _btn(foot, "  Reinstall (.deb)  ",  _do_reinstall, 2, 0,
             fg=C["warning"])
        _btn(foot, "  Open GitHub releases  ", _do_github,  3, 0)
        _btn(foot, "  Close  ",             win.destroy,   3, 1,
             fg=C["muted"], bg=C["surface"])

        # ── Initial paint ─────────────────────────────────────────────────
        win.update_idletasks()
        w = win.winfo_reqwidth(); h = win.winfo_reqheight()
        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
        win.geometry(f"{max(w,520)}x{max(h,400)}+{(sw-max(w,520))//2}+{max(0,(sh-max(h,400))//2)}")
        win.grab_set()

    def _detect_install_locations(self) -> list:
        """Return a list of human-readable install location strings, ordered
        from most-likely to least-likely. Used by the updates dialog to tell
        the user where the app is actually installed."""
        locs = []
        seen = set()
        # 1. The .deb package install (most common on Debian/Ubuntu)
        for p in ("/usr/share/ttsvoices/ttsvoices.py",
                  "/opt/ttsvoices/ttsvoices.py"):
            if Path(p).is_file() and p not in seen:
                locs.append(f"{p}  (.deb package)")
                seen.add(p)
        # 2. The launcher in $PATH
        import shutil as _sh
        launcher = _sh.which("ttsvoices")
        if launcher and launcher not in seen:
            try:
                src = Path(launcher).resolve()
                if src.is_file() and not src.suffix == ".py":
                    # launcher script — try to read target dir from it
                    txt = src.read_text(errors="ignore")
                    import re as _re
                    m = _re.search(r'cd\s+"([^"]+)"', txt)
                    if m:
                        locs.append(f"{m.group(1)}  (via {launcher})")
                        seen.add(m.group(1))
            except Exception:
                pass
        # 3. The path of the *currently running* ttsvoices.py
        try:
            here = str(Path(_APP_DIR).resolve())
            if here not in seen and Path(here, "ttsvoices.py").is_file():
                locs.append(f"{here}  (currently running)")
                seen.add(here)
        except Exception:
            pass
        # 4. Common dev / portable locations
        for home_p in (Path.home() / "tts_work",
                       Path.home() / "ttsvoices",
                       Path.home() / "Documents" / "tts_work"):
            if (home_p / "ttsvoices.py").is_file() and str(home_p) not in seen:
                locs.append(f"{home_p}  (portable)")
                seen.add(str(home_p))
        if not locs:
            locs.append("(no install found — running from a temporary location)")
        return locs

    def _fetch_release_meta_bg(self, on_done=None):
        """Fetch /releases/latest from GitHub in a background thread.
        Caches into self._last_release_meta. Calls on_done(dict) on the
        main thread if provided."""
        import urllib.request as _ur, json as _json
        def _worker():
            try:
                req = _ur.Request(self._RELEASES_URL, headers={
                    "User-Agent": f"TTSVoices/{__version__}",
                    "Accept": "application/vnd.github+json",
                })
                with _ur.urlopen(req, timeout=8) as r:
                    data = _json.loads(r.read(8192).decode("utf-8", errors="ignore"))
                self._last_release_meta = data
                if on_done:
                    self.root.after(0, lambda: on_done(data))
            except Exception as e:
                if bug_tracker:
                    bug_tracker.warning(f"Release metadata fetch failed: {e}")
                if on_done:
                    self.root.after(0, lambda: on_done({}))
        threading.Thread(target=_worker, daemon=True).start()

    def _show_changelog(self, parent, set_status):
        """Fetch CHANGELOG.md from GitHub main and display in a scrollable
        Toplevel. Falls back to a local CHANGELOG.md if present."""
        win = tk.Toplevel(parent)
        win.title("Changelog — TTS Voices")
        win.configure(bg=C["bg"])
        win.geometry("720x540")
        win.transient(parent)
        win.attributes("-topmost", True)

        hdr = tk.Frame(win, bg=C["surface"]); hdr.pack(fill="x")
        tk.Label(hdr, text="  Changelog",
                 font=("Courier New", 12, "bold"),
                 fg=C["accent2"], bg=C["surface"],
                 padx=14, pady=10).pack(side="left")
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        txt_frame = tk.Frame(win, bg=C["bg"])
        txt_frame.pack(fill="both", expand=True, padx=10, pady=10)
        scr = tk.Scrollbar(txt_frame)
        scr.pack(side="right", fill="y")
        txt = tk.Text(txt_frame, wrap="word", font=("Courier New", 9),
                      bg=C["surface"], fg=C["text"],
                      insertbackground=C["text"],
                      selectbackground=C["accent_dim"],
                      selectforeground="white",
                      relief="flat", padx=10, pady=10,
                      yscrollcommand=scr.set)
        txt.pack(side="left", fill="both", expand=True)
        scr.config(command=txt.yview)
        # Markdown-ish header highlighting
        txt.tag_configure("h1", font=("Courier New", 12, "bold"),
                          foreground=C["accent2"])
        txt.tag_configure("h2", font=("Courier New", 10, "bold"),
                          foreground=C["warning"])
        txt.tag_configure("muted", foreground=C["muted"])
        txt.configure(state="disabled")

        set_status("Fetching changelog from GitHub…", C["text2"])

        def _render(md: str, source: str):
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            for line in md.splitlines():
                if line.startswith("# "):
                    txt.insert("end", line[2:] + "\n", "h1")
                elif line.startswith("## "):
                    txt.insert("end", line[3:] + "\n", "h2")
                else:
                    txt.insert("end", line + "\n")
            txt.insert("end", f"\n\n— loaded from {source}\n", "muted")
            txt.configure(state="disabled")
            set_status(f"Changelog loaded from {source}", C["success"])

        def _worker():
            import urllib.request as _ur
            try:
                req = _ur.Request(self._CHANGELOG_URL, headers={
                    "User-Agent": f"TTSVoices/{__version__}"
                })
                with _ur.urlopen(req, timeout=6) as r:
                    md = r.read(65536).decode("utf-8", errors="ignore")
                if not md.strip():
                    raise RuntimeError("empty response")
                win.after(0, lambda: _render(md, "GitHub"))
            except Exception:
                # Fall back to local CHANGELOG.md
                local = Path(_APP_DIR) / "CHANGELOG.md"
                if local.is_file():
                    md = local.read_text(encoding="utf-8", errors="ignore")
                    win.after(0, lambda: _render(md, "local file"))
                else:
                    def _fail():
                        _render("# Changelog unavailable\n\n"
                                "Could not reach GitHub and no local "
                                "CHANGELOG.md was found.\n",
                                "error")
                        set_status("Changelog unavailable (offline + no "
                                   "local copy)", C["error"])
                    win.after(0, _fail)

        threading.Thread(target=_worker, daemon=True).start()

    def _reinstall_from_deb(self, url: str, filename: str, size: int,
                            parent, set_status, progress):
        """Download a .deb asset from GitHub, then install it via pkexec+dpkg.
        All work happens in background threads; UI is updated via parent.after."""
        import tempfile as _tf, shutil as _sh, subprocess as _sp, urllib.request as _ur
        set_status(f"Downloading {filename}…", C["text2"])
        try:
            progress.pack(side="right", padx=8)
            progress["value"] = 0
        except Exception: pass
        # Download into a temp file
        tmpdir = _tf.mkdtemp(prefix="ttsvoices_update_")
        dest = str(Path(tmpdir) / filename)
        cancel_flag = {"v": False}

        def _dl_worker():
            try:
                req = _ur.Request(url, headers={
                    "User-Agent": f"TTSVoices/{__version__}"
                })
                with _ur.urlopen(req, timeout=30) as r:
                    total = int(r.headers.get("Content-Length") or size or 0)
                    got = 0
                    with open(dest, "wb") as f:
                        while True:
                            if cancel_flag["v"]:
                                raise RuntimeError("cancelled")
                            chunk = r.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            got += len(chunk)
                            if total > 0:
                                pct = min(100, got * 100 / total)
                                parent.after(0, lambda p=pct: progress.configure(value=p))
                parent.after(0, lambda: _on_dl_done(dest))
            except Exception as e:
                err = str(e)
                parent.after(0, lambda: _on_dl_fail(err))

        def _on_dl_done(path):
            try: progress.pack_forget()
            except Exception: pass
            set_status(f"Downloaded {filename}. Verifying…", C["text2"])
            # Sanity: file must be a valid .deb (starts with "!<arch>")
            try:
                with open(path, "rb") as f:
                    magic = f.read(8)
                if not magic.startswith(b"!<arch>"):
                    raise RuntimeError(f"Not a valid .deb file (magic={magic!r})")
            except Exception as e:
                set_status(f"Downloaded file is invalid: {e}", C["error"])
                _sh.rmtree(tmpdir, ignore_errors=True)
                return
            sz = Path(path).stat().st_size
            parent.after(0, lambda: _confirm_install(path, sz))

        def _on_dl_fail(err):
            try: progress.pack_forget()
            except Exception: pass
            set_status(f"Download failed: {err}", C["error"])

        def _confirm_install(path, sz):
            mb = sz / (1024 * 1024)
            msg = (f"Downloaded {filename}  ({mb:.2f} MB)\n\n"
                   "Install it now?  This will:\n"
                   "  • replace /usr/share/ttsvoices/ttsvoices.py\n"
                   "  • run 'sudo dpkg -i' to register with the package manager\n"
                   "  • ask you for your password\n\n"
                   "Continue?")
            if not self._dark_confirm(parent, "Install update", msg):
                set_status("Install cancelled.", C["muted"])
                _sh.rmtree(tmpdir, ignore_errors=True)
                return
            set_status("Installing… (you may be prompted for your password)",
                       C["warning"])
            threading.Thread(target=_install_worker,
                             args=(path,), daemon=True).start()

        def _install_worker(path):
            # Prefer pkexec (graphical sudo prompt) over plain sudo.
            for cmd in (["pkexec", "dpkg", "-i", path],
                        ["sudo", "-A", "dpkg", "-i", path],
                        ["sudo", "dpkg", "-i", path]):
                try:
                    r = _sp.run(cmd, capture_output=True, text=True, timeout=120)
                    parent.after(0, lambda r=r, c=cmd: _on_install_done(r, c))
                    return
                except FileNotFoundError:
                    continue   # this command doesn't exist, try next
                except Exception as e:
                    parent.after(0, lambda e=e: _on_install_fail(str(e)))
                    return
            parent.after(0, lambda: _on_install_fail(
                "No privilege-elevation tool found. Install sudo or pkexec."))

        def _on_install_done(r, cmd):
            if r.returncode == 0:
                set_status(
                    f"Installed successfully. Restart the app to use v"
                    f"{self._last_release_meta.get('tag_name', '?').lstrip('v')}.",
                    C["success"])
                if self._dark_confirm(parent, "Restart now?",
                    "The update is installed.\n\nRestart TTS Voices now "
                    "to load the new version?"):
                    self._restart_app()
            else:
                # dpkg may complain about missing deps — try apt-get -f install
                if "dependency problems" in (r.stderr or "").lower():
                    set_status("Resolving dependencies…", C["warning"])
                    threading.Thread(target=_fix_deps_worker,
                                     daemon=True).start()
                else:
                    set_status(f"Install failed (exit {r.returncode}): "
                               f"{(r.stderr or r.stdout or '').strip()[:200]}",
                               C["error"])

        def _fix_deps_worker():
            for cmd in (["pkexec", "apt-get", "install", "-f", "-y"],
                        ["sudo", "apt-get", "install", "-f", "-y"]):
                try:
                    r = _sp.run(cmd, capture_output=True, text=True, timeout=180)
                    parent.after(0, lambda r=r: (
                        set_status(
                            "Dependencies resolved. Re-run the updater to retry."
                            if r.returncode == 0
                            else f"apt-get -f failed: "
                                 f"{(r.stderr or '').strip()[:200]}",
                            C["success"] if r.returncode == 0 else C["error"]),
                    ))
                    return
                except FileNotFoundError:
                    continue
                except Exception as e:
                    parent.after(0, lambda e=e: set_status(
                        f"apt-get failed: {e}", C["error"]))
                    return

        def _on_install_fail(err):
            set_status(f"Install failed: {err}", C["error"])

        threading.Thread(target=_dl_worker, daemon=True).start()

    def _restart_app(self):
        """Re-exec the current process so the freshly-installed code is
        loaded. The old process exits with code 0."""
        try:
            import sys as _sys, os as _os
            _os.execv(_sys.executable, [_sys.executable] + _sys.argv)
        except Exception as e:
            if bug_tracker:
                bug_tracker.error(f"Restart failed: {e}")
            self._dark_error(self.root, "Restart",
                f"Could not restart automatically:\n{e}\n\n"
                f"Please close and reopen the app manually.")

    def _check_deps_outdated(self):
        """Return list of outdated pip packages using the venv pip.
        Called from the Settings window's dep checker tab.
        Returns list of dicts: [{name, version, latest_version}, ...]
        """
        import subprocess as _sp, json as _json, sys as _sys
        # Always use the current Python's pip — portable, no venv path detection
        try:
            r = _sp.run(
                [_sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
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
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        try: import os as _os; _os.chmod(PLUGINS_DIR, 0o700)
        except Exception: pass
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

        Renders a rich per-core + RAM + disk + network display into the
        subtitle label under the logo. The format is:

          v2.5.1 · ▶0 · CPU ▁▃▅▂▁▃▅▂ 23% · RAM 4.2/7.4G · DSK 73% · ▲12M ▼3M

        The per-core block characters show load distribution at a glance —
        no other app in this category does that. Colour of the whole line
        shifts: accent2 (cyan) at low load → amber at medium → red at high,
        so the user gets a visual cue without any extra widget.
        """
        cpu   = snap["cpu"]
        ram   = snap["ram"]
        ram_used  = snap.get("ram_used", 0)
        ram_total = snap.get("ram_total", 0)
        per_cpu   = snap.get("per_cpu", [])
        disk      = snap.get("disk", 0.0)
        net_up    = snap.get("net_up", 0.0)
        net_down  = snap.get("net_down", 0.0)
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
            lbl_color = "#f87171" if level == "high" else (
                        "#fbbf24" if level == "medium" else C["accent2"])

            # Per-core block visualisation: ▁▂▃▄▅ by load bucket
            _BLOCKS = "▁▂▃▄▅"
            blocks = "".join(
                _BLOCKS[min(len(_BLOCKS)-1, int(p) // 20)]
                for p in per_cpu
            ) if per_cpu else "····"

            # RAM: show "used/total GB" instead of just % for at-a-glance capacity
            if ram_total > 0:
                ram_gb_used  = ram_used  / (1024 ** 3)
                ram_gb_total = ram_total / (1024 ** 3)
                ram_str = f"RAM {ram_gb_used:.1f}/{ram_gb_total:.1f}G"
            else:
                ram_str = f"RAM {ram:.0f}%"

            # Network: human-readable B/K/M/G per second
            def _fmt_rate(bps: float) -> str:
                if bps < 1024:         return f"{int(bps)}B"
                if bps < 1024**2:      return f"{int(bps/1024)}K"
                if bps < 1024**3:      return f"{int(bps/1024**2)}M"
                return f"{bps/1024**3:.1f}G"
            net_str = f"▲{_fmt_rate(net_up)} ▼{_fmt_rate(net_down)}" if (net_up > 0 or net_down > 0) else ""

            # Disk: hide if unavailable or 0% (some chroot envs return 0)
            disk_str = f"DSK {disk:.0f}%" if disk > 0 else ""

            # Assemble the final string
            parts = [
                f"v{__version__}{gen_part}",
                f"CPU {blocks} {cpu:.0f}%",
                ram_str,
            ]
            if disk_str:
                parts.append(disk_str)
            if net_str:
                parts.append(net_str)

            self._subtitle_lbl.configure(
                text="  ·  ".join(parts),
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

    # ══════════════════════════════════════════════════════════════════════════
    #  AUTO-OPTIMIZE — tune settings based on detected hardware
    # ══════════════════════════════════════════════════════════════════════════

    def _optimize_for_hardware(self, silent=False):
        """Auto-tune app settings to match the detected system.  Called once
        at the end of _finish_init on fresh install or when hardware changes.
        Sets cfg keys that the rest of the app already respects."""
        import os as _os

        cpu_count = _os.cpu_count() or 2
        ram_mb = 0
        try:
            with open("/proc/meminfo") as _f:
                for _ln in _f:
                    if _ln.startswith("MemTotal:"):
                        ram_mb = int(_ln.split()[1]) // 1024
                        break
        except Exception:
            ram_mb = 2048  # safe fallback

        # ── Parallel workers ─────────────────────────────────────────────
        parallel = max(1, min(cpu_count - 1, 8))
        if ram_mb < 2048:
            parallel = max(1, cpu_count // 2)

        # ── Chunk size ───────────────────────────────────────────────────
        if ram_mb >= 8192:
            chunk = 8192
        elif ram_mb >= 4096:
            chunk = 4096
        else:
            chunk = 2048

        # ── GPU detection ────────────────────────────────────────────────
        try:
            import voices as _v
            avail = _v.get_available_providers()
        except Exception:
            avail = ("CPUExecutionProvider",)
        best_gpu = None
        for p in ("CUDAExecutionProvider", "ROCMExecutionProvider",
                  "TensorrtExecutionProvider", "DmlExecutionProvider"):
            if p in avail:
                best_gpu = p
                break
        has_gpu = best_gpu is not None

        # ── Build a hardware digest to detect changes ────────────────────
        digest = f"cpu{cpu_count}_ram{ram_mb}_gpu{best_gpu or 'none'}"

        # ── Apply to config ──────────────────────────────────────────────
        old_digest = self.cfg.get("_hw_digest", "")
        if digest == old_digest and not self.cfg.get("_optimized_once"):
            return  # already tuned for this hardware
        changed = digest != old_digest

        self.cfg["parallel_downloads"] = parallel
        self.cfg["chunk_size"]         = chunk
        if has_gpu and self.cfg.get("provider", "CPU") == "CPU":
            self.cfg["provider"] = best_gpu
        elif not has_gpu:
            self.cfg["provider"] = "CPUExecutionProvider"
        self.cfg["_hw_digest"]      = digest
        self.cfg["_optimized_once"] = True

        if not silent:
            gpu_str = best_gpu.replace("ExecutionProvider", "") if best_gpu else "none"
            msg = (f"✓ Optimised for your system: "
                   f"{cpu_count} cores, {ram_mb} MB RAM, GPU: {gpu_str}, "
                   f"{parallel} workers, {chunk} KB chunks")
            self._set_status(msg, C["success"])

    def _rerun_optimization(self):
        """Force re-optimization and show result in the status bar."""
        self.cfg.pop("_hw_digest", None)
        self.cfg.pop("_optimized_once", None)
        self._optimize_for_hardware(silent=False)
        save_config(self.cfg)

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

        # 3. Kill direct audio children by walking our own child PIDs only.
        #    We do NOT call killpg() — if launched from a terminal, killpg()
        #    sends SIGTERM to the entire process group including the user's shell.
        #    Instead, os._exit(0) below guarantees all daemon threads die hard.
        try:
            import audio_handler as _ah2
            if hasattr(_ah2, "_current_proc") and _ah2._current_proc:
                try: _ah2._current_proc.kill()
                except Exception: pass
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

    # ══════════════════════════════════════════════════════════════════════════
    #  LOGO — spiral pinwheel drawn with Canvas primitives (no deps)
    # ══════════════════════════════════════════════════════════════════════════

    _LOGO_COLORS = ("#00d4ff", "#0099cc", "#6600ff", "#cc00ff", "#ff0066")

    @staticmethod
    def _make_logo_canvas(parent, size=140):
        """Draw the TTSVOICES spiral pinwheel on a tkinter Canvas and
        return the Canvas widget.  The Canvas is already packed."""
        bg = getattr(parent, "cget", lambda _: "#000000")("bg")
        cnv = tk.Canvas(parent, width=size, height=size,
                        highlightthickness=0, bg=bg)
        cx = cy = size // 2
        # 5 spiral arms — draw curved pie slices
        n_arms = 5
        for i in range(n_arms):
            start_angle = i * (360 / n_arms) - 90
            hue = TTSVoicesApp._LOGO_COLORS[i]
            dark = TTSVoicesApp._lerp_color(hue, "#000000", 0.35)
            # Outer ring arc
            cnv.create_arc(4, 4, size-4, size-4,
                           start=start_angle - 20,
                           extent=50, fill=dark, outline="")
            # Inner spiral fade
            for ring in range(3, 0, -1):
                frac = ring / 4
                c = TTSVoicesApp._lerp_color(hue, bg, frac)
                margin = int(frac * cx) + 2
                cnv.create_arc(margin, margin, size-margin, size-margin,
                               start=start_angle + 5 * (1 - frac),
                               extent=40 + 45 * frac,
                               fill=c, outline="",
                               tags="arm")
        # Center hub
        cnv.create_oval(cx-8, cy-8, cx+8, cy+8,
                        fill="#0a0a1a", outline="")
        # Glow ring
        cnv.create_oval(cx-18, cy-18, cx+18, cy+18,
                        fill="", outline="#6600ff", width=1, dash=(2, 3))
        # Outer ring
        cnv.create_oval(2, 2, size-2, size-2,
                        fill="", outline=TTSVoicesApp._LOGO_COLORS[0],
                        width=1, dash=(4, 4), tags="outer")
        return cnv

    @staticmethod
    def _lerp_color(a: str, b: str, t: float) -> str:
        """Linearly interpolate two hex colours.  0 → a, 1 → b."""
        t = max(0, min(1, t))
        ra = int(a[1:3], 16); ga = int(a[3:5], 16); ba = int(a[5:7], 16)
        rb = int(b[1:3], 16); gb = int(b[3:5], 16); bb = int(b[5:7], 16)
        return f"#{int(ra+(rb-ra)*t):02x}{int(ga+(gb-ga)*t):02x}{int(ba+(bb-ba)*t):02x}"


    def _show_about_dialog(self):
        """About dialog displaying the logo, version, and credits."""
        win = tk.Toplevel(self.root)
        win.title(f"About TTS Voices")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        win.attributes("-topmost", True)

        # Header with logo
        hdr = tk.Frame(win, bg=C["surface"], pady=14); hdr.pack(fill="x")
        cnv = self._make_logo_canvas(hdr, size=90)
        cnv.pack(pady=(0, 6))

        tk.Label(hdr, text="TTS VOICES",
                 font=("Segoe UI", 16, "bold"),
                 fg=C["accent2"], bg=C["surface"]).pack(pady=(4, 0))
        tk.Label(hdr, text="UNLIMITED TEXT-TO-SPEECH",
                 font=("Segoe UI", 8, "bold"),
                 fg=C["text2"], bg=C["surface"]).pack()
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")

        body = tk.Frame(win, bg=C["bg"], padx=28, pady=14); body.pack(fill="x")
        info = [
            ("Version",     f"v{__version__}  ({VERSION_DATE})"),
            ("Engine",      "Kokoro ONNX  ·  espeak-ng  ·  Edge TTS (cloud)"),
            ("License",     "MIT  —  Free and open-source"),
            ("Repository",  "github.com/jspgamer0503-coder/TTSVoices"),
            ("Author",      "JSP Gamer  (jsp.gamer0503@gmail.com)"),
            ("Maintenance", "opencode AI assistant"),
        ]
        for i, (k, v) in enumerate(info):
            tk.Label(body, text=k, font=("Courier New", 9, "bold"),
                     fg=C["text2"], bg=C["bg"]).grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(body, text=v, font=("Courier New", 9),
                     fg=C["text"], bg=C["bg"], anchor="w"
                     ).grid(row=i, column=1, sticky="w", pady=2, padx=(12, 0))
        tk.Frame(win, bg=C["border"], height=1).pack(fill="x")
        foot = tk.Frame(win, bg=C["surface2"], pady=10); foot.pack(fill="x")
        def _do_optimize():
            self._rerun_optimization()
            self._set_status("Hardware optimisation complete. Restart app to apply fully.",
                             C["success"])
            win.destroy()
        tk.Button(foot, text="  ⚡ Auto-tune  ",
                  font=("Courier New", 9), bg=C["accent"], fg="white",
                  relief="flat", padx=10, pady=6, cursor="hand2",
                  activebackground=C["accent_dim"],
                  command=_do_optimize).pack(side="left", padx=(12, 4))
        tk.Button(foot, text="  Close  ",
                  font=("Courier New", 9), bg=C["surface"], fg=C["muted"],
                  relief="flat", padx=14, pady=6, cursor="hand2",
                  command=win.destroy).pack(side="right", padx=(4, 12))
        win.update_idletasks()
        sw = self.root.winfo_screenwidth(); sh = self.root.winfo_screenheight()
        ww = max(win.winfo_reqwidth(), 420); wh = win.winfo_reqheight()
        win.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")
        win.grab_set()

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
