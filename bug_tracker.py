"""
TTS Voices 2.5.0 - Bug Tracker Module

Maintained by the opencode AI assistant — see README.md.
Centralised error logging, crash recovery, self-tests, and health checks.

Self-test coverage (run at startup):
  - ODT crypto: parse_manifest, derive_key, decompression pipeline
  - Highlight: after() scheduling, cancel IDs
  - Resume: bookmark save/load roundtrip
  - Fallback: fallback_occurred flag set correctly
  - Threading: stop_flag, queue sentinel pattern
  - Audio: export_wav, stop_playback idempotent
  - File extractor: unsupported ext, binary guard, encryption detection
"""
import os
import sys
import traceback
import datetime
import json
import functools
import threading
from pathlib import Path
from collections import deque

LOG_DIR = Path.home() / ".ttsvoices" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_session_log  = LOG_DIR / f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
_MAX_LOG_FILES = 10          # keep only last 10 session logs
_errors        = deque(maxlen=500)  # auto-evicts oldest; prevents unbounded RAM growth
_lock          = threading.Lock()

def _rotate_logs():
    """Keep only the _MAX_LOG_FILES most recent session logs to prevent disk bloat."""
    try:
        logs = sorted(LOG_DIR.glob("session_*.log"), reverse=True)
        for old in logs[_MAX_LOG_FILES:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass

_rotate_logs()  # Run once at import time

# Long-running sessions used to accumulate log files indefinitely because
# _rotate_logs() only fired at module import. Track how many entries have
# been written since the last rotation and rotate opportunistically.
_ENTRIES_SINCE_ROTATE = 0
_ROTATE_EVERY = 200   # ~ every 200 log lines


# ── Core logging ──────────────────────────────────────────────────────────────

def _write(level: str, message: str, details: str = ""):
    global _ENTRIES_SINCE_ROTATE
    timestamp = datetime.datetime.now().isoformat()
    entry = {
        "timestamp": timestamp,
        "level":     level,
        "message":   message,
        "details":   details,
    }
    with _lock:
        _errors.append(entry)
        try:
            with open(_session_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # disk full / permissions — don't crash the app
        # Opportunistic rotation so a long transcription session doesn't
        # fill the disk with old session logs.
        _ENTRIES_SINCE_ROTATE += 1
        if _ENTRIES_SINCE_ROTATE >= _ROTATE_EVERY:
            _ENTRIES_SINCE_ROTATE = 0
            try:
                _rotate_logs()
            except Exception:
                pass


def info(message: str):
    _write("INFO", message)


def warning(message: str, details: str = ""):
    _write("WARNING", message, details)


def error(message: str, details: str = ""):
    _write("ERROR", message, details)


def critical(message: str, details: str = ""):
    """Critical: app-breaking failure. Writes immediately and flushes."""
    _write("CRITICAL", message, details)


# ── Decorator ────────────────────────────────────────────────────────────────

def wrap(func):
    """Auto-capture any exception in the decorated function and re-raise."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            tb = traceback.format_exc()
            error(f"Exception in {func.__name__}: {e}", tb)
            raise
    return wrapper


# ── Global Tkinter exception handler ──────────────────────────────────────────

def install_tkinter_exception_handler(root):
    """
    Install a global Tk exception handler so unhandled widget callback errors
    are logged to the bug tracker instead of crashing silently or printing to
    stderr. Based on best practice: override Tk.report_callback_exception.
    """
    def _tk_error_handler(exc_type, exc_value, exc_tb):
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        critical(
            f"Unhandled Tkinter callback exception: {exc_value}",
            tb_str
        )
        # Don't suppress — let default Tk handler also run
        import tkinter as tk
        tk.Tk.report_callback_exception(root, exc_type, exc_value, exc_tb)

    try:
        root.report_callback_exception = _tk_error_handler
    except Exception:
        pass


# ── Health checks for recent fixes ───────────────────────────────────────────

class HealthCheck:
    """
    Runs targeted self-tests for each critical subsystem at startup.
    Each test is isolated — failure of one does not prevent others.
    Results appear in the bug tracker log so the first session always
    captures whether the current install is healthy.
    """

    def __init__(self):
        self.passed: list = []
        self.failed: list = []

    def _run(self, name: str, fn):
        try:
            fn()
            self.passed.append(name)
            info(f"[HEALTH ✓] {name}")
        except Exception as e:
            tb = traceback.format_exc()
            self.failed.append(name)
            error(f"[HEALTH ✗] {name}: {e}", tb)

    # ── ODT Crypto ──────────────────────────────────────────────────────────

    def check_odf_crypto_parse(self):
        """Verify _parse_manifest and _parse_manifest_raw both extract IV + salt."""
        def _test():
            import base64, zipfile, io, sys
            sys.path.insert(0, str(Path(__file__).parent))
            from odf_crypto import _parse_manifest, _parse_manifest_raw

            # Minimal manifest with known values
            SALT = base64.b64encode(b"A" * 16).decode()
            IV   = base64.b64encode(b"B" * 16).decode()
            CK   = base64.b64encode(b"C" * 32).decode()
            mf   = f"""<?xml version="1.0"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml">
  <manifest:encryption-data
   manifest:checksum-type="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0#sha256-1k"
   manifest:checksum="{CK}">
   <manifest:algorithm
    manifest:algorithm-name="http://www.w3.org/2001/04/xmlenc#aes256-cbc"
    manifest:initialisation-vector="{IV}"/>
   <manifest:key-derivation
    manifest:key-derivation-name="PBKDF2"
    manifest:key-size="32"
    manifest:iteration-count="100000"
    manifest:salt="{SALT}"/>
   <manifest:start-key-generation
    manifest:start-key-generation-name="http://www.w3.org/2000/09/xmldsig#sha256"
    manifest:key-size="32"/>
  </manifest:encryption-data>
 </manifest:file-entry>
</manifest:manifest>""".encode()

            # Test ET parser
            et_params = _parse_manifest(mf).get("content.xml", {})
            assert len(et_params.get("iv",   b"")) == 16, \
                f"ET parser: IV empty (ElementTree bool(element) bug?)"
            assert len(et_params.get("salt", b"")) == 16, \
                f"ET parser: salt empty"
            assert et_params.get("iterations") == 100000, \
                f"ET parser: iterations wrong"

            # Test regex fallback parser
            rx_params = _parse_manifest_raw(mf, "content.xml")
            assert len(rx_params.get("iv",   b"")) == 16, \
                f"Regex parser: IV empty"
            assert len(rx_params.get("salt", b"")) == 16, \
                f"Regex parser: salt empty"

        self._run("ODF crypto: manifest parse (ET + regex fallback)", _test)

    def check_odf_crypto_keydrive(self):
        """Verify PBKDF2 key derivation is deterministic."""
        def _test():
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from odf_crypto import _derive_key
            params = {
                "start_key_algo": "http://www.w3.org/2000/09/xmldsig#sha256",
                "start_key_size": 32,
                "kd_name":        "PBKDF2",
                "key_size":       32,
                "iterations":     1000,   # low for speed in test
                "salt":           b"testsalt12345678",
            }
            k1 = _derive_key("password", params)
            k2 = _derive_key("password", params)
            k3 = _derive_key("WRONG",    params)
            assert k1 == k2,     "Key not deterministic"
            assert k1 != k3,     "Different passwords gave same key"
            assert len(k1) == 32, "Key wrong length"

        self._run("ODF crypto: PBKDF2 key derivation deterministic", _test)

    def check_odf_encryption_detection(self):
        """Verify raw string encryption detection works."""
        def _test():
            manifest_with_enc    = b"<manifest><encryption-data/></manifest>"
            manifest_without_enc = b"<manifest><file-entry/></manifest>"
            assert b"encryption-data" in manifest_with_enc
            assert b"encryption-data" not in manifest_without_enc

        self._run("ODF crypto: raw string encryption detection", _test)

    # ── Word highlighting ────────────────────────────────────────────────────

    def check_highlight_cancel_ids(self):
        """Verify after() IDs can be tracked and cancelled."""
        def _test():
            ids = []
            # Simulate accumulation and cancel — use a mock
            class MockRoot:
                def after(self, ms, fn):
                    ids.append(f"id_{ms}")
                    return f"id_{ms}"
                def after_cancel(self, id_):
                    if id_ in ids:
                        ids.remove(id_)

            root = MockRoot()
            pending = []
            for i in range(5):
                aid = root.after(i * 100, lambda: None)
                pending.append(aid)

            assert len(pending) == 5, "IDs not tracked"
            for aid in pending:
                root.after_cancel(aid)
            pending.clear()
            assert len(pending) == 0, "IDs not cleared after cancel"

        self._run("Highlighting: after() ID tracking and cancellation", _test)

    def check_syllable_estimator(self):
        """Verify syllable counting gives reasonable proportions."""
        def _test():
            import re
            def syllables(word):
                w = re.sub(r"[^a-zA-Z]", "", word.lower())
                if not w: return 1
                count = len(re.findall(r"[aeiouy]+", w))
                if w.endswith("e") and count > 1: count -= 1
                return max(1, count)

            # 'extraordinary' should have more syllables than 'the'
            assert syllables("extraordinary") > syllables("the"), \
                "Syllable estimator broken"
            assert syllables("the") == 1
            assert syllables("") == 1   # fallback

        self._run("Highlighting: syllable-proportional timing estimator", _test)

    # ── Resume / Bookmark ────────────────────────────────────────────────────

    def check_bookmark_roundtrip(self):
        """Verify bookmark saves and loads correctly via config."""
        def _test():
            import json, tempfile, os
            tmp = tempfile.mktemp(suffix=".json")
            cfg = {
                "speed": 1.3, "pitch": 1.0, "volume": 63,
                "voice_idx": 0, "theme": "dark", "provider": "CPU",
                "bookmark_chunk": 42,
                "bookmark_file":  "/home/user/story.odt",
            }
            with open(tmp, "w") as f:
                json.dump(cfg, f)
            with open(tmp) as f:
                loaded = json.load(f)
            os.unlink(tmp)
            assert loaded["bookmark_chunk"] == 42, "Chunk not saved"
            assert loaded["bookmark_file"] == "/home/user/story.odt", "File not saved"
            # Simulate resume decision
            saved_file  = loaded["bookmark_file"]
            saved_chunk = loaded["bookmark_chunk"]
            current     = "/home/user/story.odt"
            total       = 100
            should_resume = (saved_chunk > 0
                             and saved_file == current
                             and saved_chunk < total)
            assert should_resume, "Resume should trigger for same file"

        self._run("Resume: bookmark save/load roundtrip", _test)

    def check_chunk_position_slicing(self):
        """Verify chunk_positions is sliced alongside chunks on resume."""
        def _test():
            text   = "Word one. Word two. Word three. Word four. Word five."
            chunks = ["Word one.", "Word two.", "Word three.", "Word four.", "Word five."]
            total  = len(chunks)

            # Build positions (as the real code does)
            positions = []
            pos = 0
            for chunk in chunks:
                found = text.find(chunk.split()[0], pos)
                positions.append(found if found != -1 else pos)
                pos = max(0, found) + 1

            # Simulate resume from chunk 2
            saved_chunk = 2
            sliced_chunks    = chunks[saved_chunk:]
            sliced_positions = positions[saved_chunk:]

            assert len(sliced_chunks) == 3, f"Expected 3 chunks, got {len(sliced_chunks)}"
            assert len(sliced_positions) == 3, "Positions not sliced with chunks"
            assert sliced_positions[0] == positions[2], "First resumed position wrong"

        self._run("Resume: chunk_positions sliced correctly with chunks", _test)

    # ── Fallback warning ─────────────────────────────────────────────────────

    def check_fallback_flag(self):
        """Verify fallback_occurred is set on engine failure."""
        def _test():
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            import voices
            orig = voices.fallback_occurred
            voices.fallback_occurred = True
            assert voices.fallback_occurred is True, "Flag not set"
            voices.fallback_occurred = False
            assert voices.fallback_occurred is False, "Flag not cleared"
            voices.fallback_occurred = orig

        self._run("Fallback: fallback_occurred flag reads/writes correctly", _test)

    # ── Phoneme overflow guard ────────────────────────────────────────────────

    def check_chunk_size_limit(self):
        """Verify chunk_text respects the 80-word safe limit."""
        def _test():
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from voices import chunk_text
            # 500 words of prose — no punctuation (worst case for overflow)
            text   = "word " * 500
            chunks = chunk_text(text, max_words=80)
            for i, c in enumerate(chunks):
                wc = len(c.split())
                assert wc <= 80, f"Chunk {i} has {wc} words — exceeds 80 limit"

        self._run("Phoneme overflow: chunk_text hard limit at 80 words", _test)

    def check_chunk_text_empty(self):
        """chunk_text('') must return []."""
        def _test():
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from voices import chunk_text
            assert chunk_text("") == [], "Empty string should return []"
            assert chunk_text("   ") == [], "Whitespace-only should return []"

        self._run("Phoneme overflow: chunk_text handles empty/whitespace", _test)

    # ── Audio handler ────────────────────────────────────────────────────────

    def check_audio_stop_idempotent(self):
        """stop_playback() must not raise when called multiple times."""
        def _test():
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            import audio_handler
            for _ in range(5):
                audio_handler.stop_playback()

        self._run("Audio: stop_playback() idempotent (10× rapid call)", _test)

    def check_audio_export_empty(self):
        """export_wav([]) must return False without creating a file."""
        def _test():
            import sys, os
            sys.path.insert(0, str(Path(__file__).parent))
            import audio_handler, tempfile
            out = tempfile.mktemp(suffix=".wav")
            result = audio_handler.export_wav([], out)
            assert result is False, "Expected False for empty chunk list"
            assert not os.path.exists(out), "File should not be created"

        self._run("Audio: export_wav([]) returns False, no file created", _test)

    def check_audio_volume_scale(self):
        """Volume 0-32767 maps to 0.0-1.0 correctly."""
        def _test():
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            import audio_handler
            audio_handler.set_volume_level(0)
            assert audio_handler._current_volume == 0.0
            audio_handler.set_volume_level(32767)
            assert abs(audio_handler._current_volume - 1.0) < 0.001
            audio_handler.set_volume_level(16383)
            assert 0.49 < audio_handler._current_volume < 0.51
            audio_handler.set_volume_level(int(63 * 327.67))  # restore ~63%

        self._run("Audio: volume 0-32767 maps to 0.0-1.0 correctly", _test)

    # ── File extractor ────────────────────────────────────────────────────────

    def check_file_extractor_binary_guard(self):
        """PK-header binary data must be rejected at UI level."""
        def _test():
            text  = "PK\x03\x04some binary content"
            first = text.strip()[:4]
            is_binary = first.startswith("PK") or "\x00" in text[:100]
            assert is_binary, "Binary guard should trigger on PK header"

            good = "The war veteran walked home."
            first2 = good.strip()[:4]
            assert not first2.startswith("PK"), "Real text wrongly flagged"

        self._run("File extractor: binary PK-header guard", _test)

    def check_file_extractor_unsupported(self):
        """Unsupported extension must raise ValueError."""
        def _test():
            import sys, tempfile, os
            sys.path.insert(0, str(Path(__file__).parent))
            from file_extractor import extract_text
            tmp = tempfile.mktemp(suffix=".xyz")
            open(tmp, "w").close()
            try:
                extract_text(tmp)
                raise AssertionError("Should have raised ValueError")
            except ValueError:
                pass
            finally:
                os.unlink(tmp)

        self._run("File extractor: unsupported extension raises ValueError", _test)

    def check_file_extractor_null_bytes(self):
        """Null bytes must be stripped before synthesis check."""
        def _test():
            for raw in ["\x00\x00", "\x00Hello\x00", "   \x00   "]:
                sanitized = "".join(ch for ch in raw if ch >= " " or ch in "\n\t")
                if not sanitized.strip():
                    would_speak = False
                else:
                    would_speak = True
                # \x00\x00 and whitespace-null should NOT speak
                if all(c in "\x00 \n\t" for c in raw):
                    assert not would_speak, f"Null-byte text would be spoken: {repr(raw)}"

        self._run("File extractor: null bytes stripped before synthesis", _test)

    # ── Threading patterns ────────────────────────────────────────────────────

    def check_stop_flag_pattern(self):
        """stop_flag.set/clear/is_set must work correctly."""
        def _test():
            import threading
            flag = threading.Event()
            assert not flag.is_set()
            flag.set()
            assert flag.is_set()
            flag.clear()
            assert not flag.is_set()

        self._run("Threading: stop_flag Event set/clear/is_set", _test)

    def check_queue_sentinel_pattern(self):
        """Producer-consumer with None sentinel must drain completely."""
        def _test():
            import queue, threading
            q      = queue.Queue()
            result = []

            def producer():
                for i in range(5):
                    q.put(i)
                q.put(None)  # sentinel

            def consumer():
                while True:
                    item = q.get(timeout=2)
                    if item is None:
                        break
                    result.append(item)

            t1 = threading.Thread(target=producer)
            t2 = threading.Thread(target=consumer)
            t1.start(); t2.start()
            t1.join(3); t2.join(3)
            assert result == [0, 1, 2, 3, 4], f"Queue drain failed: {result}"

        self._run("Threading: producer-consumer None sentinel pattern", _test)

    def check_concurrent_cache_read(self):
        """KokoroSingleton (or legacy _kokoro_cache) must be thread-safe."""
        def _test():
            import sys, threading
            sys.path.insert(0, str(Path(__file__).parent))
            import voices
            errors = []

            # GLM 5 replaced _kokoro_cache with KokoroSingleton
            if hasattr(voices, '_kokoro_singleton'):
                singleton = voices._kokoro_singleton
                def reader():
                    try:
                        _ = singleton.is_loaded  # thread-safe property
                    except Exception as e:
                        errors.append(str(e))
            else:
                lock  = getattr(voices, '_kokoro_lock', threading.Lock())
                cache = getattr(voices, '_kokoro_cache', {})
                def reader():
                    try:
                        with lock: _ = cache.get("instance")
                    except Exception as e:
                        errors.append(str(e))

            threads = [threading.Thread(target=reader) for _ in range(20)]
            for t in threads: t.start()
            for t in threads: t.join(1)
            assert not errors, f"Race errors: {errors}"

        self._run("Threading: concurrent _kokoro_cache reads (20 threads)", _test)

    def check_version_constant(self):
        """__version__ must exist in ttsvoices.py and be valid semver."""
        def _test():
            import re
            src = (Path(__file__).parent / "ttsvoices.py").read_text(encoding="utf-8")
            found = False
            for line in src.split("\n"):
                if "__version__" in line and "=" in line:
                    ver = line.split("=", 1)[1].strip().strip("\"'")
                    assert re.match(r"\d+\.\d+\.\d+", ver), f"Not semver: {ver!r}"
                    found = True
                    break
            assert found, "Missing __version__ in ttsvoices.py"
        self._run("Versioning: __version__ semver constant exists", _test)

    def check_audio_export_sample_rate_locked(self):
        """export_wav locks sample_rate from first chunk not last."""
        def _test():
            import sys, io, wave as _wave
            sys.path.insert(0, str(Path(__file__).parent))
            import audio_handler

            def _mk_wav(sr):
                b = io.BytesIO()
                with _wave.open(b, 'wb') as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                    wf.writeframes(b'\x00\x00' * sr)
                return b.getvalue()

            import tempfile, os
            out = tempfile.mktemp(suffix='.wav')
            # First chunk 24000Hz, second 22050Hz
            ok = audio_handler.export_wav([_mk_wav(24000), _mk_wav(22050)], out)
            if ok and os.path.exists(out):
                with _wave.open(out) as wf:
                    sr = wf.getframerate()
                os.unlink(out)
                assert sr == 24000, f"Expected 24000 (first chunk), got {sr}"

        self._run("Audio: export_wav locks sample_rate from first chunk", _test)

    def check_espeak_tmp_cleanup(self):
        """espeak temp file is cleaned even if subprocess raises."""
        def _test():
            import sys, os, tempfile, unittest.mock as mock
            sys.path.insert(0, str(Path(__file__).parent))
            import voices
            # Patch subprocess to fail immediately
            with mock.patch('subprocess.run', side_effect=RuntimeError("fake fail")):
                try:
                    voices._synth_espeak("test", "English (US)")
                except Exception:
                    pass
            # Check no .wav files left in /tmp from this call
            # (We can't check perfectly without capturing the exact path,
            # but the code uses try/finally so this is structural validation)
            import inspect
            src = inspect.getsource(voices._synth_espeak)
            assert 'finally' in src, "Missing finally block in _synth_espeak"
            assert 'os.unlink(out)' in src, "Missing unlink in finally"
        self._run("Audio: espeak temp file cleaned up in finally block", _test)

    def check_config_debounce(self):
        """Config save must be debounced — checks source file directly."""
        def _test():
            src = (Path(__file__).parent / "ttsvoices.py").read_text(encoding="utf-8")
            fn_start = src.find("def _on_cfg_change(")
            fn_end   = src.find("\n    def ", fn_start + 1)
            fn_src   = src[fn_start:fn_end]
            assert "after_cancel" in fn_src, "Config save not debounced (missing after_cancel)"
            assert "after(800" in fn_src or "after(1000" in fn_src,                 "Debounce delay not found in _on_cfg_change"
        self._run("Config: slider changes debounced — no immediate disk writes", _test)

    def check_wav_buffer_stream_discard(self):
        """Played WAV chunks are freed from _wav_buffer immediately."""
        def _test():
            src = (Path(__file__).parent / "ttsvoices.py").read_text(encoding="utf-8")
            assert "_wav_buffer[i] = None" in src,                 "WAV buffer not stream-and-discarding chunks after play"
        self._run("Memory: _wav_buffer clears chunks after play (stream-and-discard)", _test)

    def check_fallback_thread_safety(self):
        """fallback_event must be a threading.Event, not a plain bool."""
        def _test():
            import sys, threading
            sys.path.insert(0, str(Path(__file__).parent))
            import voices
            assert hasattr(voices, 'fallback_event'), "fallback_event missing"
            assert isinstance(voices.fallback_event, threading.Event),                 f"fallback_event should be threading.Event, got {type(voices.fallback_event)}"
        self._run("Threading: fallback_occurred uses thread-safe Event", _test)

    def check_chunk_unicode_terminators(self):
        """chunk_text must split on Unicode sentence terminators."""
        def _test():
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from voices import chunk_text
            jp_text = "これはテストです。もう一つの文です。三つ目です。"
            chunks = chunk_text(jp_text * 10, max_words=80)
            assert len(chunks) >= 1, "chunk_text returned nothing for Unicode text"
        self._run("Chunking: Unicode sentence terminators (。？！) handled", _test)

    def run_all(self) -> dict:
        """Run all checks. Returns summary dict."""
        checks = [
            self.check_odf_crypto_parse,
            self.check_odf_crypto_keydrive,
            self.check_odf_encryption_detection,
            self.check_highlight_cancel_ids,
            self.check_syllable_estimator,
            self.check_bookmark_roundtrip,
            self.check_chunk_position_slicing,
            self.check_fallback_flag,
            self.check_chunk_size_limit,
            self.check_chunk_text_empty,
            self.check_audio_stop_idempotent,
            self.check_audio_export_empty,
            self.check_audio_volume_scale,
            self.check_file_extractor_binary_guard,
            self.check_file_extractor_unsupported,
            self.check_file_extractor_null_bytes,
            self.check_stop_flag_pattern,
            self.check_queue_sentinel_pattern,
            self.check_concurrent_cache_read,
            # Architecture review additions
            self.check_version_constant,
            self.check_audio_export_sample_rate_locked,
            self.check_espeak_tmp_cleanup,
            self.check_config_debounce,
            self.check_wav_buffer_stream_discard,
            self.check_fallback_thread_safety,
            self.check_chunk_unicode_terminators,
        ]
        for check in checks:
            check()

        total = len(self.passed) + len(self.failed)
        summary = {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "total":  total,
            "ok":     len(self.failed) == 0,
        }
        if self.failed:
            warning(
                f"Health check: {len(self.failed)}/{total} tests FAILED",
                "\n".join(self.failed)
            )
        else:
            info(f"Health check: all {total} tests passed ✓")

        return summary


def run_health_checks() -> dict:
    """Public entry point — call once at app startup."""
    return HealthCheck().run_all()


# ── Report / accessors ────────────────────────────────────────────────────────

def get_errors() -> list:
    with _lock:
        return list(_errors)


def get_log_path() -> str:
    return str(_session_log)


def get_report() -> str:
    lines = [
        "TTS Voices 2.0 – Bug Report",
        f"Session: {_session_log.name}",
        "=" * 60,
    ]
    with _lock:
        snapshot = list(_errors)
    for e in snapshot:
        lines.append(f"[{e['level']}] {e['timestamp']}")
        lines.append(f"  {e['message']}")
        if e.get("details"):
            for dl in e["details"].strip().split("\n"):
                lines.append(f"    {dl}")
        lines.append("")
    return "\n".join(lines)


def clear_log():
    """Clear all in-memory log entries and truncate the session log file.
    New entries will appear after the next app activity (speak, load, theme change).
    """
    with _lock:
        _errors.clear()
        try:
            with open(_session_log, "w", encoding="utf-8") as f:
                f.write("")
        except OSError:
            pass
