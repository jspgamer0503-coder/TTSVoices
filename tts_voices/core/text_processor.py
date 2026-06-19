import re
import logging
from typing import List

logger = logging.getLogger(__name__)


class TextProcessor:
    """Handles text chunking and preparation for TTS engines."""

    ABBREVIATIONS = {
        'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc',
        'fig', 'no', 'vol', 'pp', 'll', 'mm', 'gen', 'col', 'capt', 'inc',
        'ltd', 'co', 'dept', 'est', 'govt', 'misc', 'corp',
    }

    def __init__(self, max_chunk_size: int = 1500):
        self.max_chunk_size = max_chunk_size

    def chunk_text(self, text: str) -> List[str]:
        if not text or not text.strip():
            return []

        text = text.strip()

        if len(text) <= self.max_chunk_size:
            return [text]

        chunks = []
        current_chunk = ""
        sentences = self._split_sentences(text)

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if len(current_chunk) + len(sentence) + 1 <= self.max_chunk_size:
                current_chunk += " " + sentence if current_chunk else sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)

                if len(sentence) > self.max_chunk_size:
                    sub_chunks = self._hard_split(sentence)
                    chunks.extend(sub_chunks)
                    current_chunk = ""
                else:
                    current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        logger.debug(f"Split text into {len(chunks)} chunks")
        return chunks

    def _split_sentences(self, text: str) -> List[str]:
        text = re.sub(r'\s+', ' ', text)
        # Replace abbreviation periods with placeholder to avoid false splits
        for abbr in self.ABBREVIATIONS:
            text = re.sub(rf'\b{abbr}\.', f'{abbr}_ABBR_DOT_', text, flags=re.IGNORECASE)
        sentences = re.split(r'\. ', text)
        for i in range(len(sentences) - 1):
            if not sentences[i].endswith('.'):
                sentences[i] += '.'
        # Restore abbreviation periods
        result = [s.replace('_ABBR_DOT_', '.') for s in sentences]
        return result

    def _hard_split(self, text: str) -> List[str]:
        chunks = []
        words = text.split(' ')
        current = ""

        for word in words:
            if len(current) + len(word) + 1 <= self.max_chunk_size:
                current += " " + word if current else word
            else:
                if current:
                    chunks.append(current)
                if len(word) > self.max_chunk_size:
                    for i in range(0, len(word), self.max_chunk_size):
                        chunks.append(word[i:i + self.max_chunk_size])
                    current = ""
                else:
                    current = word

        if current:
            chunks.append(current)

        return chunks
