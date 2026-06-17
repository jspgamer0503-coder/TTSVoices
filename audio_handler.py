"""
TTS Voices 2.5.2 - Audio Handler (Lightweight Edition)

Maintained by the opencode AI assistant — see README.md.
Uses system audio tools (aplay/paplay/ffplay) — no pygame dependency.
Zero import overhead, ~0MB extra RAM vs pygame's 30-60MB.
"""
import os
import io
import wave
import subprocess
import tempfile
import threading
import time
import atexit
import bug_tracker

# ── Playback state ────────────────────────────────────────────────────────────
_current_volume = 0.63      # 0.0–1.0
_stop_event     = threading.Event()
_play_lock      = threading.Lock()
_current_proc   = None      # active subprocess for CLI playback
_poll_sleep     = threading.Event()  # reusable wait object for polling loops

# ── Playback timing for synchronized word highlighting ────────────────────────
_on_playback_start  = None   # callback: fired when audio actually starts
_on_playback_stop   = None   # callback: fired when audio stops
_playback_start_time = 0.0   # monotonic time when current chunk started

def set_callbacks(on_start=None, on_stop=None):
    """Register callbacks for playback timing (used by highlight sync)."""
    global _on_playback_start, _on_playback_stop
    _on_playback_start = on_start
    _on_playback_stop  = on_stop

def get_playback_position() -> float:
    """Return elapsed seconds since current chunk started playing."""
    global _playback_start_time
    if _current_proc is None or _current_proc.poll() is not None:
        return 0.0
    return time.monotonic() - _playback_start_time

def set_volume_level(volume_0_to_32767: int):
    global _current_volume
    _current_volume = max(0.0, min(1.0, volume_0_to_32767 / 32767.0))

