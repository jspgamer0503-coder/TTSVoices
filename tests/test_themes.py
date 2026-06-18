import pytest
import sys
sys.path.insert(0, ".")

from tts_voices.gui.themes import THEMES, get_theme, list_themes


class TestThemes:
    def test_all_themes_have_required_keys(self):
        required = {"bg", "surface", "accent", "text", "muted"}
        for key, palette in THEMES.items():
            missing = required - set(palette.keys())
            assert not missing, f"Theme {key!r} missing: {missing}"

    def test_get_theme_returns_copy(self):
        t = get_theme("dark")
        t["accent"] = "#modified"
        assert THEMES["dark"]["accent"] != "#modified"

    def test_get_theme_fallback(self):
        t = get_theme("nonexistent")
        assert t["accent"] == THEMES["dark"]["accent"]

    def test_list_themes(self):
        items = list_themes()
        assert len(items) >= 2
        keys = [k for k, _ in items]
        assert "dark" in keys
        assert "light" in keys
