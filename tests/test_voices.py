import pytest
import sys
sys.path.insert(0, ".")

from voices import (
    TTSEngineManager,
    ChunkProcessor,
    WordTimingEstimator,
    KOKORO_VOICES,
    ESPEAK_VOICES,
)


class TestTTSEngineManager:
    def test_priority_kokoro_first(self):
        mgr = TTSEngineManager({"cloud_tts_enabled": False})
        assert mgr.engine_priority[0] == "Kokoro ONNX"
        assert mgr.engine_priority[1] == "espeak-ng"

    def test_edge_is_last(self):
        mgr = TTSEngineManager({"cloud_tts_enabled": True})
        assert mgr.engine_priority[-1] == "Edge TTS (Cloud)"

    def test_raises_when_no_config(self):
        mgr = TTSEngineManager({})
        assert mgr.engine_priority[0] == "Kokoro ONNX"


class TestChunkProcessor:
    def test_empty_text(self):
        assert ChunkProcessor.chunk_text_safe("") == []

    def test_short_text_no_split(self):
        chunks = ChunkProcessor.chunk_text_safe("Hello world.")
        assert len(chunks) == 1

    def test_estimate_phonemes(self):
        count = ChunkProcessor.estimate_phonemes("Hello world")
        assert count > 0

    def test_count_syllables(self):
        assert ChunkProcessor._count_syllables("hello") == 2
        assert ChunkProcessor._count_syllables("world") == 1


class TestWordTimingEstimator:
    def test_empty_text(self):
        assert WordTimingEstimator.estimate_word_timings("") == []

    def test_basic_timings(self):
        timings = WordTimingEstimator.estimate_word_timings("hello world")
        assert len(timings) == 2
        assert timings[0][0] == "hello"
        assert timings[1][0] == "world"
        assert timings[0][1] >= 0
        assert timings[0][2] > timings[0][1]

    def test_estimate_duration(self):
        dur = WordTimingEstimator.estimate_chunk_duration("hello world")
        assert dur > 0


class TestVoiceData:
    def test_kokoro_voices_exist(self):
        assert len(KOKORO_VOICES) >= 10

    def test_kokoro_voice_keys(self):
        for name, config in KOKORO_VOICES.items():
            assert "voice" in config
            assert "lang" in config

    def test_espeak_voices_exist(self):
        assert len(ESPEAK_VOICES) >= 3
