"""
save_point_manager.py — TTS Voices 2.1
Persistent per-file save points stored under ~/.ttsvoices/savepoints/
Taken from Base 44 reference implementation (superior to in-memory config approach).

Improvements over the config-based bookmark:
- Each file gets its own JSON keyed by MD5 hash of the path
- Survives app restarts without polluting the main config
- toggle_reset allows one-shot "play from beginning" without clearing the save point
"""
import os, json, hashlib

SAVEPOINTS_DIR = os.path.expanduser('~/.ttsvoices/savepoints')


class SavePointManager:
    def __init__(self):
        os.makedirs(SAVEPOINTS_DIR, exist_ok=True)
        self.saved_chunk    = None   # int chunk index
        self.reset_toggle   = False  # True = play from beginning once, then restore
        self.current_file   = None

    # ── Load / save ───────────────────────────────────────────────────────
    def load_for_file(self, file_path: str):
        """Load the saved chunk index for a given file path."""
        self.current_file = file_path
        data = self._read(file_path)
        self.saved_chunk  = data.get('chunk')
        self.reset_toggle = False

    def set_save_point(self, chunk_idx: int):
        """Persist the current chunk index as the save point."""
        self.saved_chunk = chunk_idx
        if self.current_file:
            self._write(self.current_file, {'chunk': chunk_idx})

    def get_start_chunk(self) -> int:
        """Return 0 if reset_toggle is on, else the saved chunk index."""
        if self.reset_toggle or self.saved_chunk is None:
            return 0
        return self.saved_chunk

    # ── Controls ──────────────────────────────────────────────────────────
    def toggle_reset(self) -> bool:
        """Toggle 'start from beginning' mode. Returns new state."""
        self.reset_toggle = not self.reset_toggle
        return self.reset_toggle

    def clear_save_point(self):
        """Remove this file's save point from disk and memory."""
        self.saved_chunk  = None
        self.reset_toggle = False
        if self.current_file:
            p = self._path_for(self.current_file)
            if os.path.exists(p):
                os.unlink(p)

    def clear_all(self):
        """Remove all save points from disk."""
        for f in os.listdir(SAVEPOINTS_DIR):
            try:
                os.unlink(os.path.join(SAVEPOINTS_DIR, f))
            except OSError:
                pass
        self.saved_chunk = None

    def has_save_point(self) -> bool:
        return self.saved_chunk is not None and self.saved_chunk > 0

    # ── Internal ──────────────────────────────────────────────────────────
    def _path_for(self, file_path: str) -> str:
        h = hashlib.md5(file_path.encode()).hexdigest()
        return os.path.join(SAVEPOINTS_DIR, f'{h}.json')

    def _read(self, file_path: str) -> dict:
        p = self._path_for(file_path)
        if os.path.exists(p):
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _write(self, file_path: str, data: dict):
        p = self._path_for(file_path)
        try:
            with open(p, 'w') as f:
                json.dump(data, f)
        except OSError:
            pass
