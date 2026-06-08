"""
TTS Voices 2.5.0 - Optimized Voice Synthesis Module

Maintained by the opencode AI assistant — see README.md.
Priority chain: Edge TTS (cloud) -> Kokoro ONNX -> espeak-ng

OPTIMIZATIONS:
1. Lazy loading of Kokoro model (only load when first used)
2. Better caching with LRU cache for voice models
3. Pre-emptive chunk splitting to avoid phoneme overflow
4. Word timing estimation for synchronized highlighting
5. Better error recovery and fallback chain
6. Thread-safe singleton pattern for Kokoro instance
7. Memory-efficient streaming for long texts
"""
import os
import io
import asyncio
import contextlib
import wave
import subprocess
import tempfile
import threading
import re
import time
from pathlib import Path
from functools import lru_cache
from typing import Optional, Tuple, List, Dict, Generator, Callable
from collections import deque
import weakref

# ── espeak-ng Data Path Resolution ───────────────────────────────────────────
#
# The `espeakng-loader` pip package hard-bakes the GitHub Actions runner path
# (/home/runner/work/espeakng-loader/.../espeak-ng-data) at compile time.
# On any real machine that path doesn't exist, so every synthesis call prints:
#   "Error processing file '.../phontab': No such file or directory"
#
# We fix this at module-import time by:
#   1. Locating the real espeak-ng-data directory on disk
#   2. Setting ESPEAK_DATA_PATH before any espeak call is made
#   3. Verifying the install actually works (test synthesis) for check_espeak()
#
# Edge cases covered:
#   - espeakng-loader broken path (pip install on any real machine)
#   - x86_64 vs aarch64 vs armhf multiarch lib paths
#   - ESPEAK_DATA_PATH already set but stale (e.g. from a previous venv)
#   - System espeak-ng vs espeakng-loader bundled binary
#   - Old `espeak` binary (non-ng) as last resort
#   - Binary exists but data files genuinely missing (partial install)
#   - conda / snap / flatpak / non-FHS installs
#   - Read permission errors on data directory


def _find_espeak_data_dir() -> str:
    """
    Locate the espeak-ng-data directory on this machine.

    Returns the directory path (str) if found and valid, else empty string.
    Checks in order:
      1. Current ESPEAK_DATA_PATH env var (if it actually works)
      2. All multiarch lib paths (/usr/lib/<triplet>/espeak-ng-data)
      3. /usr/share and /usr/local/share
      4. The espeakng-loader package's own bundled data
      5. Recursive search under /usr (slow, last resort)
    """
    import glob

    def _has_phontab(path: str) -> bool:
        """A data dir is valid if phontab is readable inside it."""
        if not path:
            return False
        try:
            return os.path.isfile(os.path.join(path, "phontab"))
        except (OSError, PermissionError):
            return False

    # 1. Honour existing env var only if it actually points somewhere real
    existing = os.environ.get("ESPEAK_DATA_PATH", "")
    if _has_phontab(existing):
        return existing

    # 2. Standard multiarch lib paths (covers x86_64, aarch64, armhf, riscv64…)
    candidates = []
    for triplet in (
        "x86_64-linux-gnu",
        "aarch64-linux-gnu",
        "armhf-linux-gnueabihf",
        "arm-linux-gnueabihf",
        "riscv64-linux-gnu",
        "loongarch64-linux-gnu",
        "i386-linux-gnu",
    ):
        candidates.append(f"/usr/lib/{triplet}/espeak-ng-data")

    # 3. Non-multiarch and local installs
    candidates += [
        "/usr/share/espeak-ng-data",
        "/usr/local/share/espeak-ng-data",
        "/usr/local/lib/espeak-ng-data",
        "/opt/espeak-ng/lib/espeak-ng-data",
        "/opt/espeak-ng/share/espeak-ng-data",
    ]

    for p in candidates:
        if _has_phontab(p):
            return p

    # 4. espeakng-loader bundled data (pip package installs its own copy)
    try:
        import importlib.util
        spec = importlib.util.find_spec("espeakng")
        if spec and spec.origin:
            pkg_dir = os.path.dirname(spec.origin)
            for rel in (
                "espeak-ng-data",
                os.path.join("..", "espeak-ng-data"),
                os.path.join("..", "_dynamic", "share", "espeak-ng-data"),
            ):
                candidate = os.path.normpath(os.path.join(pkg_dir, rel))
                if _has_phontab(candidate):
                    return candidate
    except Exception:
        pass

    # 5. Conda environments
    try:
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            for rel in (
                os.path.join("share", "espeak-ng-data"),
                os.path.join("lib", "espeak-ng-data"),
            ):
                candidate = os.path.join(conda_prefix, rel)
                if _has_phontab(candidate):
                    return candidate
    except Exception:
        pass

    # 6. Recursive glob under /usr — slow but catches unusual installs
    try:
        hits = glob.glob("/usr/**/espeak-ng-data/phontab", recursive=True)
        if hits:
            return os.path.dirname(hits[0])
    except Exception:
        pass

    return ""


def _resolve_espeak_binary() -> str:
    """
    Return the best available espeak binary name.

    Prefers 'espeak-ng', falls back to 'espeak' (older systems).
    Returns empty string if neither is found.
    """
    import shutil
    for binary in ("espeak-ng", "espeak"):
        if shutil.which(binary):
            return binary
    return ""


# Run path resolution at import time so the env var is set before any
# subprocess call — including those made by the espeakng Python package.
_ESPEAK_DATA_DIR: str = _find_espeak_data_dir()
_ESPEAK_BINARY:   str = _resolve_espeak_binary()

if _ESPEAK_DATA_DIR:
    os.environ["ESPEAK_DATA_PATH"] = _ESPEAK_DATA_DIR

# Cache the result of an actual test synthesis so check_espeak() is accurate.
# None = not yet tested, True/False = result
_espeak_verified: "bool | None" = None


def _verify_espeak_works() -> bool:
    """
    Attempt a real (silent) synthesis to confirm espeak is fully functional.

    This catches the case where the binary exists but data files are missing
    or broken (the espeakng-loader CI-path bug, partial install, etc.).

    Result is cached in _espeak_verified so it only runs once per session.
    """
    global _espeak_verified
    if _espeak_verified is not None:
        return _espeak_verified

    if not _ESPEAK_BINARY:
        _espeak_verified = False
        return False

    import tempfile, shutil

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name

        env = dict(os.environ)
        if _ESPEAK_DATA_DIR:
            env["ESPEAK_DATA_PATH"] = _ESPEAK_DATA_DIR

        result = subprocess.run(
            [_ESPEAK_BINARY, "-v", "en", "-w", tmp, "test"],
            input=None,
            capture_output=True,
            timeout=10,
            env=env,
        )

        # Success: return code 0 AND output file has content
        ok = (result.returncode == 0 and
              os.path.isfile(tmp) and
              os.path.getsize(tmp) > 44)  # > WAV header size

        if not ok:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            _get_bug_tracker().warning(
                f"espeak-ng test synthesis failed (rc={result.returncode}): "
                f"{stderr_text[:200]}"
            )
            if "phontab" in stderr_text or "No such file" in stderr_text:
                _get_bug_tracker().warning(
                    "espeak-ng data path problem detected. "
                    "Fix: sudo apt install espeak-ng espeak-ng-data  "
                    "or: pip install --force-reinstall espeakng-loader"
                )

        _espeak_verified = ok
        return ok

    except subprocess.TimeoutExpired:
        _get_bug_tracker().warning("espeak-ng test synthesis timed out")
        _espeak_verified = False
        return False
    except Exception as e:
        _get_bug_tracker().warning(f"espeak-ng verification failed: {e}")
        _espeak_verified = False
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ── Lazy import of bug_tracker to avoid circular imports ─────────────────────
_bug_tracker = None
def _get_bug_tracker():
    """Lazy import of bug_tracker module."""
    global _bug_tracker
    if _bug_tracker is None:
        import bug_tracker
        _bug_tracker = bug_tracker
    return _bug_tracker

