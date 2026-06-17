"""
TTS Voices 2.5.2 - Custom Exceptions

Maintained by the opencode AI assistant — see README.md.
Provides typed exceptions for cleaner error handling across all modules.
"""


class TTSError(Exception):
    """Base exception for all TTS Voices errors."""
    pass


class EngineNotAvailableError(TTSError):
    """Raised when the requested TTS engine is not installed or has no model files."""
    pass


class SynthesisError(TTSError):
    """Raised when text-to-speech synthesis fails on all available engines."""
    pass


class ModelNotFoundError(TTSError):
    """Raised when Kokoro model files are missing from ~/.ttsvoices/models/."""
    pass


class PhonemeOverflowError(SynthesisError):
    """Raised when a text chunk exceeds Kokoro's 510-phoneme limit after re-splitting."""
    pass


class FileExtractionError(TTSError):
    """Raised when text cannot be extracted from a document file."""
    pass


class EncryptedFileError(FileExtractionError):
    """Raised when a file is password-protected and no password was provided."""
    pass


class WrongPasswordError(FileExtractionError):
    """Raised when the provided password cannot decrypt the file."""
    pass


class UnsupportedFormatError(FileExtractionError):
    """Raised when the file extension is not in the supported formats list."""
    pass


class AudioExportError(TTSError):
    """Raised when WAV or MP3 export fails."""
    pass
