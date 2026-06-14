"""
TTS Voices Plugin — Word Counter

Maintained by the opencode AI assistant — see README.md.
Example plugin. Shows a word/character count in the nav bar.

Install: copy this file to ~/.ttsvoices/plugins/word_counter.py
Restart the app (or open Settings and click + Add Plugin).
"""

_app = None
_btn = None

def register(app):
    """Called by TTS Voices at startup. app is the TTSVoicesApp instance."""
    global _app
    _app = app

    # Add a nav button showing word count
    app.add_nav_button("W: —", _on_click)

    # Update count whenever speech starts (text is loaded)
    app.on_speak_start(_update_count)


def _update_count():
    """Refresh the word count button label."""
    if _app is None:
        return
    text = _app.get_current_text().strip()
    words = len(text.split()) if text else 0
    chars = len(text)
    try:
        # Find our button in the nav bar by its label prefix
        for btn in getattr(_app, "_nav_btns", []):
            label = btn._lbl.cget("text")
            if label.startswith("W:"):
                btn._lbl.configure(text=f"W: {words:,}  C: {chars:,}")
                break
    except Exception:
        pass


def _on_click():
    """Button click: show a quick summary."""
    if _app is None:
        return
    text = _app.get_current_text().strip()
    words = len(text.split()) if text else 0
    chars = len(text)
    sentences = text.count(".") + text.count("!") + text.count("?")
    mins = round(words / 150)  # ~150 wpm average reading speed
    _app.set_status(f"{words:,} words · {chars:,} chars · ~{mins} min read", "")