# ── Thread-Safe Singleton for Kokoro Instance ─────────────────────────────────
class KokoroSingleton:
    """
    Thread-safe singleton pattern for Kokoro ONNX instance.
    
    OPTIMIZATION: Uses double-checked locking with a dedicated lock
    to ensure only one Kokoro instance is created across all threads.
    This saves ~500MB of memory when multiple synthesis threads run.
    """
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # Double-checked locking
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._kokoro_instance = None
            self._model_path = None
            self._voices_path = None
            self._provider = "CPUExecutionProvider"
            self._load_lock = threading.Lock()
            self._initialized = True
    
    def get_instance(self, model_path: str, voices_path: str, provider: str):
        """
        Get or create the Kokoro instance.

        OPTIMIZATION: Lazy loading - instance is only created when first requested.
        Uses a separate load lock to allow concurrent reads after initialization.
        """
        # ── Provider blacklist gate ────────────────────────────────────────
        # If a previous attempt showed this provider is fundamentally
        # incompatible with the current model (e.g. OpenVINO EP can't
        # handle Kokoro's STFT dynamic-rank op), skip it on subsequent
        # calls instead of waiting the 8+ s for it to fail again. The
        # blacklist is process-local so a future onnxruntime upgrade
        # that fixes the incompatibility is picked up on next launch.
        if provider in _KOKORO_INCOMPATIBLE_PROVIDERS:
            _get_bug_tracker().info(
                f"Skipping {provider!r} for Kokoro (known incompatible, "
                f"see _KOKORO_INCOMPATIBLE_PROVIDERS)."
            )
            # Downgrade to CPU so synthesis still works.
            provider = "CPUExecutionProvider"

        # Fast path - already loaded with same config
        if (self._kokoro_instance is not None and
            self._model_path == model_path and
            self._provider == provider):
            return self._kokoro_instance
        
        with self._load_lock:
            # Re-check after acquiring lock
            if (self._kokoro_instance is not None and 
                self._model_path == model_path and 
                self._provider == provider):
                return self._kokoro_instance
            
            _get_bug_tracker().info(f"Loading Kokoro [{provider}]: {model_path}")
            t0 = time.time()

            try:
                import kokoro_onnx
                # kokoro_onnx 0.5.0 Kokoro.__init__ does NOT accept `providers=`;
                # build a custom onnxruntime.InferenceSession with the chosen
                # provider and wire it in via Kokoro.from_session() so the
                # provider actually takes effect (was previously silently
                # falling through to CPU).
                import onnxruntime as ort
                # onnxruntime logs the full stack trace to stderr when a
                # provider fails to compile the model. For known-bad
                # providers (see _KOKORO_INCOMPATIBLE_PROVIDERS) this is
                # just noise — redirect stderr to /dev/null during the
                # doomed attempt and only log the captured exception.
                if provider in _KOKORO_INCOMPATIBLE_PROVIDERS:
                    _stderr_cm = contextlib.redirect_stderr(io.StringIO())
                else:
                    _stderr_cm = contextlib.nullcontext()
                with _stderr_cm:
                    sess = ort.InferenceSession(model_path, providers=[provider])
                self._kokoro_instance = kokoro_onnx.Kokoro.from_session(
                    sess, voices_path)
            except Exception as _e:
                # Suppress the onnxruntime stack-trace noise for known-bad
                # providers (see _KOKORO_INCOMPATIBLE_PROVIDERS comment).
                # The first failure still goes to the bug log as ERROR for
                # diagnostics, but subsequent attempts for the same provider
                # are downgraded to INFO via the blacklist.
                _msg = f"Provider-specific load failed for {provider!r} ({_e}); falling back to default"
                if provider in _KOKORO_INCOMPATIBLE_PROVIDERS:
                    _get_bug_tracker().info(
                        f"{_msg}  (provider on blacklist — see "
                        f"_KOKORO_INCOMPATIBLE_PROVIDERS)"
                    )
                else:
                    # First-time failure for a provider not on the static
                    # blacklist — mark it dynamically so we don't repeat the
                    # 8+ s wait on every later synthesis.
                    _KOKORO_INCOMPATIBLE_PROVIDERS.add(provider)
                    _get_bug_tracker().warning(
                        f"{_msg}  (added {provider!r} to runtime blacklist)"
                    )
                import kokoro_onnx
                self._kokoro_instance = kokoro_onnx.Kokoro(model_path, voices_path)

            self._model_path = model_path
            self._voices_path = voices_path
            self._provider = provider

            _get_bug_tracker().info(f"Kokoro loaded in {time.time()-t0:.1f}s")

            # Verify active providers — Kokoro exposes the session as `.sess`
            # directly (not `.model.sess` — that AttributeError was being
            # swallowed and hiding the provider bug above).
            try:
                session_prov = self._kokoro_instance.sess.get_providers()
                _get_bug_tracker().info(f"ONNX active providers: {session_prov}")
                if provider not in session_prov:
                    _get_bug_tracker().warning(
                        f"Requested provider {provider!r} not active; got {session_prov}. "
                        f"The runtime may have fallen back to CPU."
                    )
            except Exception as _e:
                _get_bug_tracker().warning(f"Provider probe failed: {_e}")

            return self._kokoro_instance
    
    def clear(self):
        """Clear the cached instance (e.g., when provider changes)."""
        with self._load_lock:
            self._kokoro_instance = None
            self._model_path = None
            self._voices_path = None
    
    @property
    def is_loaded(self) -> bool:
        return self._kokoro_instance is not None


# ── LRU Cache for Voice Configurations ────────────────────────────────────────
@lru_cache(maxsize=32)
def _get_voice_config(voice_name: str, engine: str) -> Tuple[str, str]:
    """
    Get voice configuration with LRU caching.
    
    OPTIMIZATION: Caches voice lookups to avoid repeated dict traversals.
    Returns (voice_key, lang) tuple.
    """
    if engine == ENGINE_KOKORO:
        config = KOKORO_VOICES.get(voice_name, {})
        return (config.get("voice", "af_heart"), config.get("lang", "en-us"))
    elif engine == ENGINE_EDGE_TTS:
        config = EDGE_TTS_VOICES.get(voice_name, {})
        return (config.get("voice", "en-US-AriaNeural"), "en-us")
    elif engine == ENGINE_ESPEAK:
        config = ESPEAK_VOICES.get(voice_name, {})
        return (config.get("voice", "en-us"), "en-us")
    return ("af_heart", "en-us")