def _apply_volume_to_wav(wav_data: bytes) -> bytes:
    """Scale PCM samples by _current_volume.

    Speed priority:
      1. C extension (audio_fast.so apply_volume) — fastest, in-place
      2. numpy vectorised                          — fast
      3. array module loop                         — pure Python fallback
    """
    if _current_volume >= 0.99:
        return wav_data
    try:
        with wave.open(io.BytesIO(wav_data)) as wf:
            ch  = wf.getnchannels()
            sw  = wf.getsampwidth()
            sr  = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())

        if sw != 2:
            return wav_data   # Only handle 16-bit PCM

        gain = float(_current_volume)

        # ── Path 1: C extension ───────────────────────────────────────────
        lib = _load_audio_fast()
        if lib:
            import ctypes
            buf = (ctypes.c_int16 * (len(pcm) // 2)).from_buffer_copy(pcm)
            lib.apply_volume(buf, len(buf), ctypes.c_float(gain))
            scaled_pcm = bytes(buf)
        else:
            # ── Path 2: numpy ─────────────────────────────────────────────
            try:
                import numpy as np
                samples    = np.frombuffer(pcm, dtype=np.int16).copy()
                scaled     = np.round(samples * gain).clip(-32768, 32767).astype(np.int16)
                scaled_pcm = scaled.tobytes()
            except ImportError:
                # ── Path 3: pure Python array ─────────────────────────────
                import array as arr
                samples = arr.array('h', pcm)
                for i in range(len(samples)):
                    samples[i] = max(-32768, min(32767, int(samples[i] * gain)))
                scaled_pcm = samples.tobytes()

        buf2 = io.BytesIO()
        with wave.open(buf2, 'wb') as wf:
            wf.setnchannels(ch)
            wf.setsampwidth(sw)
            wf.setframerate(sr)
            wf.writeframes(scaled_pcm)
        return buf2.getvalue()
    except Exception as _vole:
        bug_tracker.warning(f"Volume scaling failed, returning unscaled audio: {_vole}")
        return wav_data

def begin_session():
    """
    Reset the stop flag at the start of a new speak session.

    Must be called from _on_speak() BEFORE launching the worker thread.
    Separating this from play_wav() closes the race condition where
    stop_playback() sets _stop_event between the worker's _stop_flag check
    and the next play_wav() call — which previously wiped the stop signal
    and caused the voice to repeat despite the user pressing Stop.
    """
    _stop_event.clear()


def play_wav(wav_data: bytes) -> bool:
    """Play WAV bytes. Uses aplay → paplay → ffplay in order."""
    # NOTE: _stop_event is NO LONGER cleared here.  It is cleared exactly once
    # per session by begin_session(), called from _on_speak() before the worker
    # thread starts.  Clearing it inside play_wav() created a race where
    # stop_playback() could be wiped mid-loop, causing repeated/unstoppable audio.
    if _stop_event.is_set():
        return False   # Stop was pressed before this chunk — honour it immediately
    scaled = _apply_volume_to_wav(wav_data)

    # Write to temp file (all CLI tools need a file path)
    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    atexit.register(lambda p=tmp.name: os.path.exists(p) and os.unlink(p))
    try:
        tmp.write(scaled)
        tmp.flush()
        tmp.close()
        return _play_file(tmp.name)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


# ── Audio backend — probed once at first playback ────────────────────────────
_probed_backend = None   # the single backend that worked, or None to re-probe

def _probe_backends() -> list:
    """
    Return ordered list of playback commands to try.
    Detects PipeWire / PulseAudio / ALSA at runtime.
    """
    import shutil
    def has(cmd): return shutil.which(cmd) is not None

    ordered = []
    # pw-play — native PipeWire, zero ALSA conflicts
    if has("pw-play"):
        ordered.append(["pw-play", "--target=auto"])
    # paplay — PulseAudio / PipeWire compat layer
    if has("paplay"):
        ordered.append(["paplay"])
    # aplay with large buffer (prevents exit-code 1 on some PipeWire setups)
    if has("aplay"):
        ordered.append(["aplay", "-q", "--buffer-size=131072"])
        ordered.append(["aplay", "-q"])
    # ffplay as last resort
    if has("ffplay"):
        ordered.append(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"])
    return ordered


def _play_file(path: str) -> bool:
    """Try audio backends in order until one works. Remembers the winner."""
    global _current_proc, _probed_backend

    # Verify the file has audio frames before attempting playback
    try:
        with wave.open(path, 'rb') as _wf:
            _frames = _wf.getnframes()
            _sr     = _wf.getframerate()
        if _frames == 0:
            bug_tracker.error("play_wav: WAV has 0 frames — synthesis may have failed")
            return False
        bug_tracker.info(f"play_wav: {_frames} frames @ {_sr}Hz = {_frames/_sr:.2f}s")
    except Exception as _e:
        bug_tracker.warning(f"play_wav: could not verify WAV: {_e}")

    # If we already know a working backend, use it directly (skip probe overhead)
    if _probed_backend is not None:
        cmd = _probed_backend + [path]
        ok = _run_backend(cmd)
        if ok is True:
            return True
        if ok == -1:
            return False
        if ok is False and _stop_event.is_set():
            return False
        # Backend broke (not stopped) — fall through to re-probe
        _probed_backend = None
        bug_tracker.warning(f"Cached backend failed, re-probing...")

    # Probe: try each backend in order, remember the first that works
    backends = _probe_backends()
    if not backends:
        bug_tracker.error("No audio playback tool found. Install one: pw-play, paplay, aplay, or ffplay")
        return False

    for base_cmd in backends:
        cmd  = base_cmd + [path]
        tool = base_cmd[0]
        ok = _run_backend(cmd)
        if ok is True:
            # This backend worked — cache it for all future chunks
            _probed_backend = base_cmd
            bug_tracker.info(f"Audio backend selected: {tool}")
            return True
        elif ok == -1:
            return False
        elif ok is False and _stop_event.is_set():
            return False   # User stopped — don't try more backends
        elif ok is False:
            bug_tracker.warning(f"Audio backend {tool} failed — trying next")
            continue
        # ok is None means not attempted (shouldn't happen here)

    bug_tracker.error(f"All audio backends failed. Install pw-play, paplay, or ffplay.")
    return False


def _run_backend(cmd: list):
    """
    Run a single playback command. Returns:
      True   — played successfully
      False  — failed (bad exit code, exception, or user stopped)
      None   — tool not found
      -1     — busy (another playback is already running)
    """
    global _current_proc
    import shutil
    if not shutil.which(cmd[0]):
        return None

    try:
        with _play_lock:
            if _stop_event.is_set():
                return False
            if _current_proc is not None and _current_proc.poll() is None:
                return -1
            proc = subprocess.Popen(cmd,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            _current_proc = proc
            _poll_sleep.clear()

        global _playback_start_time
        _playback_start_time = time.monotonic()
        if _on_playback_start:
            try: _on_playback_start()
            except Exception: pass

        while proc.poll() is None:
            if _stop_event.is_set():
                proc.terminate()
                try: proc.wait(timeout=1)
                except subprocess.TimeoutExpired: proc.kill()
                _current_proc = None
                return False
            _poll_sleep.wait(0.05)

        _current_proc = None
        if _on_playback_stop:
            try: _on_playback_stop()
            except Exception: pass

        return proc.returncode == 0

    except Exception as e:
        bug_tracker.warning(f"Backend {cmd[0]} exception: {e}")
        return False


def stop_playback():
    """
    Signal current playback to stop. Does NOT clear the flag — the flag is
    only cleared by _play_file() when starting a new playback cycle.
    """
    global _current_proc
    with _play_lock:
        _stop_event.set()
        _poll_sleep.set()
        proc = _current_proc
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass


# ── System mixer for real-time volume control ─────────────────────────────────
_sys_volume_pct   = 100    # last value sent to system mixer
_sys_volume_lock  = threading.Lock()
_sys_volume_event = threading.Event()

def _sys_volume_worker():
    while True:
        _sys_volume_event.wait()
        _sys_volume_event.clear()
        with _sys_volume_lock:
            pct = _sys_volume_pct
        try:
            r = subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
                capture_output=True, timeout=2)
            if r.returncode == 0:
                continue
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        try:
            subprocess.run(
                ["amixer", "set", "Master", f"{pct}%"],
                capture_output=True, timeout=2)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

threading.Thread(target=_sys_volume_worker, daemon=True).start()

def set_system_volume(pct: int):
    """Set system output volume in real-time via pactl or amixer.
    Called from the UI thread whenever the Volume slider changes.
    pct: 0-100
    """
    global _sys_volume_pct
    pct = max(0, min(100, pct))
    with _sys_volume_lock:
        if pct == _sys_volume_pct:
            return
        _sys_volume_pct = pct
    _sys_volume_event.set()


_audio_fast_lib  = None   # ctypes CDLL, or False if unavailable
_audio_fast_tried = False

def _load_audio_fast():
    global _audio_fast_lib, _audio_fast_tried
    if _audio_fast_tried:
        return _audio_fast_lib
    _audio_fast_tried = True
    try:
        import ctypes, ctypes.util
        from pathlib import Path as _Path
        so = _Path(__file__).resolve().parent / "audio_fast.so"
        if so.exists():
            lib = ctypes.CDLL(str(so))
            # concat_wavs signature
            lib.concat_wavs.restype  = ctypes.c_int
            lib.concat_wavs.argtypes = [
                ctypes.POINTER(ctypes.c_char_p),   # chunks (array of byte buffers)
                ctypes.POINTER(ctypes.c_uint32),   # chunk_sizes
                ctypes.c_int,                       # n
                ctypes.POINTER(ctypes.c_char_p),   # out_buf
                ctypes.POINTER(ctypes.c_uint32),   # out_size
            ]
            lib.free_buf.restype  = None
            lib.free_buf.argtypes = [ctypes.c_char_p]
            lib.apply_volume.restype  = None
            lib.apply_volume.argtypes = [
                ctypes.POINTER(ctypes.c_int16),
                ctypes.c_uint32,
                ctypes.c_float,
            ]
            _audio_fast_lib = lib
            bug_tracker.info("audio_fast.so loaded — C-accelerated WAV concat active")
        else:
            _audio_fast_lib = False
    except Exception as e:
        bug_tracker.warning(f"audio_fast.so load failed: {e}")
        _audio_fast_lib = False
    return _audio_fast_lib


def _export_wav_c(valid_chunks: list, output_path: str) -> bool:
    """C-accelerated WAV concatenation via audio_fast.so."""
    import ctypes
    lib = _load_audio_fast()
    if not lib:
        return False
    try:
        n  = len(valid_chunks)
        # Build ctypes arrays
        bufs  = (ctypes.c_char_p  * n)(*[ctypes.c_char_p(c) for c in valid_chunks])
        sizes = (ctypes.c_uint32 * n)(*[len(c) for c in valid_chunks])
        out_buf  = ctypes.c_char_p(None)
        out_size = ctypes.c_uint32(0)
        ret = lib.concat_wavs(bufs, sizes, n,
                              ctypes.byref(out_buf),
                              ctypes.byref(out_size))
        if ret != 0:
            bug_tracker.warning(f"concat_wavs returned {ret} — falling back to Python")
            return False
        data = ctypes.string_at(out_buf, out_size.value)
        lib.free_buf(out_buf)
        with open(output_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        bug_tracker.warning(f"C export failed: {e} — falling back to Python")
        return False


def export_wav(wav_chunks: list, output_path: str, progress_cb=None) -> bool:
    """Concatenate WAV chunks into a single output file.
    Uses C-accelerated concat when audio_fast.so is available (10-15x faster).
    progress_cb(i) is called after each chunk is processed (optional).
    """
    if not wav_chunks:
        bug_tracker.warning("export_wav: empty chunk list")
        return False
    try:
        valid_chunks = [c for c in wav_chunks if c is not None]
        if not valid_chunks:
            return False

        # ── Fast path: C extension ──────────────────────────────────────────
        if _export_wav_c(valid_chunks, output_path):
            if progress_cb:
                for i in range(len(valid_chunks)):
                    try: progress_cb(i)
                    except Exception: pass
            return True

        # ── Fallback: pure Python ───────────────────────────────────────────
        parts       = []
        sample_rate = None
        channels    = 1
        sampwidth   = 2
        for idx, chunk in enumerate(valid_chunks):
            try:
                with wave.open(io.BytesIO(chunk)) as wf:
                    sr  = wf.getframerate()
                    ch  = wf.getnchannels()
                    sw  = wf.getsampwidth()
                    if sample_rate is None:
                        sample_rate = sr
                        channels    = ch
                        sampwidth   = sw
                    elif sr != sample_rate or ch != channels or sw != sampwidth:
                        bug_tracker.warning(
                            f"Skipping chunk {idx}: format mismatch "
                            f"(sr={sr} vs {sample_rate}, ch={ch} vs {channels}, sw={sw} vs {sampwidth})"
                        )
                        continue
                    parts.append(wf.readframes(wf.getnframes()))
            except Exception as e:
                bug_tracker.warning(f"Skipping corrupt chunk: {e}")
            if progress_cb:
                try: progress_cb(idx)
                except Exception: pass
        if not parts:
            return False
        all_data = b"".join(parts)
        sample_rate = sample_rate or 24000
        with wave.open(output_path, 'wb') as out:
            out.setnchannels(channels)
            out.setsampwidth(sampwidth)
            out.setframerate(sample_rate)
            out.writeframes(all_data)
        return True
    except PermissionError as e:
        bug_tracker.error(f"WAV export permission denied: {e}")
        return False
    except OSError as e:
        bug_tracker.error(f"WAV export OS error: {e}")
        return False
    except Exception as e:
        bug_tracker.error(f"WAV export failed: {e}")
        return False


def export_mp3(wav_chunks: list, output_path: str, progress_cb=None) -> bool:
    """Export to MP3 using ffmpeg.
    progress_cb(i) is called per WAV chunk during the write stage (optional).
    Temp WAV file is always cleaned up via try/finally — no leaks on crash/exit.
    """
    import tempfile as _tf
    # Use NamedTemporaryFile instead of deprecated mktemp — delete=False so we
    # control cleanup ourselves (mktemp races between creation and use on Linux).
    with _tf.NamedTemporaryFile(suffix='_tts_export.wav', delete=False) as _f:
        tmp = _f.name
    try:
        if not export_wav(wav_chunks, tmp, progress_cb=progress_cb):
            return False
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp,
             "-codec:a", "libmp3lame", "-qscale:a", "2", output_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600
        )
        return result.returncode == 0
    except Exception as e:
        bug_tracker.error(f"MP3 export failed: {e}")
        return False
    finally:
        # Always remove temp WAV regardless of success, exception, or process kill
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass

def set_volume(vol_percent: int):
    """Legacy shim: accepts 0-100."""
    set_volume_level(int(round(vol_percent * 327.67)))
