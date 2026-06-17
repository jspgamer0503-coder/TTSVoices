#!/usr/bin/env python3
"""
build_audio_fast.py — Compile audio_fast.c into audio_fast.so

Maintained by the opencode AI assistant — see README.md.
Run this script once during install to enable the C-accelerated
WAV concatenation. Falls back gracefully if gcc is not available.
"""
import subprocess, os, sys
from pathlib import Path

APP_DIR  = Path(__file__).parent.resolve()
SRC      = APP_DIR / "audio_fast.c"
OUT      = APP_DIR / "audio_fast.so"

def build():
    if not SRC.exists():
        print(f"  ⚠  audio_fast.c not found — skipping C build")
        return False

    if OUT.exists():
        # Check if source is newer
        if SRC.stat().st_mtime <= OUT.stat().st_mtime:
            print(f"  ✓  audio_fast.so already up to date")
            return True

    print("  Compiling audio_fast.c …")
    try:
        r = subprocess.run(
            ["gcc", "-O2", "-shared", "-fPIC", "-o", str(OUT), str(SRC), "-lm"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            print(f"  ✓  audio_fast.so compiled ({OUT.stat().st_size // 1024} KB)")
            return True
        else:
            print(f"  ⚠  gcc failed: {r.stderr.strip()[:120]}")
            return False
    except FileNotFoundError:
        print("  ⚠  gcc not found — install with: sudo apt install gcc")
        return False
    except Exception as e:
        print(f"  ⚠  Build error: {e}")
        return False

if __name__ == "__main__":
    ok = build()
    sys.exit(0 if ok else 1)