# ── Pre-emptive Chunk Splitting ──────────────────────────────────────────────
class ChunkProcessor:
    """
    Handles pre-emptive chunk splitting to avoid phoneme overflow.
    
    OPTIMIZATION: Pre-calculates safe chunk boundaries based on:
    - Average phonemes per word (~6-7 for English)
    - Safe phoneme limit (510 for Kokoro)
    - Sentence boundaries for natural breaks
    """
    PHONEMES_PER_WORD_AVG = 7
    PHONEME_LIMIT = 510
    SAFE_WORD_LIMIT = 65  # ~455 phonemes, leaves safety margin
    
    # Regex pattern for sentence boundaries including Unicode
    SENTENCE_SPLIT_PATTERN = re.compile(
        r'(?<=[.!?\u3002\uff1f\uff01\u2026])\s*|'  # Standard + CJK terminators (no space required)
        r'(?<=\u2014)\s*|'   # Em-dash
        r'(?<=\n)\s*'        # Newline boundary
    )

    @classmethod
    def estimate_phonemes(cls, text: str) -> int:
        """
        Estimate phoneme count for a text.
        For CJK/non-ASCII text, uses character count directly
        (each CJK character ≈ 1 syllable ≈ 2.5 phonemes).
        """
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
        if ascii_ratio < 0.5:
            # Non-Latin script: count characters directly
            return int(len(text) * 2.5)
        words = text.split()
        total_syllables = sum(cls._count_syllables(w) for w in words)
        return int(total_syllables * 2.5 + len(words) * 0.5)

    @staticmethod
    def _count_syllables(word: str) -> int:
        """Count syllables in a word for phoneme estimation."""
        ascii_word = re.sub(r"[^a-zA-Z]", "", word.lower())
        if not ascii_word:
            # CJK or other non-Latin: each character ≈ 1 syllable
            return max(1, len(word))
        count = len(re.findall(r"[aeiouy]+", ascii_word))
        if ascii_word.endswith("e") and count > 1:
            count -= 1
        return max(1, count)
    
    @classmethod
    def chunk_text_safe(cls, text: str, max_words: int = None) -> List[str]:
        """
        Split text into sentence-respecting chunks with phoneme-aware limits.
        
        OPTIMIZATION: Uses pre-emptive splitting based on estimated phoneme
        count rather than waiting for overflow errors.
        """
        max_words = max_words or cls.SAFE_WORD_LIMIT
        text = text.strip()
        if not text:
            return []
        
        # Pre-emptive check: if text is small, return as-is
        words = text.split()
        if len(words) <= max_words:
            estimated = cls.estimate_phonemes(text)
            if estimated < cls.PHONEME_LIMIT:
                return [text]
        
        # Split by sentence boundaries
        sentences = cls.SENTENCE_SPLIT_PATTERN.split(text)
        chunks = []
        current_chunk = []
        current_words = 0
        current_phonemes = 0
        
        for sentence in sentences:
            if not sentence.strip():
                continue
            
            sent_words = sentence.split()
            sent_word_count = len(sent_words)
            sent_phonemes = cls.estimate_phonemes(sentence)
            
            # If single sentence exceeds limits, hard-split it
            if sent_word_count > max_words or sent_phonemes > cls.PHONEME_LIMIT:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_words = 0
                    current_phonemes = 0
                
                # Hard-split the oversized sentence
                for i in range(0, sent_word_count, max_words):
                    piece = " ".join(sent_words[i:i + max_words])
                    if piece.strip():
                        chunks.append(piece)
            
            # Check if adding would exceed limits
            elif (current_words + sent_word_count > max_words or 
                  current_phonemes + sent_phonemes > cls.PHONEME_LIMIT):
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                current_chunk = [sentence]
                current_words = sent_word_count
                current_phonemes = sent_phonemes
            
            else:
                current_chunk.append(sentence)
                current_words += sent_word_count
                current_phonemes += sent_phonemes
        
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        
        return [c.strip() for c in chunks if c.strip()]


# ── Word Timing Estimation for Highlighting ───────────────────────────────────
class WordTimingEstimator:
    """
    Estimates word timing for synchronized highlighting.
    
    OPTIMIZATION: Provides timing estimates without requiring
    actual audio synthesis, enabling real-time highlighting preview.
    """
    
    # Average speaking rates (words per minute) by speed multiplier
    BASE_WPM = 150  # Normal speech rate
    
    @classmethod
    def estimate_word_timings(
        cls, 
        text: str, 
        speed: float = 1.0,
        sample_rate: int = 24000
    ) -> List[Tuple[str, float, float]]:
        """
        Estimate timing for each word.
        
        Returns list of (word, start_time, end_time) tuples.
        Times are in seconds.
        
        OPTIMIZATION: Uses syllable-weighted timing for more accurate
        synchronization than simple equal-word-duration.
        """
        words = text.split()
        if not words:
            return []
        
        # Calculate effective WPM
        effective_wpm = cls.BASE_WPM * speed
        seconds_per_word = 60.0 / effective_wpm
        
        timings = []
        current_time = 0.0
        
        for word in words:
            # Adjust timing by syllable count
            syllables = ChunkProcessor._count_syllables(word)
            # Base duration per syllable (average is ~1.5 syllables per word)
            duration = seconds_per_word * (syllables / 1.5)
            
            timings.append((word, current_time, current_time + duration))
            current_time += duration
        
        return timings
    
    @classmethod
    def estimate_chunk_duration(cls, text: str, speed: float = 1.0) -> float:
        """
        Estimate total duration for a text chunk.
        
        OPTIMIZATION: Provides quick duration estimate for progress bars
        and playback scheduling.
        """
        words = len(text.split())
        syllables = sum(ChunkProcessor._count_syllables(w) for w in text.split())
        effective_wpm = cls.BASE_WPM * speed
        
        # Weighted by syllable count
        return (syllables / 1.5) * (60.0 / effective_wpm)


# ── Memory-Efficient Streaming for Long Texts ────────────────────────────────
class StreamingSynthesizer:
    """
    Memory-efficient streaming synthesis for long texts.
    
    OPTIMIZATION: Yields audio chunks as they're generated rather than
    accumulating all audio in memory. Uses a circular buffer for
    recent chunks (for potential re-play).
    """
    
    def __init__(self, buffer_size: int = 10):
        """
        Initialize streaming synthesizer.
        
        Args:
            buffer_size: Number of recent chunks to keep in memory for replay
        """
        self._buffer_size = buffer_size
        self._buffer = deque(maxlen=buffer_size)
        self._lock = threading.Lock()
    
    def synthesize_stream(
        self,
        text: str,
        engine: str,
        voice_name: str,
        speed: float = 1.0,
        pitch: float = 1.0,
        chunk_callback: Optional[Callable[[bytes, int], None]] = None
    ) -> Generator[bytes, None, None]:
        """
        Synthesize text and yield audio chunks.
        
        OPTIMIZATION: Yields chunks as they're synthesized, reducing
        peak memory usage for long texts from O(n) to O(1) where n
        is the number of chunks.
        
        Args:
            text: Text to synthesize
            engine: TTS engine to use
            voice_name: Voice name
            speed: Speech speed multiplier
            pitch: Pitch multiplier (espeak only)
            chunk_callback: Optional callback for each chunk (audio_data, sample_rate)
        
        Yields:
            WAV audio bytes for each chunk
        """
        chunks = ChunkProcessor.chunk_text_safe(text)
        
        for i, chunk in enumerate(chunks):
            try:
                audio_data = synthesize(chunk, engine, voice_name, speed, pitch)
                
                # Store in circular buffer
                with self._lock:
                    self._buffer.append((i, audio_data))
                
                # Call progress callback if provided
                if chunk_callback:
                    # Extract sample rate from WAV header
                    sr = self._extract_sample_rate(audio_data)
                    chunk_callback(audio_data, sr)
                
                yield audio_data
                
            except Exception as e:
                _get_bug_tracker().error(f"Chunk {i} synthesis failed: {e}")
                # Continue with next chunk rather than failing entirely
                continue
    
    @staticmethod
    def _extract_sample_rate(wav_bytes: bytes) -> int:
        """Extract sample rate from WAV header."""
        try:
            with wave.open(io.BytesIO(wav_bytes), 'rb') as wf:
                return wf.getframerate()
        except Exception:
            return 24000  # Default
    
    def get_buffered_chunk(self, index: int) -> Optional[bytes]:
        """Get a buffered chunk by index (for replay)."""
        with self._lock:
            for i, data in self._buffer:
                if i == index:
                    return data
        return None
    
    def clear_buffer(self):
        """Clear the circular buffer to free memory."""
        with self._lock:
            self._buffer.clear()


# ── Global State ──────────────────────────────────────────────────────────────
_kokoro_singleton = KokoroSingleton()
_kokoro_lock = threading.Lock()
_onnx_provider = "CPUExecutionProvider"

