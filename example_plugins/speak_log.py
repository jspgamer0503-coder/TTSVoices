"""
TTS Voices Plugin — Speak Log

Maintained by the opencode AI assistant — see README.md.
Logs a timestamped entry to ~/.ttsvoices/speak_log.txt every time
speech starts or stops. Useful for tracking usage.

Install: copy this file to ~/.ttsvoices/plugins/speak_log.py
"""

from pathlib import Path
import time

LOG_FILE = Path.home() / ".ttsvoices" / "speak_log.txt"
_app = None
_start_time = None


def register(app):
    global _app
    _app = app
    app.on_speak_start(_on_start)
    app.on_speak_stop(_on_stop)


def _on_start():
    global _start_time
    _start_time = time.time()
    _write(f"START  {time.strftime('%Y-%m-%d %H:%M:%S')}")


def _on_stop():
    duration = ""
    if _start_time:
        secs = int(time.time() - _start_time)
        duration = f"  ({secs}s)"
    _write(f"STOP   {time.strftime('%Y-%m-%d %H:%M:%S')}{duration}")


def _write(line: str):
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
