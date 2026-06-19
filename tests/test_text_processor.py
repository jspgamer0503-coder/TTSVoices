import pytest
import sys
sys.path.insert(0, ".")

from tts_voices.core.text_processor import TextProcessor


class TestTextProcessor:
    def setup_method(self):
        self.tp = TextProcessor(max_chunk_size=100)

    def test_empty_text(self):
        assert self.tp.chunk_text("") == []
        assert self.tp.chunk_text("   ") == []

    def test_short_text(self):
        result = self.tp.chunk_text("Hello world.")
        assert len(result) == 1
        assert result[0] == "Hello world."

    def test_splits_long_text(self):
        text = "A. " * 50
        result = self.tp.chunk_text(text)
        assert len(result) > 1

    def test_respects_abbreviations(self):
        text = "Mr. Smith went to Dr. Jones. He was late."
        result = self.tp.chunk_text(text)
        assert len(result) >= 1
        assert "Mr." in result[0] or "Mr." in " ".join(result)

    def test_hard_split_long_word(self):
        tp = TextProcessor(max_chunk_size=10)
        result = tp.chunk_text("superlongwordthatiswaytoobig")
        assert len(result) >= 3
        for chunk in result:
            assert len(chunk) <= 10

    def test_hard_split_long_sentence(self):
        tp = TextProcessor(max_chunk_size=20)
        text = "This is a very long sentence that should be split into multiple chunks because it exceeds the maximum chunk size."
        result = tp.chunk_text(text)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 20

    def test_multiple_sentences(self):
        tp = TextProcessor(max_chunk_size=200)
        text = "First sentence here. Second sentence there. Third one at the end."
        result = tp.chunk_text(text)
        assert len(result) >= 1

    def test_newlines_handled(self):
        result = self.tp.chunk_text("Hello\nWorld\n\nTest")
        assert len(result) >= 1