MODELS_DIR = Path.home() / ".ttsvoices" / "models"

_KOKORO_MODEL_NAMES = [
    "kokoro-v1.0.onnx", "kokoro-v0_19.onnx",
    "kokoro-v0.19.onnx", "model.onnx",
    # FP16 variants from taylorchu/kokoro-onnx (hosted on
    # thewh1teagle/kokoro-onnx releases). Smaller, faster on CPU,
    # slightly lower quality. Optional — pick whichever you prefer.
    "kokoro-v1.0.fp16.onnx",   # 169 MB — FP16, smaller than FP32 with similar quality
    "kokoro-v1.0.fp16-gpu.onnx",  # 169 MB — FP16, GPU-targeted weights
]
_KOKORO_VOICES_NAMES = [
    "voices-v1.0.bin", "voices.bin", "voices.json"
]

# Engine cache for fast availability checks
_engine_cache: dict = {}

KOKORO_VOICES = {
    "Heart (US Female)":    {"voice": "af_heart",    "lang": "en-us"},
    "Bella (US Female)":    {"voice": "af_bella",    "lang": "en-us"},
    "Sarah (US Female)":    {"voice": "af_sarah",    "lang": "en-us"},
    "Nicole (US Female)":   {"voice": "af_nicole",   "lang": "en-us"},
    "Sky (US Female)":      {"voice": "af_sky",      "lang": "en-us"},
    "Adam (US Male)":       {"voice": "am_adam",     "lang": "en-us"},
    "Michael (US Male)":    {"voice": "am_michael",  "lang": "en-us"},
    "Emma (UK Female)":     {"voice": "bf_emma",     "lang": "en-gb"},
    "Isabella (UK Female)": {"voice": "bf_isabella", "lang": "en-gb"},
    "George (UK Male)":     {"voice": "bm_george",   "lang": "en-gb"},
    "Lewis (UK Male)":      {"voice": "bm_lewis",    "lang": "en-gb"},
}

ESPEAK_VOICES = {
    "English (US)":     {"voice": "en-us"},
    "English (UK)":     {"voice": "en-gb"},
    "English (Female)": {"voice": "en+f3"},
}

# Microsoft Edge TTS cloud voices — high quality, fast (network-bound).
# No model download needed; ~16 popular voices across en-US/GB/AU + multilingual.
# See https://learn.microsoft.com/azure/ai-services/speech-service/language-support
EDGE_TTS_VOICES = {
    "Aria (US F, HD)":      {"voice": "en-US-AriaNeural"},
    "Jenny (US F, HD)":     {"voice": "en-US-JennyNeural"},
    "Sara (US F, HD)":      {"voice": "en-US-SaraNeural"},
    "Ana (US F, Child)":    {"voice": "en-US-AnaNeural"},
    "Michelle (US F, HD)":  {"voice": "en-US-MichelleNeural"},
    "Guy (US M, HD)":       {"voice": "en-US-GuyNeural"},
    "Davis (US M, HD)":     {"voice": "en-US-DavisNeural"},
    "Tony (US M, HD)":      {"voice": "en-US-TonyNeural"},
    "Andrew (US M, Multi)": {"voice": "en-US-AndrewMultilingualNeural"},
    "Emma (US F, Multi)":   {"voice": "en-US-EmmaMultilingualNeural"},
    "Brian (US M, Multi)":  {"voice": "en-US-BrianMultilingualNeural"},
    "Sonia (UK F, HD)":     {"voice": "en-GB-SoniaNeural"},
    "Ryan (UK M, HD)":      {"voice": "en-GB-RyanNeural"},
    "Libby (UK F, HD)":     {"voice": "en-GB-LibbyNeural"},
    "William (UK M, HD)":   {"voice": "en-GB-WilliamNeural"},
    "Natasha (AU F, HD)":   {"voice": "en-AU-NatashaNeural"},
    "William (AU M, HD)":   {"voice": "en-AU-WilliamNeural"},
}

ENGINE_KOKORO     = "Kokoro ONNX"
ENGINE_ESPEAK     = "espeak-ng"
ENGINE_EDGE_TTS   = "Edge TTS (Cloud)"
ENGINE_CHATTERBOX = "Chatterbox"
ENGINE_OMNIVOICE  = "OmniVoice"
ENGINE_F5TTS      = "F5-TTS"

# ── Kokoro-incompatible ONNX execution providers ─────────────────────────
# Providers that onnxruntime advertises as available but that fundamentally
# cannot run the current Kokoro-82M model. Trying them produces a 5-15 s
# wait followed by a noisy stack trace and a fallback to CPU. The user
# experiences this as a freeze on the first Speak click.
#
# Known incompatibilities:
#   OpenVINOExecutionProvider
#     - Kokoro's /decoder/.../STFT_output_0 op has a dynamic rank.
#     - OpenVINO's CPU plugin (the default backend) refuses dynamic-rank
#       Parameter ops: "Check '!shape.rank().is_dynamic()' failed at
#       src/plugins/intel_cpu/src/node.cpp:106".
#     - Reproduced on onnxruntime 1.20+ with onnxruntime-openvino 2024.x
#       and Kokoro-82M (kokoro_onnx 0.5.0).
#     - Workaround would require an ONNX export with static STFT shapes,
#       which is not provided by upstream Kokoro-82M.
#
# Add to this set only after reproducing the failure on a real install.
# Removing an entry is a one-line fix that takes effect on next launch.
_KOKORO_INCOMPATIBLE_PROVIDERS: set = {
    "OpenVINOExecutionProvider",
}

# Thread-safe fallback signaling
last_engine_used: str = ""
fallback_event = threading.Event()
fallback_occurred: bool = False


# ── Utility Functions ────────────────────────────────────────────────────────
def _find_file(candidates: List[str]) -> Optional[str]:
    """Find first existing file from candidate list."""
    for name in candidates:
        p = MODELS_DIR / name
        if p.exists():
            return str(p)
    return None


def kokoro_models_ready() -> bool:
    """Check if Kokoro model files are available."""
    return (_find_file(_KOKORO_MODEL_NAMES) is not None and 
            _find_file(_KOKORO_VOICES_NAMES) is not None)


def get_available_providers() -> List[str]:
    """Get list of available ONNX execution providers."""
    try:
        import onnxruntime as ort
        return ort.get_available_providers()
    except Exception:
        return ["CPUExecutionProvider"]


def get_current_provider() -> str:
    """Get current ONNX provider."""
    return _onnx_provider


def set_provider(provider: str):
    """
    Set ONNX execution provider.
    
    OPTIMIZATION: Clears cached Kokoro instance to force reload
    with new provider settings.
    """
    global _onnx_provider
    _onnx_provider = provider
    
    # Clear singleton instance to force reload with new provider
    _kokoro_singleton.clear()
    
    # Clear engine cache
    _engine_cache.clear()
    
    # Clear voice config cache
    _get_voice_config.cache_clear()
    
    _get_bug_tracker().info(f"ONNX provider set to: {provider}")


def check_kokoro() -> bool:
    """
    Fast check for Kokoro availability.
    
    OPTIMIZATION: Checks filesystem first (fast), then imports
    (slow) only if files exist. Result is cached.
    """
    if "kokoro" not in _engine_cache:
        has_model = any((MODELS_DIR / n).exists() for n in _KOKORO_MODEL_NAMES)
        if not has_model:
            _engine_cache["kokoro"] = False
        else:
            try:
                import importlib.util
                _engine_cache["kokoro"] = (
                    importlib.util.find_spec("kokoro_onnx") is not None
                )
            except Exception:
                _engine_cache["kokoro"] = False
    return _engine_cache["kokoro"]


