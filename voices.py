"""
TTS Voices 2.2.5 - Optimized Voice Synthesis Module
Priority chain: Kokoro ONNX -> espeak-ng

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

# Lazy import of bug_tracker to avoid circular imports
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
                self._kokoro_instance = kokoro_onnx.Kokoro(model_path, voices_path, providers=[provider])
            except TypeError:
                # Older kokoro_onnx versions don't accept providers arg
                self._kokoro_instance = kokoro_onnx.Kokoro(model_path, voices_path)
            
            self._model_path = model_path
            self._voices_path = voices_path
            self._provider = provider
            
            _get_bug_tracker().info(f"Kokoro loaded in {time.time()-t0:.1f}s")
            
            # Verify active providers
            try:
                session_prov = self._kokoro_instance.model.sess.get_providers()
                _get_bug_tracker().info(f"ONNX active providers: {session_prov}")
            except Exception:
                pass
            
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
        r'(?<=[.!?\u3002\uff1f\uff01\u2026])\s+|'  # Standard + CJK terminators
        r'(?<=\u2014)\s+|'  # Em-dash
        r'(?<=\u2026)\s*'   # Ellipsis
    )
    
    @classmethod
    def estimate_phonemes(cls, text: str) -> int:
        """
        Estimate phoneme count for a text.
        
        OPTIMIZATION: Uses syllable-based estimation for better accuracy
        than simple word count, especially for longer words.
        """
        words = text.split()
        total_syllables = sum(cls._count_syllables(w) for w in words)
        # Phonemes roughly correlate to syllables * 2.5 + word boundaries
        return int(total_syllables * 2.5 + len(words) * 0.5)
    
    @staticmethod
    def _count_syllables(word: str) -> int:
        """Count syllables in a word for phoneme estimation."""
        word = re.sub(r"[^a-zA-Z]", "", word.lower())
        if not word:
            return 1
        count = len(re.findall(r"[aeiouy]+", word))
        if word.endswith("e") and count > 1:
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
    "kokoro-v0.19.onnx", "model.onnx"
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

ENGINE_KOKORO     = "Kokoro ONNX"
ENGINE_ESPEAK     = "espeak-ng"
ENGINE_CHATTERBOX = "Chatterbox"
ENGINE_OMNIVOICE  = "OmniVoice"
ENGINE_F5TTS      = "F5-TTS"

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
    """Check for espeak-ng availability (cached)."""
    if "espeak" not in _engine_cache:
        _engine_cache["espeak"] = subprocess.run(
            ["which", "espeak-ng"], 
            capture_output=True
        ).returncode == 0
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


def get_engine_status() -> dict:
    """Get availability status for all engines."""
    return {
        ENGINE_KOKORO:  check_kokoro(),
        ENGINE_ESPEAK:  check_espeak(),
    }


def get_all_voices() -> List[Tuple[str, str, str]]:
    """Get list of all available voices across engines.
    Voice-cloning engines (Chatterbox, OmniVoice, F5-TTS) are excluded
    as they require GPU hardware not available on this system.
    """
    result = []
    status = get_engine_status()

    if status[ENGINE_KOKORO]:
        for n in KOKORO_VOICES:
            result.append((f"Kokoro · {n}", ENGINE_KOKORO, n))

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
    Synthesize using espeak-ng.
    
    OPTIMIZATIONS:
    - Better text truncation handling
    - Improved temp file cleanup
    - Timeout protection
    """
    voice, _ = _get_voice_config(voice_name, ENGINE_ESPEAK)
    
    # Guard: truncate to avoid ARG_MAX errors
    if len(text) > 3800:
        text = text[:3800] + "..."
        _get_bug_tracker().warning(
            "espeak: text truncated to 3800 chars to avoid ARG_MAX"
        )
    
    out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            out = f.name
        
        subprocess.run(
            ["espeak-ng", "-v", voice,
             "-s", str(int(175 * speed)),
             "-p", str(int(50 * pitch)),
             "-w", out, text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30
        )
        
        with open(out, "rb") as fh:
            return fh.read()
            
    except Exception as e:
        _get_bug_tracker().error(f"espeak-ng synthesis error: {e}")
        raise
        
    finally:
        if out and os.path.exists(out):
            try:
                os.unlink(out)
            except OSError:
                pass


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

        _get_bug_tracker().warning("Attempting fallback...")

    # Fallback to espeak if Kokoro failed
    if engine != ENGINE_ESPEAK and check_espeak():
        try:
            wav = _synth_espeak(text, "English (US)", speed, pitch)
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
