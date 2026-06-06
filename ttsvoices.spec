# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import sys

block_cipher = None

a = Analysis(
    ['ttsvoices.py'],
    pathex=['.'],
    binaries=[('audio_fast.so', '.')],
    datas=collect_data_files('ttsvoices') + [
        ('example_plugins', 'example_plugins'),
    ],
    hiddenimports=[
        'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox',
        'kokoro_onnx', 'onnxruntime', 'numpy', 'edge_tts', 'aiohttp',
        'pdfplumber', 'pypdf', 'docx', 'ebooklib', 'bs4', 'lxml',
        'striprtf', 'chardet', 'pikepdf', 'msoffcrypto', 'Crypto',
        'argon2', 'faster_whisper',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter.test'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
          name='ttsvoices', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
          console=False, icon=None)