def check_espeak() -> bool:
    """
    Check that espeak-ng is installed AND actually works.

    Unlike a bare shutil.which() check, this catches:
      - espeakng-loader baked-in CI data path (binary exists, synthesis fails)
      - Partial system installs (binary present, espeak-ng-data missing)
      - Any other broken configuration

    Result is cached after the first call.
    """
    if "espeak" not in _engine_cache:
        _engine_cache["espeak"] = _verify_espeak_works()
    return _engine_cache["espeak"]


def check_chatterbox() -> bool:
    """Check for Chatterbox TTS availability (cached)."""
    if "chatterbox" not in _engine_cache:
        try:
            import importlib.util
            _engine_cache["chatterbox"] = (
                importlib.util.find_spec("chatterbox") is not None
            )
        except Exception:
            _engine_cache["chatterbox"] = False
    return _engine_cache["chatterbox"]


def check_omnivoice() -> bool:
    """Check for OmniVoice availability (cached)."""
    if "omnivoice" not in _engine_cache:
        try:
            import importlib.util
            _engine_cache["omnivoice"] = (
                importlib.util.find_spec("omnivoice") is not None
            )
        except Exception:
            _engine_cache["omnivoice"] = False
    return _engine_cache["omnivoice"]


def check_f5tts() -> bool:
    """Check for F5-TTS availability (cached)."""
    if "f5tts" not in _engine_cache:
        try:
            import importlib.util
            _engine_cache["f5tts"] = (
                importlib.util.find_spec("f5_tts") is not None
            )
        except Exception:
            _engine_cache["f5tts"] = False
    return _engine_cache["f5tts"]


def check_edge_tts() -> bool:
    """Check for edge-tts availability (cached) and a working network.

    Returns True only if both the package is importable AND a connectivity
    probe to api.edge.microsoft.com succeeds within 3 s. Offline users
    don't see the engine as available even if the package is installed.
    """
    if "edge_tts" not in _engine_cache:
        try:
            importlib = __import__("importlib")
            _engine_cache["edge_tts"] = (
                importlib.util.find_spec("edge_tts") is not None
            )
        except Exception:
            _engine_cache["edge_tts"] = False
    if not _engine_cache["edge_tts"]:
        return False
    if "edge_tts_online" in _engine_cache:
        return _engine_cache["edge_tts_online"]
    # Probe the actual Edge TTS endpoint with a 3 s timeout.
    # (api.edge.microsoft.com doesn't resolve; the real endpoint is
    # speech.platform.bing.com, which is the WebSocket host edge-tts uses.)
    try:
        import socket
        sock = socket.create_connection(("speech.platform.bing.com", 443), timeout=3)
        sock.close()
        _engine_cache["edge_tts_online"] = True
    except Exception:
        _engine_cache["edge_tts_online"] = False
    return _engine_cache["edge_tts_online"]


def get_engine_status() -> dict:
    """Get availability status for all engines."""
    return {
        ENGINE_KOKORO:   check_kokoro(),
        ENGINE_ESPEAK:   check_espeak(),
        ENGINE_EDGE_TTS: check_edge_tts(),
    }


def get_all_voices() -> List[Tuple[str, str, str]]:
    """Get list of all available voices across engines.
    Voice-cloning engines (Chatterbox, OmniVoice, F5-TTS) are excluded
    as they require GPU hardware not available on this system.
    Edge TTS voices are only included if the package is installed AND a
    network probe to api.edge.microsoft.com succeeds.
    """
    result = []
    status = get_engine_status()

    if status[ENGINE_KOKORO]:
        for n in KOKORO_VOICES:
            result.append((f"Kokoro · {n}", ENGINE_KOKORO, n))

    if status[ENGINE_EDGE_TTS]:
        for n in EDGE_TTS_VOICES:
            result.append((f"Edge · {n}", ENGINE_EDGE_TTS, n))

    if status[ENGINE_ESPEAK]:
        for n in ESPEAK_VOICES:
            result.append((f"espeak · {n}", ENGINE_ESPEAK, n))

    if not result:
        result.append(("espeak · English (US)", ENGINE_ESPEAK, "English (US)"))

    return result


# ── Synthesis Functions ───────────────────────────────────────────────────────

