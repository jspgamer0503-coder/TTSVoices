"""Entry point for TTS Voices.

This module is part of the package structure (tts_voices/).
The legacy `ttsvoices.py` in the project root remains the primary entry point
for backward compatibility. This file provides import-based access.
"""

from tts_voices.gui.themes import get_theme, list_themes, THEMES

__all__ = ["get_theme", "list_themes", "THEMES"]