# ── Text Preprocessor ────────────────────────────────────────────────────────
def _is_mostly_ascii(text: str, threshold: float = 0.7) -> bool:
    """Return True if at least `threshold` fraction of non-space chars are ASCII."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return True
    ascii_count = sum(1 for c in chars if ord(c) < 128)
    return ascii_count / len(chars) >= threshold


def _preprocess_for_kokoro(text: str) -> str:
    """
    Clean text before sending to Kokoro to prevent synthesis errors.

    Handles the two most common failure modes seen in logs:
    1. "number of lines in input and output must be equal" — caused by bullet
       chars (•, ▪, ◦) and some Unicode that confuse Kokoro's phonemizer.
    2. "Overlapped entries / zip bomb" — voices.bin corruption, handled separately.
    3. PDF-merged words (e.g. "CustomerService:" → "Customer Service:") — caused
       by PDF extractors stripping spaces at line breaks.

    NOTE: Non-ASCII stripping (step 2) is skipped when text is predominantly
    non-English (CJK, Arabic, Cyrillic, etc.) to avoid wiping the entire input.
    Kokoro English voices will still fail on such text, but at least we preserve
    the string so the error message makes sense.
    """
    import re

    # 1. Replace bullet / list markers with "- "
    text = re.sub(r'[•▪◦‣⁃◆◇●○■□▶►▸→]', '-', text)

    # 2. Strip non-ASCII ONLY if text is mostly ASCII (English / Latin-script).
    #    Skipping this step for CJK/Arabic/Cyrillic lets Kokoro produce its own
    #    error rather than receiving an empty string and silently falling to espeak.
    if _is_mostly_ascii(text):
        text = re.sub(r'[^\x00-\x7F\n\t]', lambda m: _unicode_fallback(m.group()), text)

    # 3. Insert spaces before uppercase run in camelCase / merged words from PDF
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)

    # 4. Insert space after colon when immediately followed by a capital (no space)
    text = re.sub(r':([A-Z])', r': \1', text)

    # 5. Collapse multiple blank lines to max two
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 6. Strip leading/trailing whitespace per line
    lines = [l.rstrip() for l in text.splitlines()]
    text  = '\n'.join(lines)

    return text.strip()


def _unicode_fallback(char: str) -> str:
    """Replace unmapped Unicode with ASCII equivalent or space."""
    MAP = {
        '\u2013': '-', '\u2014': '-', '\u2015': '-',  # dashes
        '\u2018': "'", '\u2019': "'",                   # smart quotes
        '\u201c': '"', '\u201d': '"',
        '\u2026': '...',                                 # ellipsis
        '\u00a0': ' ',                                   # non-breaking space
        '\u00b7': '-',                                   # middle dot
        '\u2022': '-',                                   # bullet
    }
    return MAP.get(char, ' ')


def _synth_kokoro(
    text: str, 
    voice_name: str, 
    speed: float = 1.0
) -> bytes:
    """
    Synthesize using Kokoro ONNX.
    
    OPTIMIZATIONS:
    - Uses singleton pattern for model instance
    - Pre-emptive chunk splitting
    - Memory-efficient concatenation
    - Better error recovery
    """
    import numpy as np

    # Preprocess text to fix PDF artifacts and Kokoro-hostile characters
    text = _preprocess_for_kokoro(text)
    if not text.strip():
        raise ValueError("Empty text after preprocessing")

    voice_key, lang = _get_voice_config(voice_name, ENGINE_KOKORO)
    speed = max(0.5, min(2.0, speed))

    mp = _find_file(_KOKORO_MODEL_NAMES)
    vp = _find_file(_KOKORO_VOICES_NAMES)

    if mp is None or vp is None:
        raise FileNotFoundError(
            "Kokoro model files not found in ~/.ttsvoices/models/\n"
            "Open Voice Library → Download All Required."
        )

    # Try to get Kokoro instance — handle corrupted voices.bin
    try:
        kokoro = _kokoro_singleton.get_instance(mp, vp, _onnx_provider)
    except Exception as e:
        err_str = str(e).lower()
        if "zip bomb" in err_str or "overlapped" in err_str or "corrupt" in err_str:
            # voices.bin is corrupted — delete it and raise a clear error
            try:
                import os as _os
                _os.unlink(vp)
                _get_bug_tracker().error(
                    f"voices.bin corrupted (zip bomb / overlapped entries) — "
                    f"deleted {vp}. Re-download via Voice Library.")
            except Exception:
                pass
            _kokoro_singleton.clear()
            raise RuntimeError(
                "Kokoro voices file is corrupted and has been deleted.\n"
                "Open Voice Library → Kokoro ONNX tab → Download 'Voices Data'."
            )
        raise
    
    # Pre-emptive chunk splitting
    chunks = ChunkProcessor.chunk_text_safe(text)
    
    if len(chunks) <= 1:
        # Single chunk - direct synthesis
        # Acquire the global Kokoro lock to prevent concurrent ONNX session calls
        # from parallel threads (e.g. synthesize_batch with max_workers>1).
        # onnxruntime sessions are NOT thread-safe for concurrent run() calls —
        # simultaneous calls corrupt internal buffers and produce garbled audio.
        with _kokoro_lock:
            samples, sr = kokoro.create(text, voice=voice_key, speed=speed, lang=lang)
        return _create_wav(samples, sr)
    
    # Multiple chunks - concatenate
    all_pcm = []
    final_sr = 24000
    
    for chunk in chunks:
        if not chunk.strip():
            continue
        
        try:
            with _kokoro_lock:
                samples, sr = kokoro.create(
                    chunk, voice=voice_key, speed=speed, lang=lang
                )
            # Convert to PCM bytes immediately to free numpy array memory
            pcm = (samples * 32767).astype(np.int16).tobytes()
            all_pcm.append(pcm)
            final_sr = sr
        except Exception as e:
            err_str = str(e)
            if "510" in err_str or "out of bounds" in err_str:
                _get_bug_tracker().warning(
                    f"Phoneme overflow on chunk, retrying with smaller split: {chunk[:40]}"
                )
                # Emergency re-split with smaller chunks
                sub_chunks = ChunkProcessor.chunk_text_safe(chunk, max_words=30)
                for sub in sub_chunks:
                    if sub.strip():
                        try:
                            with _kokoro_lock:
                                ss, ssr = kokoro.create(
                                    sub, voice=voice_key, speed=speed, lang=lang
                                )
                            all_pcm.append((ss * 32767).astype(np.int16).tobytes())
                            final_sr = ssr
                        except Exception:
                            continue
            else:
                _get_bug_tracker().error(f"Kokoro chunk synthesis error: {e}")
                raise
    
    # Guard: if ALL chunks failed silently, raise so caller triggers espeak fallback
    if not all_pcm:
        raise RuntimeError(
            "Kokoro produced no audio for any chunk. "
            "All synthesis attempts failed silently."
        )

    # Concatenate all PCM data
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(final_sr)
        wf.writeframes(b"".join(all_pcm))

    return buf.getvalue()


def _create_wav(samples, sample_rate: int) -> bytes:
    """Create WAV bytes from numpy samples."""
    import numpy as np
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _synth_espeak(
    text: str,
    voice_name: str,
    speed: float = 1.0,
    pitch: float = 1.0
) -> bytes:
    """
    Synthesize using espeak-ng (or espeak as fallback binary).

    Edge cases handled:
      - Uses _ESPEAK_BINARY (resolved at import) not a hardcoded "espeak-ng"
      - Sets ESPEAK_DATA_PATH in subprocess env from _ESPEAK_DATA_DIR
      - Captures stderr and logs it (was silently swallowed before)
      - Detects the espeakng-loader phontab error and gives an actionable message
      - Falls back gracefully if the output WAV is empty/missing
      - Passes text via stdin (--stdin) to avoid ARG_MAX and shell injection
    """
    if not _ESPEAK_BINARY:
        raise RuntimeError(
            "espeak-ng is not installed.\n"
            "Fix: sudo apt install espeak-ng"
        )

    voice, _ = _get_voice_config(voice_name, ENGINE_ESPEAK)
    out = None

    # Build subprocess environment with patched data path
    env = dict(os.environ)
    if _ESPEAK_DATA_DIR:
        env["ESPEAK_DATA_PATH"] = _ESPEAK_DATA_DIR

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            out = f.name

        result = subprocess.run(
            [_ESPEAK_BINARY,
             "-v", voice,
             "-s", str(int(175 * speed)),
             "-p", str(int(50 * pitch)),
             "-w", out,
             "--stdin"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=30,
            env=env,
        )

        # Log stderr even on success (espeak prints warnings there)
        stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
        if stderr_text:
            # Filter the known espeakng-loader noise line so it isn't printed
            # to the terminal on every synthesis call.
            filtered = [
                ln for ln in stderr_text.splitlines()
                if "runner/work/espeakng-loader" not in ln
            ]
            if filtered:
                _get_bug_tracker().warning(
                    f"espeak-ng stderr: {'; '.join(filtered[:5])}"
                )

        if result.returncode != 0:
            # Surface a helpful message for the phontab error
            if "phontab" in stderr_text or "No such file" in stderr_text:
                raise RuntimeError(
                    "espeak-ng cannot find its data files (phontab missing).\n"
                    "Fix options:\n"
                    "  1. sudo apt install espeak-ng espeak-ng-data\n"
                    "  2. pip install --force-reinstall --no-cache-dir espeakng-loader\n"
                    "  3. export ESPEAK_DATA_PATH=/usr/lib/x86_64-linux-gnu/espeak-ng-data"
                )
            raise RuntimeError(
                f"espeak-ng exited with code {result.returncode}: {stderr_text[:200]}"
            )

        if not os.path.isfile(out) or os.path.getsize(out) <= 44:
            raise RuntimeError(
                "espeak-ng produced an empty WAV file. "
                f"stderr: {stderr_text[:200]}"
            )

        with open(out, "rb") as fh:
            return fh.read()

    except (RuntimeError, FileNotFoundError, PermissionError):
        raise
    except subprocess.TimeoutExpired:
        raise RuntimeError("espeak-ng synthesis timed out after 30 s")
    except Exception as e:
        _get_bug_tracker().error(f"espeak-ng synthesis error: {e}")
        raise

    finally:
        if out and os.path.exists(out):
            try:
                os.unlink(out)
            except OSError:
                pass


def _synth_edge_tts(
    text: str,
    voice_name: str,
    speed: float = 1.0,
    pitch: float = 1.0
) -> bytes:
    """
    Synthesize using Microsoft Edge TTS cloud API.

    No model download — text is sent to api.edge.microsoft.com which streams
    back MP3 audio. On this hardware (Intel i7-1065G7, CPU-only), this is
    ~7-9x faster than Kokoro while delivering higher quality (Azure Neural
    voices, the same ones used in the Edge browser Read Aloud feature).

    Speed 1.0 = "+0%" rate. Range: 0.5 ("-50%") to 2.0 ("+100%").
    Pitch is in Hz offset ("+0Hz" .. "+50Hz" / "-50Hz").

    Returns 16-bit mono 24 kHz WAV bytes (matches Kokoro's output format
    so downstream code doesn't care which engine produced them).

    Edge cases:
      - edge-tts package missing → clear Install message
      - Network unreachable → bubble a RuntimeError so the caller can
        fall back to Kokoro/espeak
      - ffmpeg missing (needed for MP3→WAV) → clear Install message
      - Empty audio from server → RuntimeError("Edge TTS returned no audio")
    """
    if not check_edge_tts():
        if "edge_tts" not in _engine_cache or not _engine_cache.get("edge_tts"):
            raise RuntimeError(
                "edge-tts package is not installed.\n"
                "Fix: pip install edge-tts"
            )
        raise RuntimeError(
            "Edge TTS is offline (cannot reach api.edge.microsoft.com).\n"
            "Check your network connection or pick a different engine."
        )

    config = EDGE_TTS_VOICES.get(voice_name, {})
    voice = config.get("voice", "en-US-AriaNeural")

    # Clamp and convert speed (0.5..2.0) to Edge TTS rate string ("+0%" .. "+100%")
    rate_pct = max(-50, min(100, int(round((speed - 1.0) * 100))))
    rate_str = f"{rate_pct:+d}%"

    # Convert pitch (0.5..2.0) to Hz offset. Edge TTS supports -50Hz..+50Hz.
    # 1.0 = no shift. We map 0.5→-50, 1.0→0, 2.0→+50 (rough but reasonable).
    pitch_hz = max(-50, min(50, int(round((pitch - 1.0) * 100))))
    pitch_str = f"{pitch_hz:+d}Hz"

    # Run the async client in a new event loop. ~5-10 ms overhead per call
    # is negligible compared to the 0.8-1.8 s network round-trip.
    async def _stream_to_bytes() -> bytes:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
        chunks: List[bytes] = []
        async for ev in communicate.stream():
            if ev.get("type") == "audio":
                chunks.append(ev["data"])
        return b"".join(chunks)

    try:
        mp3_data = asyncio.run(_stream_to_bytes())
    except Exception as e:
        raise RuntimeError(f"Edge TTS network call failed: {e}")

    if not mp3_data:
        raise RuntimeError("Edge TTS returned no audio (empty response)")

    # Verify ffmpeg is available for the MP3→WAV conversion
    ffmpeg_bin = shutil_which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError(
            "ffmpeg is required to decode Edge TTS audio.\n"
            "Fix: sudo apt install ffmpeg"
        )

    mp3_path = None
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
            f.write(mp3_data)
        wav_path = mp3_path[:-4] + ".wav"
        proc = subprocess.run(
            [ffmpeg_bin, "-y", "-loglevel", "error",
             "-i", mp3_path,
             "-ar", "24000",      # 24 kHz to match Kokoro output
             "-ac", "1",          # mono
             "-sample_fmt", "s16", # 16-bit signed PCM
             wav_path],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0 or not os.path.isfile(wav_path):
            err = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
            raise RuntimeError(f"ffmpeg MP3→WAV conversion failed: {err}")
        with open(wav_path, "rb") as fh:
            return fh.read()
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg MP3→WAV conversion timed out after 30 s")
    finally:
        for p in (mp3_path, wav_path):
            if p and os.path.exists(p):
                try: os.unlink(p)
                except OSError: pass


def shutil_which(name: str) -> Optional[str]:
    """Local copy of shutil.which to avoid the import at module top — keeps
    the dependency surface minimal and lets us return None on any failure."""
    try:
        import shutil
        return shutil.which(name)
    except Exception:
        return None


# ── Main Synthesis Entry Point ───────────────────────────────────────────────


def _synth_chatterbox(text: str, voice_name: str, speed: float = 1.0,
                      ref_audio: str = "") -> bytes:
    """
    Synthesize using Chatterbox TTS (Resemble AI open-source model).
    Requires: pip install chatterbox-tts
    ref_audio: optional path to a WAV/MP3 reference clip for zero-shot cloning.
    Falls back to espeak if Chatterbox is unavailable or errors.
    """
    import io
    try:
        from chatterbox.tts import ChatterboxTTS
        import torchaudio

        model = ChatterboxTTS.from_pretrained(device="cpu")

        gen_kwargs = {}
        if ref_audio and __import__("os").path.isfile(ref_audio):
            gen_kwargs["audio_prompt_path"] = ref_audio

        wav_tensor, sr = model.generate(text, **gen_kwargs)

        # Export to WAV bytes
        buf = io.BytesIO()
        torchaudio.save(buf, wav_tensor.squeeze(0).cpu(), sr, format="wav")
        buf.seek(0)
        return buf.read()
    except ImportError:
        _get_bug_tracker().warning("Chatterbox not installed — falling back to espeak")
        return _synth_espeak(text, "English (US)", speed, 1.0)
    except Exception as e:
        _get_bug_tracker().error(f"Chatterbox synthesis error: {e}")
        return _synth_espeak(text, "English (US)", speed, 1.0)


# ── Parallel Batch Synthesis ──────────────────────────────────────────────────

def _synth_omnivoice(text: str, speed: float = 1.0, ref_audio: str = "") -> bytes:
    """
    Synthesize using OmniVoice (k2-fsa multilingual zero-shot model).
    Requires: pip install omnivoice + torch
    ref_audio: optional path to reference audio for voice cloning.
    Falls back to espeak on failure.
    """
    import io
    try:
        from omnivoice import OmniVoice
        import torchaudio

        model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu")
        gen_kwargs = {}
        if ref_audio and __import__("os").path.isfile(ref_audio):
            gen_kwargs["ref_audio"] = ref_audio

        audio = model.generate(text=text, **gen_kwargs)
        buf = io.BytesIO()
        torchaudio.save(buf, audio.cpu(), model.sample_rate, format="wav")
        buf.seek(0)
        return buf.read()
    except ImportError:
        _get_bug_tracker().warning("OmniVoice not installed — falling back to espeak")
        return _synth_espeak(text, "English (US)", speed, 1.0)
    except Exception as e:
        _get_bug_tracker().error(f"OmniVoice synthesis error: {e}")
        return _synth_espeak(text, "English (US)", speed, 1.0)


def _synth_f5tts(text: str, speed: float = 1.0, ref_audio: str = "") -> bytes:
    """
    Synthesize using F5-TTS (flow-matching zero-shot model).
    Requires: pip install f5-tts
    ref_audio: required reference audio for voice cloning.
    Falls back to espeak on failure.
    """
    import io, tempfile, os
    try:
        from f5_tts.api import F5TTS
        import soundfile as sf
        import numpy as np

        model = F5TTS()
        if not ref_audio or not os.path.isfile(ref_audio):
            _get_bug_tracker().warning("F5-TTS requires reference audio — falling back to espeak")
            return _synth_espeak(text, "English (US)", speed, 1.0)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            out_path = tf.name
        try:
            model.infer(
                ref_file=ref_audio,
                ref_text="",
                gen_text=text,
                output_dir=os.path.dirname(out_path),
                output_file=os.path.basename(out_path),
            )
            with open(out_path, "rb") as f:
                return f.read()
        finally:
            try: os.unlink(out_path)
            except Exception: pass

    except ImportError:
        _get_bug_tracker().warning("F5-TTS not installed — falling back to espeak")
        return _synth_espeak(text, "English (US)", speed, 1.0)
    except Exception as e:
        _get_bug_tracker().error(f"F5-TTS synthesis error: {e}")
        return _synth_espeak(text, "English (US)", speed, 1.0)


def synthesize_batch(
    chunks:      list,           # list of text strings
    engine:      str,
    voice_name:  str,
    speed:       float = 1.0,
    pitch:       float = 1.0,
    stop_flag=None,              # threading.Event — checked between chunks
    progress_cb=None,            # callable(done_count, total) — progress updates
    max_workers: int = 0,        # 0 = auto-detect (min(4, cpu_count))
) -> list:
    """
    Synthesize a list of text chunks in parallel using a thread pool.

    ONNX Runtime releases the Python GIL during inference, so
    ThreadPoolExecutor gives true hardware parallelism across CPU cores.
    On a 4-core CPU this is typically 2–3.5× faster than sequential synthesis.

    Returns a list of bytes (WAV data) in the SAME ORDER as the input chunks.
    If a chunk fails, its slot contains None (skipped during playback/export).

    Strategy:
      - max_workers defaults to min(4, os.cpu_count() or 2)
      - Workers are capped at 4 because Kokoro ONNX inference is memory-bound;
        beyond 4 threads the ONNX session locks start to dominate.
      - Each worker calls synthesize() which handles fallback automatically.
    """
    import concurrent.futures
    import os

    if not chunks:
        return []

    n = len(chunks)
    if max_workers <= 0:
        max_workers = min(4, os.cpu_count() or 2)

    # For very short lists (≤2 chunks) sequential is fine — avoids thread overhead
    if n <= 2:
        results = []
        for i, chunk in enumerate(chunks):
            if stop_flag and stop_flag.is_set():
                results.append(None)
                continue
            try:
                results.append(synthesize(chunk, engine, voice_name, speed, pitch))
            except Exception as e:
                _get_bug_tracker().error(f"synthesize_batch chunk {i}: {e}")
                results.append(None)
            if progress_cb:
                try: progress_cb(i + 1, n)
                except Exception: pass
        return results

    results    = [None] * n
    done_count = [0]
    lock       = threading.Lock()

    def _do(idx_chunk):
        idx, chunk = idx_chunk
        if stop_flag and stop_flag.is_set():
            return idx, None
        try:
            wav = synthesize(chunk, engine, voice_name, speed, pitch)
            return idx, wav
        except Exception as e:
            _get_bug_tracker().error(f"synthesize_batch chunk {idx}: {e}")
            return idx, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_do, (i, c)): i for i, c in enumerate(chunks)}
        for fut in concurrent.futures.as_completed(futures):
            if stop_flag and stop_flag.is_set():
                # Cancel remaining futures (best-effort — already-running ones finish)
                for f in futures:
                    f.cancel()
                break
            try:
                idx, wav = fut.result()
                results[idx] = wav
            except Exception as e:
                _get_bug_tracker().error(f"synthesize_batch future: {e}")
            with lock:
                done_count[0] += 1
            if progress_cb:
                try: progress_cb(done_count[0], n)
                except Exception: pass

    return results


def synthesize(
    text: str, 
    engine: str, 
    voice_name: str, 
    speed: float = 1.0, 
    pitch: float = 1.0
) -> bytes:
    """
    Synthesize text to speech.
    
    OPTIMIZATIONS:
    - Thread-safe fallback signaling
    - Better error recovery chain
    - Automatic fallback to espeak if Kokoro fails
    """
    global last_engine_used, fallback_occurred
    fallback_event.clear()
    fallback_occurred = False

    # Try requested engine first
    try:
        if engine == ENGINE_KOKORO:
            wav = _synth_kokoro(text, voice_name, speed)
            last_engine_used = ENGINE_KOKORO
            return wav

        if engine == ENGINE_EDGE_TTS:
            wav = _synth_edge_tts(text, voice_name, speed, pitch)
            last_engine_used = ENGINE_EDGE_TTS
            return wav

        if engine == ENGINE_CHATTERBOX:
            wav = _synth_chatterbox(text, voice_name, speed)
            last_engine_used = ENGINE_CHATTERBOX
            return wav

        if engine == ENGINE_ESPEAK:
            wav = _synth_espeak(text, voice_name, speed, pitch)
            last_engine_used = ENGINE_ESPEAK
            return wav

    except Exception as e:
        err_str = str(e)
        _get_bug_tracker().error(f"Engine {engine} failed: {e}")

        # Before falling back to espeak, retry Kokoro with aggressive preprocessing
        # This fixes "number of lines" errors from PDF-merged text
        if engine == ENGINE_KOKORO and "number of lines" in err_str:
            try:
                clean = _preprocess_for_kokoro(text)
                # Also strip any remaining non-letter runs
                import re as _re
                clean = _re.sub(r'[^\w\s\'\-\.,;:!?]', ' ', clean)
                clean = _re.sub(r'\s+', ' ', clean).strip()
                if clean and clean != text:
                    _get_bug_tracker().info("Retrying Kokoro with cleaned text")
                    wav = _synth_kokoro(clean, voice_name, speed)
                    last_engine_used = ENGINE_KOKORO
                    return wav
            except Exception as e2:
                _get_bug_tracker().warning(f"Kokoro retry also failed: {e2}")

        # Edge TTS failed — try Kokoro as offline fallback before espeak
        if engine == ENGINE_EDGE_TTS and check_kokoro():
            try:
                _get_bug_tracker().info("Edge TTS failed, falling back to Kokoro")
                wav = _synth_kokoro(text, voice_name, speed)
                last_engine_used = ENGINE_KOKORO
                fallback_occurred = True
                fallback_event.set()
                return wav
            except Exception as e2:
                _get_bug_tracker().warning(f"Kokoro fallback after Edge TTS failed: {e2}")

        _get_bug_tracker().warning("Attempting fallback...")

    # Fallback to espeak if Kokoro failed
    if engine != ENGINE_ESPEAK and check_espeak():
        try:
            # Use the user's selected voice for the espeak fallback too —
            # was hardcoded to "English (US)" which meant picking "English (UK)"
            # silently produced a US voice on Kokoro failure.
            _voice_key, _lang = _get_voice_config(voice_name, engine)
            espeak_voice = voice_name if voice_name else "English (US)"
            wav = _synth_espeak(text, espeak_voice, speed, pitch)
            last_engine_used = ENGINE_ESPEAK
            fallback_occurred = True
            fallback_event.set()
            _get_bug_tracker().warning(
                f"Fallback to espeak-ng for chunk: {text[:40]!r}"
            )
            return wav
        except Exception as e:
            _get_bug_tracker().error(f"Fallback to espeak also failed: {e}")

    raise RuntimeError(
        "All TTS engines failed.\n"
        "For Kokoro: open Voice Library → Download All Required.\n"
        "For Edge TTS: pip install edge-tts + network access.\n"
        "Fallback: sudo apt install espeak-ng"
    )


# ── Public API for Chunking ───────────────────────────────────────────────────
def chunk_text(text: str, max_words: int = 80) -> List[str]:
    """
    Public API for text chunking.
    
    Uses ChunkProcessor for phoneme-aware splitting.
    """
    return ChunkProcessor.chunk_text_safe(text, max_words)


# ── Public API for Word Timing ────────────────────────────────────────────────
def get_word_timings(
    text: str, 
    speed: float = 1.0
) -> List[Tuple[str, float, float]]:
    """
    Get estimated word timings for highlighting.
    
    Returns list of (word, start_time, end_time) tuples.
    """
    return WordTimingEstimator.estimate_word_timings(text, speed)


def estimate_duration(text: str, speed: float = 1.0) -> float:
    """Estimate synthesis duration in seconds."""
    return WordTimingEstimator.estimate_chunk_duration(text, speed)


# ── Public API for Streaming Synthesis ────────────────────────────────────────
def create_streaming_synthesizer(buffer_size: int = 10) -> StreamingSynthesizer:
    """
    Create a streaming synthesizer for memory-efficient long text synthesis.
    
    Args:
        buffer_size: Number of recent chunks to keep for replay
    
    Returns:
        StreamingSynthesizer instance
    """
    return StreamingSynthesizer(buffer_size)


# ── Cleanup Function ──────────────────────────────────────────────────────────
def cleanup():
    """
    Release resources and clear caches.
    
    Call this when shutting down the application.
    """
    _kokoro_singleton.clear()
    _engine_cache.clear()
    _get_voice_config.cache_clear()
    _get_bug_tracker().info("TTS voices module cleaned up")
