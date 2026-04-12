# TTS Voices Application
# Comprehensive Creation Plan
## Architecture, Security & User Experience Enhancement
### Combined Edition - Versions 2.0 & 2.1

GPU/CPU Toggle | Theme System | Voice Controls | ETA Display | Single-Window Architecture | Extended File Support | Themed File Browser | Advanced Security | Extended Color Palette | Fullscreen Mode | Text Highlighting | Universal Theme Support | Save Point System

---

# Table of Contents

1. Executive Summary
2. Application Overview
   - 2.1 Core Features
   - 2.2 Target Platform
   - 2.3 System Requirements
3. Technology Stack Analysis
   - 3.1 Programming Language
   - 3.2 GUI Framework
   - 3.3 TTS Engine Selection
   - 3.4 File Format Support Matrix
   - 3.5 Security Libraries
4. Architecture Design
   - 4.1 High-Level Architecture
   - 4.2 Single-Window Tabbed Interface
   - 4.3 Module Structure
   - 4.4 Themed File Browser Architecture
   - 4.5 Data Flow & Security
5. Module Specifications
   - 5.1 Main Application Module (Tabbed)
   - 5.2 Voice Synthesis Module
   - 5.3 Audio Handler Module
     - 5.3.1 Save Point System
   - 5.4 File Extractor & Decryption Module
   - 5.5 Themed File Browser Module
   - 5.6 Theme Manager (Extended)
   - 5.7 Security Manager
   - 5.8 Bug Tracker (Enhanced)
6. Voice Synthesis System
   - 6.1 Recommended TTS Models
   - 6.2 Model Performance Comparison
   - 6.3 Text Highlighting During Playback
7. GPU/CPU Toggle Support
   - 7.1 Hardware Detection
   - 7.2 Toggle Implementation
   - 7.3 Performance Impact
8. Theme System (Extended Palette)
   - 8.1 Built-in Themes (10 Presets)
   - 8.2 Custom Theme Support
   - 8.3 Theme Architecture
   - 8.4 Universal Theme Application
9. Voice Controls (Speed, Pitch, Volume)
   - 9.1 Speed Control
   - 9.2 Pitch Control
   - 9.3 Volume Control
   - 9.4 Control Panel Layout
10. Processing Time & ETA Display
    - 10.1 Progress Bar Implementation
    - 10.2 ETA Calculation
    - 10.3 Chunk Status Display
11. Settings, Security & ElevenLabs Integration
    - 11.1 Settings Panel Overview
    - 11.2 ElevenLabs API Integration
    - 11.3 File Security & Decryption
    - 11.4 Master Password & Keyring
    - 11.5 API Key Input Field
    - 11.6 Configuration File Structure
12. File Browser & Export System
    - 12.1 Themed File Browser Design
    - 12.2 Audio Export (WAV/MP3/OGG/FLAC)
    - 12.3 Audio Import Support
13. Best Practices Implementation
    - 13.1 Error Handling Strategy
    - 13.2 Memory Management
    - 13.3 Thread Safety
14. Testing Strategy
    - 14.1 Unit Testing
    - 14.2 Integration Testing
15. Deployment Plan
    - 15.1 Installation Script
16. Risk Assessment & Mitigation
17. Development Roadmap
18. Conclusion
19. Appendix A: Keyboard Shortcuts
20. Appendix B: Supported File Formats Detail

---

# 1. Executive Summary

This document presents a comprehensive creation plan for the TTS Voices application, a Linux desktop Text-to-Speech (TTS) solution designed to handle unlimited text input with professional-grade voice synthesis capabilities. The application leverages modern neural TTS models including Kokoro ONNX with Intel GPU acceleration support, and ElevenLabs API integration for premium cloud-based voices.

**Version 2.0** introduces significant enhancements including GPU/CPU toggle support, theme customization, advanced voice controls, and real-time processing time estimation. The plan emphasizes a clean architecture with clear separation of concerns, enabling maintainable and extensible code organization. Key technical decisions include Python 3.12 as the primary language, Tkinter for the GUI framework optimized for Linux desktop integration, and a multi-tier voice synthesis engine with automatic fallback chains. The architecture has been specifically optimized for Intel integrated graphics (Iris Plus, UHD, Xe) through OpenVINO integration, providing hardware acceleration options while maintaining full CPU compatibility.

**Version 2.1** represents a significant evolution from Version 2.0. While maintaining the core strengths of unlimited text processing, neural voice synthesis, and GPU acceleration, this version introduces critical architectural improvements: a unified single-window interface eliminating window clutter, an extensive file format support system covering 20+ formats with decryption capabilities, and a fully themed file browser replacing system dialogs.

Key architectural shifts in V2.1 include the migration from multi-window modals to a tabbed interface with slide-out panels, ensuring the application remains cohesive regardless of user actions. The file handling system now supports password-protected PDFs, encrypted Office documents, and archive decryption, integrated seamlessly into a custom file browser that maintains visual consistency with the selected color theme.

Version 2.1 expands the visual customization from three basic themes to ten distinct color palettes (Midnight, Arctic, Ruby, Ocean, Forest, Sunset, Royal, Sakura, Golden, Emerald), each professionally designed for specific use cases from accessibility to creative workflows. The custom file browser ensures these themes persist through every file operation, with smooth scaling animations and breadcrumb navigation.

Security enhancements in V2.1 include a master password system using the OS keyring, session-based password caching for batch operations, and secure handling of encrypted documents. The bug tracker now features intelligent log management with auto-clearing capabilities and export functionality.

---

# 2. Application Overview

## 2.1 Core Features

The TTS Voices application provides a professional-grade text-to-speech solution with the following enhanced capabilities:

### Input & Processing

- **Universal File Support**: Native handling of PDF (encrypted/decrypted), DOCX, DOC, ODT, RTF, TXT, MD, EPUB, MOBI, AZW3, FB2, HTML, CSV, XLSX, XLS, JSON, XML, ZIP, 7Z, TAR, and GZ formats
- **Audio Import**: Load WAV, MP3, OGG, FLAC, M4A, and AAC files for analysis, conversion, or playback alongside TTS
- **Decryption Engine**: Automatic detection and password handling for protected PDFs, Microsoft Office documents (DOCX, XLSX, PPTX), LibreOffice documents (ODT, ODS, ODP), OpenDocument formats, and encrypted archives (ZIP, 7Z, RAR)
- **Unlimited Text Processing**: Intelligent chunking for documents exceeding 1 million words with memory-efficient streaming

### Architecture & UI

- **Single-Window Design**: Tabbed interface (Main, Voice Library, Themes, Settings, Logs) prevents window proliferation
- **Themed File Browser**: Custom file dialogs matching the active theme (no system default dialogs)
- **Universal Theme Support**: ALL UI elements follow the selected theme - dialogs, popups, alerts, tooltips, context menus, progress dialogs, password prompts, confirmation boxes, and error messages
- **Extended Themes**: 10 preset color schemes plus custom theme builder
- **Fullscreen Mode**: Dedicated F-key toggle for distraction-free operation
- **Smooth Scaling**: Consistent icon/text scaling within file browser navigation
- **Text Highlighting**: Real-time word/sentence highlighting during playback with customizable highlight styles

### Audio & Controls

- **Multi-Format Export**: WAV, MP3, OGG, FLAC, M4A with quality presets (Low/Medium/High/Audiophile)
- **Voice Controls**: Real-time speed (0.5x-2.0x), pitch (-50% to +50%), volume (0-150%)
- **GPU/CPU Toggle**: OpenVINO acceleration with power-saving fallback
- **ETA Display**: Real-time progress with chunk status
- **Text Highlighting During Playback**: Visual highlighting of words, sentences, or paragraphs as they are spoken, with smooth scrolling to keep highlighted text visible
- **Save Point System**: Bookmark playback positions within audio to resume later, with reset toggle to start from beginning

### Security & Maintenance

- **Password Management**: Secure storage via system keyring, session caching for batch operations
- **Log Management**: Manual clear, auto-clear at threshold, archive export
- **API Security**: Encrypted ElevenLabs key storage

### Additional Features from V2.0

- **Voice Model Manager**: Built-in graphical interface for downloading, installing, and managing voice models from HuggingFace repositories
- **Neural Voice Synthesis**: Integration with multiple TTS engines including Kokoro ONNX (82M parameters, high quality offline), MeloTTS, Sherpa-ONNX, and cloud-based ElevenLabs API for premium voices

## 2.2 Target Platform

**Primary**: Linux desktop environments (Debian/Ubuntu, Linux Mint, Fedora, Arch)

**Hardware focus**: Intel integrated graphics (Iris Plus, UHD, Xe) with OpenVINO optimization

**Python environment**: 3.10+ with PEP 668 externally-managed environment support

The application is designed primarily for Linux desktop environments, with specific optimization for Debian-based distributions including Ubuntu, Linux Mint, and Kali Linux. The architecture accommodates PEP 668 externally-managed Python environment restrictions through automatic virtual environment creation. Hardware optimization focuses on Intel integrated graphics (Iris Plus, UHD, Xe) commonly found in laptops and desktop systems, while maintaining full CPU fallback compatibility.

## 2.3 System Requirements

| Component | Minimum | Recommended | Optimal |
|-----------|---------|-------------|---------|
| Python | 3.10+ | 3.11+ | 3.12+ |
| RAM | 4 GB | 8 GB | 16 GB |
| CPU | Dual-core 2.0GHz | Quad-core 2.5GHz | 8-core 3.0GHz+ |
| GPU | Not required | Intel Iris/UHD | Intel Xe/OpenVINO |
| Storage | 500 MB / 1 GB | 2 GB / 3 GB | 5 GB / 8 GB (with models) |

*Table 1: System Requirements Matrix (V2.0 values / V2.1 values where different)*

---

# 3. Technology Stack Analysis

## 3.1 Programming Language Selection

Python 3.12 selected for TTS ecosystem maturity, HuggingFace integration, and rapid GUI development capabilities. The Python ecosystem offers unparalleled TTS library support with mature packages such as kokoro-onnx, sherpa-onnx, melo-tts, and pydub readily available through pip. The extensive HuggingFace model hub integration is primarily Python-native, providing seamless access to state-of-the-art neural TTS models. Python's dynamic typing and interpreted nature facilitate rapid development iterations, which is particularly valuable for GUI applications requiring frequent UI adjustments based on user feedback.

## 3.2 GUI Framework Selection

Tkinter chosen for native Linux integration, with custom widget extensions (`ThemedFrame`, `GlowButton`, `TabContainer`) providing modern aesthetics while maintaining zero external GUI dependencies. The framework provides all necessary widgets for the application's requirements while maintaining a small memory footprint. Custom widgets extend Tkinter's capabilities to provide modern visual elements while preserving cross-distribution compatibility.

## 3.3 TTS Engine Selection

The application supports multiple TTS engines with a priority-based fallback chain. Based on extensive research and testing, the following engines have been selected for reliability, quality, and performance on Intel-based systems:

| Engine | Type | Quality | GPU Support | Use Case |
|--------|------|---------|-------------|----------|
| Kokoro ONNX | Neural | Excellent | Intel OpenVINO | Primary |
| Sherpa-ONNX | Neural | Very Good | OpenVINO/CUDA | Alternative |
| MeloTTS | Neural | Good | CPU optimized | Fast fallback |
| ElevenLabs API | Cloud | Industry Best | Cloud | Premium |
| espeak-ng | Formant | Basic | N/A | Emergency fallback |

*Table 2: TTS Engine Comparison Matrix*

## 3.4 File Format Support Matrix

| Category | Formats | Libraries |
|----------|---------|-----------|
| Documents | PDF, DOCX, DOC, ODT, RTF, TXT, MD | pdfplumber, pikepdf, python-docx, pypandoc |
| Ebooks | EPUB, MOBI, AZW3, FB2 | ebooklib, mobi, beautifulsoup4 |
| Web | HTML, HTM, XHTML | beautifulsoup4, lxml, html5lib |
| Data | CSV, XLSX, XLS, JSON, XML | pandas, openpyxl, xlrd |
| Archives | ZIP, 7Z, TAR, GZ | py7zr, zipfile, tarfile |
| Audio Import | WAV, MP3, OGG, FLAC, M4A, AAC | pydub, soundfile, librosa |
| Audio Export | WAV, MP3, OGG, FLAC, M4A | pydub, lameenc, ffmpeg |

*Table 3: File Format Support Matrix*

## 3.5 Security Libraries

**PDF Encryption:**
- **pikepdf**: PDF decryption and manipulation (RC4, AES-128/256)
- **pdfminer.six**: Alternative PDF text extraction with encryption support

**Microsoft Office Encryption:**
- **msoffcrypto-tool**: Office document encryption handling (DOCX, XLSX, PPTX, DOC, XLS)
- Supports ECMA-376 Agile encryption, RC4, and legacy XOR encryption

**LibreOffice/OpenDocument Encryption:**
- **odfpy**: OpenDocument format handling with encryption support
- **pyoo**: LibreOffice Python bindings for advanced decryption
- Custom Blowfish/AES decryption modules for ODT/ODS/ODP

**Archive Encryption:**
- **py7zr**: 7-Zip archive encryption support (AES-256)
- **pyzipper**: AES-encrypted ZIP file support
- **rarfile**: RAR archive handling with encryption support

**System Integration:**
- **keyring**: System keychain integration for password storage (SecretService, KWallet, macOS Keychain, Windows Credential)
- **cryptography**: Additional cryptographic utilities (AES, Blowfish, RSA)
- **secretstorage**: Direct SecretService API access for Linux

---

# 4. Architecture Design

## 4.1 High-Level Architecture

The TTS Voices application follows a layered architecture pattern with clear separation between presentation, business logic, and data access layers. The architecture incorporates the Model-View-Controller (MVC) pattern adapted for desktop GUI applications.

**V2.1 Single-Window Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│ Menu Bar (File, Edit, View, Tools, Help)                    │
├─────────────────────────────────────────────────────────────┤
│ Tab Bar: [Main] [Voice Library] [Themes] [Settings] [Logs] │
├─────────────────────────────────────────────────────────────┤
│ Content Area (switches based on active tab)                 │
│  • Main: Text input, controls, progress                     │
│  • Voice Library: Grid of available voices                  │
│  • Themes: Visual customization panel                       │
│  • Settings: Configuration with categories                  │
│  • Logs: Bug tracker with management controls               │
├─────────────────────────────────────────────────────────────┤
│ Status Bar: Playback, GPU/CPU, Theme Indicator              │
└─────────────────────────────────────────────────────────────┘
```

The architecture has been designed with modularity as a core principle, enabling independent development and testing of each component. The GPU/CPU toggle, theme system, and voice controls are implemented as pluggable modules that can be extended without modifying core application logic. This design ensures that future enhancements can be integrated seamlessly while maintaining backward compatibility.

## 4.2 Single-Window Tabbed Interface

**Design Principles:**
- **No Modal Windows**: Settings, voice library, and logs open as tabs, not separate windows
- **State Preservation**: Switching tabs maintains scroll position, input values, and selection states
- **Lazy Loading**: Tab content initializes only on first access to reduce startup time
- **Keyboard Navigation**: Ctrl+1 through Ctrl+5 for instant tab switching
- **Panel System**: Quick slide-out panels from right edge for file browser details, voice preview

**Implementation:**

```python
class TabbedInterface(ttk.Notebook):
    def __init__(self, master):
        self.tabs = {
            'main': MainTab(self),
            'library': VoiceLibraryTab(self),
            'themes': ThemeTab(self),
            'settings': SettingsTab(self),
            'logs': LogTab(self)
        }
        # Prevent window creation, use tab frame instead
```

## 4.3 Module Structure

| Module | Responsibility | Lines (Est.) |
|---------------------|-----------------------------------------------|--------------|
| ttsvoices.py | Main app, tab coordination, window management | ~1,400 |
| tab_main.py | Text input, synthesis controls, progress | ~800 |
| tab_library.py | Voice browser, download manager | ~600 |
| tab_themes.py | Theme selector, custom theme editor | ~500 |
| tab_settings.py | Configuration UI, security settings | ~700 |
| tab_logs.py | Bug tracker UI, log management | ~400 |
| voices.py | Synthesis engines, GPU/CPU toggle | ~600 |
| themed_browser.py | Custom file dialogs, navigation | ~900 |
| file_extractor.py | Multi-format extraction, decryption | ~800 |
| security_manager.py | Password handling, keyring integration | ~400 |
| audio_handler.py | Playback, import/export, conversion | ~600 |
| save_point_manager.py | Playback position bookmarking, reset toggle | ~250 |
| theme_manager.py | 10-preset theme system, custom themes | ~500 |

*Table 4: Module Structure and Responsibilities*

## 4.4 Themed File Browser Architecture

**Problem**: `tkinter.filedialog` uses system native dialogs, breaking theme consistency.

**Solution**: Custom `ThemedFileBrowser` widget embedded in main window or as slide-out panel.

**Features:**
- **Theme Integration**: Uses active theme colors for background, text, selection, accents
- **Breadcrumb Navigation**: Clickable path segments (Home > Documents > Projects)
- **Smooth Scaling**: Animated transitions when entering/exiting folders (150ms ease-in-out)
- **View Modes**: List (detailed), Grid (icons), Tree (hierarchical)
- **Quick Access**: Sidebar with Home, Desktop, Downloads, Recent, Bookmarks
- **Search**: Real-time filtering within current directory
- **Zoom**: Ctrl++ / Ctrl+- for icon/text size consistency
- **Security**: Visual indicator (🔒) for encrypted files, integrated password prompt

## 4.5 Data Flow & Security

**Decryption Flow:**
1. User selects encrypted file in themed browser
2. System detects encryption via magic numbers/headers
3. Password dialog appears (themed, centered modal)
4. Check keyring for stored password
5. Decrypt to temporary memory buffer (never raw disk for security)
6. Pass to extraction pipeline
7. Clear memory buffer after processing

**Password Caching:**
- **Session Memory**: Remember passwords for current session only (volatile)
- **Keyring Storage**: Optional persistent storage via OS keychain
- **Batch Operations**: Apply remembered password to all files in current operation

**Asynchronous Data Flow:**

The application implements an asynchronous, event-driven data flow to ensure UI responsiveness during long-running synthesis operations. Text input flows through a chunking pipeline that breaks large documents into manageable segments, each processed through the synthesis engine independently. Audio chunks are streamed to the playback system through a thread-safe queue, enabling real-time playback while subsequent chunks are still being synthesized.

The data flow supports both GPU-accelerated and CPU-only processing paths. When GPU mode is enabled, the system routes synthesis requests through the OpenVINO backend for Intel GPU acceleration. In CPU mode, the standard ONNX Runtime is used with optimized CPU execution providers. The switching mechanism is transparent to the user and can be changed at runtime without restarting the application.

---

# 5. Module Specifications

## 5.1 Main Application Module (Tabbed)

**Class:** `TTSVoicesApp`

- Manages main window (single instance enforcement)
- Coordinates tab switching and state management
- Handles global keyboard shortcuts (F for fullscreen, Ctrl+1–5 for tabs)
- Manages theme application across all tabs

**Key Methods:**
- `_build_ui()`: Constructs the complete interface including textarea, control panels, voice selector, progress indicators, GPU/CPU toggle, and action buttons
- `_speak_thread()`: Background thread entry point that initiates synthesis and playback sequence with GPU/CPU mode awareness
- `_update_progress()`: Thread-safe method called from synthesis thread to update progress bar, ETA display, and chunk status
- `_on_theme_change()`: Callback for theme switching that updates all UI elements to match the selected theme
- `_toggle_gpu_mode()`: Switches between GPU and CPU processing modes, reloading models as necessary

**Fullscreen Implementation:**

```python
def toggle_fullscreen(self, event=None):
    self.fullscreen = not self.fullscreen
    self.attributes('-fullscreen', self.fullscreen)
    if not self.fullscreen:
        self.geometry(self.previous_geometry)
```

**Custom Widget:** `GlowButton` (Frame-based button with hover effects and theme-aware color schemes)

## 5.2 Voice Synthesis Module

**Class:** `VoiceSynthesizer`

The voices module encapsulates all voice synthesis logic, implementing a priority-based fallback chain with GPU/CPU mode support. The module maintains model caches for both GPU (OpenVINO) and CPU (ONNX Runtime) execution paths, enabling seamless switching between modes without reloading models from disk each time.

**GPU/CPU Toggle Implementation:**

The module implements a hardware abstraction layer that detects available acceleration options and provides a user-facing toggle for switching between modes. On Intel systems with integrated graphics (Iris Plus, UHD, Xe), OpenVINO provides significant performance improvements over CPU-only execution. The toggle supports three modes: Auto (detect best available), GPU (force GPU acceleration), and CPU (force CPU-only processing for power saving or compatibility).

Model caching is implemented separately for each execution path. The `_kokoro_cache` dictionary stores CPU-loaded models, while `_kokoro_gpu_cache` stores OpenVINO-compiled models. This dual-cache approach enables instant switching between modes without model reload delays. Memory usage is monitored to prevent excessive consumption when both caches are populated.

## 5.3 Audio Handler Module

**Enhanced Capabilities:**

- **Import Support**: Load existing audio (WAV/MP3/OGG/FLAC/M4A/AAC) for playback alongside TTS
- **Export Formats**: WAV (PCM), MP3 (CBR/VBR), OGG (Vorbis), FLAC (Lossless), M4A (AAC)
- **Quality Presets:**
  - Low: MP3 128kbps, OGG 96kbps
  - Medium: MP3 192kbps, OGG 128kbps
  - High: MP3 320kbps, FLAC level 5
  - Audiophile: WAV 32-bit float, FLAC level 8

**Audio Playback Fallback Chain:**

| Priority | Method | Library | Advantages |
|----------|--------|---------|------------|
| 1 | pygame.mixer | pygame | Python-native, streaming, volume control |
| 2 | aplay | subprocess | ALSA direct, minimal dependencies |
| 3 | paplay | subprocess | PulseAudio/PipeWire integration |
| 4 | ffplay | subprocess | FFmpeg backend, format flexibility |

*Table 5: Audio Playback Fallback Chain*

### Save Point System

The application includes a Save Point system that allows users to bookmark their position in audio playback and resume from that point later, with the option to reset and start from the beginning.

**UI Components:**
- **Save Point Button**: Located in the playback controls area, allows setting a bookmark at the current playback position
- **Reset Save Point Toggle**: A toggle button next to the clear button that, when enabled, causes playback to start from the beginning instead of the saved position
- **Position Indicator**: Visual display showing the current playback position and any saved point timestamp

**Functionality:**

| Action | Description |
|--------|-------------|
| Set Save Point | Bookmark current playback position for later resumption |
| Go to Save Point | Jump to the previously saved playback position |
| Reset Save Point Toggle | When ON, playback starts from beginning; when OFF, resumes from saved point |
| Clear Save Point | Remove the saved position bookmark |

**Implementation Details:**

```python
class SavePointManager:
    def __init__(self):
        self.saved_position = None  # Timestamp in seconds
        self.reset_toggle = False   # Reset toggle state
        
    def set_save_point(self, current_position):
        """Save current playback position"""
        self.saved_position = current_position
        
    def get_start_position(self):
        """Get starting position based on reset toggle"""
        if self.reset_toggle or self.saved_position is None:
            return 0.0  # Start from beginning
        return self.saved_position
        
    def toggle_reset(self):
        """Toggle reset save point mode"""
        self.reset_toggle = not self.reset_toggle
        return self.reset_toggle
        
    def clear_save_point(self):
        """Clear the saved position"""
        self.saved_position = None
        self.reset_toggle = False
```

**Use Cases:**
- **Audiobook Listening**: Save position when taking a break, resume later
- **Long Document Review**: Bookmark section for repeated listening
- **Language Learning**: Save position to practice specific sections
- **Meeting Notes**: Mark important sections for later review

**Persistence:**
- Save points are stored in the session state
- Optional: Persist to `~/.ttsvoices/savepoints/` for cross-session retention
- Automatic save point on application close (configurable)
- Clear all save points option in Settings

**Visual Feedback:**
- Save point indicator on progress bar
- Current position vs saved position display
- Reset toggle status indicator (highlighted when active)
- Confirmation toast when save point is set/cleared

## 5.4 File Extractor & Decryption Module

**Class:** `SecureFileExtractor`

**Methods:**
- `extract_pdf(path, password=None)`: Handles encrypted PDFs via `pikepdf`
- `extract_docx(path, password=None)`: Handles encrypted Microsoft Office docs (DOCX, XLSX, PPTX) via `msoffcrypto`
- `extract_libreoffice(path, password=None)`: Handles encrypted LibreOffice/OpenDocument files (ODT, ODS, ODP) via `odfpy` and custom decryption
- `extract_office_legacy(path, password=None)`: Handles older Office formats (DOC, XLS) with legacy encryption
- `extract_archive(path, password=None)`: Handles encrypted archives (ZIP, 7Z, RAR) with passwords
- `detect_encryption(path)`: Returns encryption type or None (supports PDF, MS Office, LibreOffice, OpenDocument, archives)
- `batch_extract(paths, passwords_dict)`: Process multiple files with credential reuse

**Supported Encrypted Formats:**

| Format | Extension | Encryption Type | Library |
|--------|-----------|-----------------|----------|
| PDF | .pdf | RC4, AES-128/256 | pikepdf |
| Microsoft Word | .docx | ECMA-376 Agile | msoffcrypto-tool |
| Microsoft Excel | .xlsx | ECMA-376 Agile | msoffcrypto-tool |
| Microsoft PowerPoint | .pptx | ECMA-376 Agile | msoffcrypto-tool |
| Legacy Word | .doc | RC4, XOR | msoffcrypto-tool |
| Legacy Excel | .xls | RC4, XOR | msoffcrypto-tool |
| LibreOffice Writer | .odt | Blowfish, AES | odfpy + custom |
| LibreOffice Calc | .ods | Blowfish, AES | odfpy + custom |
| LibreOffice Impress | .odp | Blowfish, AES | odfpy + custom |
| OpenDocument Text | .odt | Blowfish, AES | odfpy + custom |
| OpenDocument Spreadsheet | .ods | Blowfish, AES | odfpy + custom |
| OpenDocument Presentation | .odp | Blowfish, AES | odfpy + custom |
| ZIP Archive | .zip | AES-256, ZipCrypto | zipfile, pyzipper |
| 7-Zip Archive | .7z | AES-256 | py7zr |
| RAR Archive | .rar | AES-256 | rarfile, unrar |

*Table: Supported Encrypted File Formats*

**Security Features:**
- Zero-disk decryption (memory buffers only)
- Automatic temp file cleanup
- Password strength validation for new exports
- Secure password caching with configurable TTL
- Automatic format detection via magic numbers/file signatures
- Multi-attempt password retry with visual feedback

## 5.5 Themed File Browser Module

**Class:** `ThemedFileBrowser(Frame)`

**UI Components:**
- **Toolbar**: Back/Forward/Up/Refresh, View toggle (List/Grid/Tree), Search box
- **Breadcrumb**: Horizontal path with clickable segments
- **Sidebar**: 200px collapsible panel (40px icons-only mode)
- **File Area**: Scrollable canvas with themed icons
- **Preview Pane**: Right panel showing text preview or metadata
- **Status Bar**: File count, selection size, encryption indicator

**Theming:**
- Background: `theme.bg_primary`
- Text: `theme.text_primary`
- Selection: `theme.accent_primary` with 20% opacity
- Hover: `theme.accent_secondary` glow effect
- Borders: `theme.border_color`

**Animations:**
- Folder enter: Fade-in content (150ms)
- Icon scaling: Smooth resize when zooming (Ctrl+scroll)
- Selection: Color transition (100ms)

## 5.6 Theme Manager (Extended)

**Class:** `ThemeManager`

**Preset Themes (10):**

| Theme | Background | Accent | Use Case |
|-------|------------|--------|----------|
| Midnight Dark | `#0F0F1E` | `#7B68EE` (Purple) | Default, low-light |
| Arctic Light | `#F0F4F8` | `#2196F3` (Blue) | Daylight, contrast |
| Ruby Red | `#1A0A0A` | `#FF2E63` (Hot pink/red) | Gaming, energy |
| Ocean Blue | `#0A1628` | `#00D4FF` (Cyan) | Professional |
| Forest Teal | `#0A1F1C` | `#00FFC2` (Mint) | Nature, focus |
| Sunset Orange | `#1A0F05` | `#FF6B35` (Orange) | Creative |
| Royal Purple | `#130A1E` | `#9D4EDD` (Violet) | Luxury |
| Sakura Pink | `#1E1218` | `#FF85A1` (Pastel pink) | Soft aesthetic |
| Golden Amber | `#1A150A` | `#FFD700` (Gold) | Warm, vintage |
| Emerald Green | `#0A1E12` | `#00FF88` (Neon green) | Matrix, coding |

*Table 6: Built-in Theme Color Schemes*

**Dynamic Switching:**
- Hot-swap without restart
- 200ms color transition via `after()` scheduling
- File browser refreshes immediately
- Export dialogs adopt new theme instantly

## 5.7 Security Manager

**Class:** `SecurityManager`

**Keyring Integration:**
- Store/retrieve master password
- Per-file password caching with TTL (5 minutes)
- OS-specific backends (SecretService, KWallet, macOS Keychain, Windows Credential)

**Password Dialog:**
- Themed modal (not system dialog)
- Show/Hide toggle
- "Remember this session" checkbox
- "Save to keyring" checkbox (requires master password)
- Strength indicator for new passwords

## 5.8 Bug Tracker (Enhanced)

**Class:** `BugTracker`

**Log Management:**
- Max Lines: Configurable (default 1000, max 10000)
- Auto-clear: When exceeding threshold (default 5000 lines)
- Manual Clear: Button in Log tab with confirmation
- Archive: Export current log before clearing (timestamped filename)
- Search: Filter by level (ERROR/WARNING/INFO) or text content
- Export: Save logs to file without clearing

**UI Controls in Log Tab:**
- Clear button (🗑️) with "Export first?" confirmation
- Auto-clear toggle checkbox
- Level filter dropdown
- Search entry box
- Archive history dropdown (previous log files)

---

# 6. Voice Synthesis System

## 6.1 Recommended TTS Models

Based on extensive research and testing, the following TTS models are recommended for reliable performance on the target system (Linux Mint 22.3, Intel Core i7-1065G7 with Iris Plus Graphics G7, 8GB RAM). These models have been selected for their quality, resource efficiency, and compatibility with Intel hardware acceleration:

- **Kokoro TTS (82M Parameters)**: Primary recommendation for high-quality offline synthesis. With only 82 million parameters, it delivers excellent quality comparable to much larger models. Supports ONNX format for CPU execution and OpenVINO for Intel GPU acceleration. Model size ranges from 82MB (quantized) to 326MB (full precision).

- **Sherpa-ONNX VITS Models**: Excellent alternative with broad model support. Includes pre-trained voices for multiple languages. Lightweight ONNX deployment with optional CUDA/OpenVINO acceleration. Model sizes typically 50-200MB.

- **MeloTTS**: Fast, lightweight TTS optimized for CPU execution. Provides good quality synthesis with minimal resource requirements. Ideal for systems where GPU acceleration is unavailable. Model sizes around 30-80MB.

- **Intel-Optimized Kokoro (magicunicorn/kokoro-tts-intel)**: Specialized version optimized for Intel integrated GPU acceleration via OpenVINO. Provides 2-4x speedup over CPU-only execution on compatible hardware.

## 6.2 Model Performance Comparison

| Model | CPU RTF | GPU RTF | Quality Score | Memory |
|-------|---------|---------|---------------|--------|
| Kokoro ONNX | 0.8-1.2 | 0.3-0.5 | 4.5/5 | 200-400 MB |
| Kokoro Intel-Opt | 0.7-1.0 | 0.2-0.4 | 4.5/5 | 200-400 MB |
| Sherpa-ONNX VITS | 0.6-1.0 | 0.3-0.5 | 4.2/5 | 100-300 MB |
| MeloTTS | 0.5-0.8 | N/A | 3.8/5 | 50-100 MB |
| ElevenLabs API | N/A | N/A | 4.9/5 | Cloud |

*Table 7: TTS Model Performance Comparison (RTF = Real-Time Factor, lower is faster)*

## 6.3 Text Highlighting During Playback

The application features a comprehensive text highlighting system that provides visual feedback during speech synthesis, helping users follow along with the spoken content in real-time.

### Highlighting Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| Word Highlight | Highlights individual words as they are spoken | Fine-grained following, karaoke-style |
| Sentence Highlight | Highlights entire sentences during playback | Natural reading flow, less visual distraction |
| Paragraph Highlight | Highlights current paragraph | Long-form content, document review |
| Chunk Highlight | Highlights current text chunk being processed | Large document processing |

### Visual Styling Options

**Highlight Appearance Settings:**
- **Highlight Color**: Customizable highlight background (defaults to theme accent color)
- **Text Color**: Customizable highlighted text color (defaults to theme text color)
- **Highlight Style**: Solid, gradient, underline, or box outline
- **Animation Speed**: Configurable transition duration (50-500ms)
- **Opacity**: Adjustable highlight opacity (50-100%)

### Implementation Details

**Thread-Safe Highlighting:**
```python
class TextHighlighter:
    def __init__(self, text_widget, theme_manager):
        self.text_widget = text_widget
        self.theme = theme_manager
        self.highlight_tag = "speech_highlight"
        self.current_position = 0
        
    def highlight_word(self, start_idx, end_idx):
        """Thread-safe word highlighting with smooth transitions"""
        self.text_widget.tag_remove(self.highlight_tag, "1.0", "end")
        self.text_widget.tag_add(self.highlight_tag, start_idx, end_idx)
        self._scroll_to_visible(start_idx)
        
    def _scroll_to_visible(self, index):
        """Auto-scroll to keep highlighted text visible"""
        self.text_widget.see(index)
        self.text_widget.update_idletasks()
```

**Auto-Scroll Behavior:**
- Smooth scrolling to keep highlighted text in view
- Configurable scroll margin (lines above/below)
- Optional center-on-highlight mode
- Pause scroll during manual text interaction

### Accessibility Features

- **High Contrast Mode**: Enhanced highlight visibility for visually impaired users
- **Focus Indicators**: Additional visual cues beyond color highlighting
- **Screen Reader Compatibility**: Highlighting state exposed via accessibility APIs
- **Reduced Motion Option**: Instant highlighting without animations for motion-sensitive users

### Settings Integration

Text highlighting preferences are integrated into the Settings panel:
- Enable/disable highlighting globally
- Select highlighting mode (word/sentence/paragraph)
- Customize highlight appearance
- Configure auto-scroll behavior
- Per-theme highlight color profiles

---

# 7. GPU/CPU Toggle Support

## 7.1 Hardware Detection

The application implements automatic hardware detection to identify available acceleration options on the user's system. The detection module checks for Intel integrated graphics (Iris Plus, UHD, Xe), NVIDIA GPUs (via CUDA), and Apple Silicon (via CoreML). On the target system (Intel Iris Plus Graphics G7), OpenVINO is the recommended acceleration backend.

The hardware detection process runs at application startup and caches results for the session. Detection includes checking for OpenVINO installation, Intel GPU driver availability, and benchmarking basic inference to verify acceleration is functional. Users can force redetection through the Settings panel.

## 7.2 Toggle Implementation

The GPU/CPU toggle is implemented as a three-way selector in the main interface, accessible via a dropdown menu or radio buttons. The three modes provide flexibility for different usage scenarios:

- **Auto Mode**: Automatically selects the best available acceleration method. Prioritizes GPU acceleration when available and functional, falls back to CPU when GPU is unavailable or encounters errors. This is the default mode for new installations.

- **GPU Mode**: Forces GPU acceleration via OpenVINO. Displays an error message if GPU acceleration is unavailable. Provides maximum performance for batch processing and real-time synthesis on compatible hardware.

- **CPU Mode**: Forces CPU-only processing. Useful for power conservation on laptops, troubleshooting GPU issues, or systems with limited GPU memory. Uses optimized ONNX Runtime CPU execution providers.

## 7.3 Performance Impact

On the target Intel Core i7-1065G7 with Iris Plus Graphics G7, GPU acceleration via OpenVINO provides significant performance improvements over CPU-only execution. Benchmark results show approximately 2-4x speedup for neural TTS synthesis, with the greatest gains observed for longer text segments. The GPU mode reduces average synthesis time from approximately 1.0 seconds per 100 characters (CPU) to approximately 0.3-0.4 seconds (GPU OpenVINO).

Power consumption is also a consideration. GPU mode typically consumes more power during synthesis, which may impact battery life on laptops. The CPU mode is recommended for battery-powered operation when performance is less critical. The application displays estimated power impact in the settings panel.

---

# 8. Theme System (Extended Palette)

## 8.1 Built-in Themes (10 Presets)

| Theme | Background | Primary Accent | Secondary | Use Case |
|-------|------------|----------------|-----------|----------|
| Midnight Dark | `#0F0F1E` | `#7B68EE` | `#4DB6AC` | Default, low-light |
| Arctic Light | `#F0F4F8` | `#2196F3` | `#FF5722` | Daylight, contrast |
| Ruby Red | `#1A0A0A` | `#FF2E63` | `#FFB300` | Gaming, energy |
| Ocean Blue | `#0A1628` | `#00D4FF` | `#7B68EE` | Professional |
| Forest Teal | `#0A1F1C` | `#00FFC2` | `#FF6B6B` | Nature, focus |
| Sunset Orange | `#1A0F05` | `#FF6B35` | `#F7931E` | Creative |
| Royal Purple | `#130A1E` | `#9D4EDD` | `#C77DFF` | Luxury |
| Sakura Pink | `#1E1218` | `#FF85A1` | `#FFACC5` | Soft aesthetic |
| Golden Amber | `#1A150A` | `#FFD700` | `#FF8C00` | Warm, vintage |
| Emerald Green | `#0A1E12` | `#00FF88` | `#ADFF2F` | Matrix, coding |

*Table 8: Complete Theme Palette with Secondary Colors*

## 8.2 Custom Theme Support

Theme Editor features:
- Color pickers for all 12 color roles (`bg_primary`, `bg_secondary`, `text_primary`, `accent_primary`, etc.)
- Live preview panel
- Import/export JSON
- Share themes via file exchange

Users can create custom themes through the Settings panel. The theme editor provides color pickers for all UI elements, with a live preview showing how the theme will appear. Custom themes are saved as JSON files in the `~/.ttsvoices/themes/` directory and can be shared between users.

The theme manager module (theme_manager.py) handles theme loading, validation, and application. Themes are validated for required color definitions and color format compliance before being applied. Invalid theme files generate user-friendly error messages without crashing the application.

## 8.3 Theme Architecture

The theme system is implemented as a separate module to ensure clean separation of concerns. The ThemeManager class maintains the current theme state and provides methods for theme switching. All UI widgets register with the ThemeManager on creation and receive callbacks when the theme changes. This observer pattern ensures consistent theme application across all interface elements.

**Observer pattern implementation:**
- All widgets register with `ThemeManager`
- On theme change, callback updates widget colors
- File browser receives immediate refresh signal
- 200ms CSS-like transition for smooth color morphing

## 8.4 Universal Theme Application

The application implements comprehensive theme support ensuring that **every single UI element** follows the selected theme, with no exceptions. This creates a consistent, professional appearance throughout the entire user experience.

### Complete Theme Coverage

| UI Element Type | Examples | Theme Properties Applied |
|-----------------|----------|------------------------|
| Main Window | Application frame, menu bar, tab bar | bg_primary, text_primary, border_color |
| Dialogs | All modal and non-modal dialogs | All theme colors |
| Popups | Context menus, dropdown menus | bg_secondary, text_primary, accent_primary |
| Alerts | Error dialogs, warnings, confirmations | bg_primary, accent_primary (for emphasis) |
| Progress Dialogs | File operations, export progress | bg_primary, accent_primary |
| File Browser | Custom file dialogs, navigation | Full theme palette |
| Tooltips | Hover tooltips, help text | bg_secondary, text_secondary |
| Scrollbars | All scrollable areas | bg_secondary, accent_primary |
| Buttons | All buttons, toggle buttons | accent_primary, hover states |
| Input Fields | Text areas, entry boxes, combo boxes | bg_secondary, text_primary, border_color |
| Sliders | Volume, speed, pitch controls | accent_primary, bg_secondary |
| Progress Bars | Synthesis progress, chunk status | accent_primary, bg_secondary |
| Status Bar | Bottom status indicators | bg_secondary, text_secondary |
| Log Viewer | Bug tracker, log display | bg_primary, text_primary |
| Settings Panels | All settings tabs | bg_primary, bg_secondary |
| Password Prompts | Encryption password dialogs | bg_primary, accent_primary |
| Error Messages | Exception dialogs, crash reports | bg_primary, error colors |
| Notification Toasts | Success/error notifications | bg_secondary, accent_primary |

### Themed Dialog Implementation

**Base Themed Dialog Class:**
```python
class ThemedDialog(tk.Toplevel):
    """Base class for all themed dialogs in the application"""
    
    def __init__(self, parent, title, theme_manager):
        super().__init__(parent)
        self.theme = theme_manager
        self.title(title)
        
        # Apply theme immediately
        self.apply_theme()
        
        # Register for theme changes
        self.theme.register_callback(self.apply_theme)
        
    def apply_theme(self):
        """Apply current theme colors to dialog"""
        self.configure(bg=self.theme.bg_primary)
        for widget in self.winfo_children():
            self._apply_theme_to_widget(widget)
            
    def _apply_theme_to_widget(self, widget):
        """Recursively apply theme to widget and children"""
        widget_type = widget.winfo_class()
        if widget_type in ('Frame', 'Labelframe'):
            widget.configure(bg=self.theme.bg_primary)
        elif widget_type == 'Label':
            widget.configure(bg=self.theme.bg_primary, fg=self.theme.text_primary)
        elif widget_type == 'Button':
            widget.configure(bg=self.theme.accent_primary, fg=self.theme.text_on_accent)
        # ... handle all widget types
        
        for child in widget.winfo_children():
            self._apply_theme_to_widget(child)
```

### Dialog Types with Full Theme Support

**1. Message Dialogs (ThemedMessageDialog)**
- Info, Warning, Error, Question types
- Custom icons matching theme colors
- Themed buttons with hover effects
- Auto-sizing based on content

**2. Confirmation Dialogs (ThemedConfirmDialog)**
- Yes/No/Cancel options
- Customizable button labels
- Keyboard shortcuts (Y/N/Esc)
- Themed focus indicators

**3. Input Dialogs (ThemedInputDialog)**
- Single-line text input
- Multi-line text input
- Masked input (for passwords)
- Validation with themed error states

**4. File Dialogs (ThemedFileDialog)**
- Open/Save dialogs
- Multi-file selection
- Directory selection
- Encryption indicator icons

**5. Progress Dialogs (ThemedProgressDialog)**
- Determinate progress (0-100%)
- Indeterminate progress (spinner)
- Cancellable operations
- Themed cancel button

**6. Color Picker (ThemedColorPicker)**
- Theme-aware color selection
- Recent colors palette
- Custom color input (hex/RGB)
- Preview area

**7. Font Picker (ThemedFontPicker)**
- System font listing
- Size selection
- Style preview
- Themed preview text

### Context Menu Theming

All right-click context menus follow the active theme:
- Background: `bg_secondary`
- Text: `text_primary`
- Highlighted item: `accent_primary`
- Borders: `border_color`
- Icons: Theme-appropriate colors

### Notification System

**Toast Notifications (ThemedToast)**
- Non-modal, auto-dismissing notifications
- Positioned in corner of main window
- Success (green accent), Info (blue accent), Warning (yellow accent), Error (red accent)
- Smooth fade-in/fade-out animations
- Clickable for more details

### Theme Transition Animation

When theme changes, all UI elements transition smoothly:
- **Duration**: 200ms
- **Easing**: Ease-in-out
- **Scope**: All visible windows, dialogs, and widgets
- **Performance**: Optimized to prevent lag during transition

### Implementation Checklist

Every new UI component must:
- [ ] Extend `ThemedWidget` base class or use `ThemeManager` callbacks
- [ ] Call `apply_theme()` during initialization
- [ ] Register with `ThemeManager` for live theme updates
- [ ] Support all 10 built-in themes
- [ ] Handle theme change during display (for dialogs)
- [ ] Test with high-contrast accessibility themes

---

# 9. Voice Controls (Speed, Pitch, Volume)

## 9.1 Speed Control

The speed control allows users to adjust speech rate from 0.5x (half speed) to 2.0x (double speed). The control is implemented as a slider with real-time preview capability. Speed adjustment is achieved through audio time-stretching for engines that do not support native speed control, ensuring consistent behavior across all TTS backends.

For Kokoro ONNX and other neural engines, speed adjustment is applied during synthesis when supported by the engine's parameters. For engines without native speed control, the application uses the rubberband library for high-quality time stretching that preserves pitch while adjusting tempo. The UI displays the current speed setting as both a multiplier (1.0x) and words-per-minute estimate.

## 9.2 Pitch Control

Pitch adjustment ranges from -50% (lower pitch) to +50% (higher pitch) relative to the base voice. The control modifies the fundamental frequency of the generated speech while preserving natural intonation patterns. As with speed control, pitch adjustment uses audio post-processing for engines without native support.

The pitch shifting algorithm uses phase vocoder techniques to maintain audio quality across the adjustment range. Extreme pitch values (>30%) may introduce artifacts, so the UI includes visual indicators for the recommended adjustment range. Users can preview pitch changes in real-time before applying them to full text synthesis.

## 9.3 Volume Control

Volume control ranges from 0% (mute) to 100% (maximum), with additional gain boost available up to 150% for quiet voices or noisy environments. The volume slider updates audio output in real-time during playback, with changes applying immediately without requiring synthesis restart.

Volume normalization is applied automatically to ensure consistent loudness across different voices. The application measures peak amplitude of generated audio and applies dynamic range compression to prevent clipping while maintaining perceived loudness. Users can disable normalization in settings if they prefer original voice characteristics.

## 9.4 Control Panel Layout

Voice controls are grouped in a collapsible panel below the text area. Each control includes a slider, value display, and reset button. A preview button allows users to hear the current settings applied to a sample phrase. Control settings are saved per-session and can be saved as presets for reuse.

---

# 10. Processing Time and ETA Display

## 10.1 Progress Bar Implementation

The application features a comprehensive progress display that shows real-time synthesis status. The progress bar indicates overall completion percentage, with separate indicators for each processing stage: text chunking, synthesis, audio buffering, and playback. The progress bar updates smoothly during synthesis to provide visual feedback without UI flicker.

Progress tracking is implemented through a thread-safe callback mechanism. The synthesis thread reports progress updates to the main thread via a queue, which processes updates using tkinter's `after()` method. This approach ensures smooth UI updates without blocking the synthesis thread or causing race conditions.

## 10.2 ETA Calculation

The ETA (Estimated Time of Arrival) display shows the predicted time remaining for the current synthesis operation. The calculation uses a rolling average of chunk processing times to estimate remaining time, with adjustments for varying chunk sizes and system load. The ETA updates every second during synthesis to provide accurate predictions.

The ETA algorithm accounts for model loading time (first chunk), varying text complexity, and GPU/CPU mode differences. After processing the first few chunks, the algorithm calibrates to the current system's performance characteristics. The display shows both absolute time remaining (e.g., '2:34 remaining') and processing rate (e.g., '150 words/second').

## 10.3 Chunk Status Display

The chunk status panel shows detailed information about the current and upcoming text chunks. For each chunk, the display includes chunk number, size (in characters or words), processing status (pending, processing, complete), and processing time. This granular visibility helps users understand synthesis progress for long documents.

The chunk display also indicates which TTS engine and voice model are being used for each chunk, helpful when using automatic engine selection that may switch between engines based on content type or performance requirements. Failed chunks are highlighted with error indicators and retry options.

---

# 11. Settings, Security & ElevenLabs Integration

## 11.1 Settings Panel Overview

The Settings panel provides comprehensive configuration options organized into categorized tabs. The panel is accessible from the main menu or via keyboard shortcut (Ctrl+,). Settings are persisted to `~/.ttsvoices/config.json` and loaded automatically on application startup.

**Settings Categories:**
- **Voice Settings**: Default voice selection, speed/pitch/volume defaults, engine preferences
- **Audio Settings**: Output device selection, sample rate, buffer size, normalization options
- **Processing Settings**: GPU/CPU mode, chunk size, threading options, memory limits
- **API Settings**: ElevenLabs API key, API endpoint, default voice, usage tracking
- **Appearance Settings**: Theme selection, custom theme editor, font preferences
- **Storage Settings**: Model download location, cache management, export defaults

## 11.2 ElevenLabs API Integration

The application integrates with ElevenLabs API for premium cloud-based voice synthesis. The API key is stored securely in the settings file with optional encryption. Users can input their API key through a dedicated input field in the API Settings tab, with options to show/hide the key for security.

The ElevenLabs integration includes usage tracking to help users monitor their API quota. The settings panel displays current month's character usage, remaining quota, and estimated usage for pending synthesis tasks. Voice selection for ElevenLabs shows available voices from the user's account, with preview capability before selection.

## 11.3 File Security & Decryption

The V2.1 security system provides comprehensive protection for encrypted files:

- **PDF Decryption**: Automatic detection and handling via `pikepdf`
- **Office Document Decryption**: Support for encrypted DOCX, XLSX via `msoffcrypto`
- **Archive Decryption**: Password-protected ZIP, 7Z support
- **Memory-Only Processing**: Decrypted content never written to disk
- **Automatic Cleanup**: Secure clearing of decryption buffers

## 11.4 Master Password & Keyring

**Keyring Integration:**
- Store/retrieve master password
- Per-file password caching with TTL (5 minutes)
- OS-specific backends (SecretService, KWallet, macOS Keychain, Windows Credential)

**Password Dialog Features:**
- Themed modal (not system dialog)
- Show/Hide toggle
- "Remember this session" checkbox
- "Save to keyring" checkbox (requires master password)
- Strength indicator for new passwords

## 11.5 API Key Input Field

The API key input field in the Settings panel provides a secure and user-friendly interface for entering ElevenLabs credentials:

- **Masked Input**: API key is displayed as masked characters by default for security
- **Show/Hide Toggle**: Button to reveal the key for verification
- **Validation**: Real-time validation of key format with error indicators
- **Test Connection**: Button to verify API connectivity before saving
- **Usage Display**: Shows current subscription tier and character usage
- **Voice Browser**: Lists available ElevenLabs voices with preview capability

## 11.6 Configuration File Structure

Settings are stored in JSON format for human readability and easy backup. The configuration file includes version information for future migration support. Sensitive values (API keys) are optionally encrypted using system-provided key storage where available.

---

# 12. File Browser & Export System

## 12.1 Themed File Browser Design

The custom `ThemedFileBrowser` widget replaces system native file dialogs to maintain theme consistency:

**UI Components:**
- **Toolbar**: Back/Forward/Up/Refresh, View toggle (List/Grid/Tree), Search box
- **Breadcrumb**: Horizontal path with clickable segments
- **Sidebar**: 200px collapsible panel (40px icons-only mode)
- **File Area**: Scrollable canvas with themed icons
- **Preview Pane**: Right panel showing text preview or metadata
- **Status Bar**: File count, selection size, encryption indicator

**Theming Colors:**
- Background: `theme.bg_primary`
- Text: `theme.text_primary`
- Selection: `theme.accent_primary` with 20% opacity
- Hover: `theme.accent_secondary` glow effect
- Borders: `theme.border_color`

**Animations:**
- Folder enter: Fade-in content (150ms)
- Icon scaling: Smooth resize when zooming (Ctrl+scroll)
- Selection: Color transition (100ms)

## 12.2 Audio Export (WAV/MP3/OGG/FLAC/M4A)

**Export Formats:**
- **WAV**: PCM, 16/24/32-bit
- **MP3**: CBR/VBR, 64-320kbps
- **OGG**: Vorbis, quality 0-10
- **FLAC**: Lossless, compression levels 0-8
- **M4A**: AAC, various bitrates

**Quality Presets:**
| Preset | MP3 | OGG | FLAC | WAV |
|--------|-----|-----|------|-----|
| Low | 128kbps | 96kbps | - | 16-bit |
| Medium | 192kbps | 128kbps | Level 5 | 16-bit |
| High | 320kbps | 192kbps | Level 5 | 24-bit |
| Audiophile | - | - | Level 8 | 32-bit float |

## 12.3 Audio Import Support

**Supported Import Formats:**
- WAV (PCM, various bit depths)
- MP3 (CBR/VBR)
- OGG (Vorbis)
- FLAC (Lossless)
- M4A (AAC)
- AAC

**Import Capabilities:**
- Playback alongside TTS output
- Format conversion
- Audio analysis
- Metadata extraction

---

# 13. Best Practices Implementation

## 13.1 Error Handling Strategy

The application implements comprehensive error handling with graceful degradation. Every critical operation is wrapped in try/except blocks with specific exception handling. User-facing errors display clear, actionable messages, while technical details are logged for debugging. The fallback chain pattern ensures functionality even when individual components fail.

## 13.2 Memory Management

For unlimited text handling, the application employs generator-based text processing that yields chunks one at a time rather than loading entire documents into memory. Model caching balances performance against memory usage, with automatic cache clearing when switching between engines or when memory pressure is detected. The application monitors available system memory and adjusts behavior accordingly.

## 13.3 Thread Safety

All shared state between threads is protected by synchronization primitives. The synthesis queue uses `queue.Queue` for built-in thread safety. GUI updates from background threads use tkinter's `after()` method to schedule callbacks on the main thread. Model caches use `threading.Lock` for read/write synchronization to prevent race conditions.

---

# 14. Testing Strategy

## 14.1 Unit Testing

Unit tests cover each module in isolation with mocked dependencies. Test categories include chunking algorithms, model caching behavior, file extraction logic, audio playback fallback chains, theme switching, and GPU/CPU toggle functionality. Tests use pytest with fixtures and parametrized test cases for edge conditions.

## 14.2 Integration Testing

Integration tests verify the complete synthesis pipeline from text input to audio output on actual hardware. Tests include memory leak detection, performance regression testing, and verification of GPU/CPU mode switching. Voice model download and installation are tested against HuggingFace endpoints with network mocking for reliability.

---

# 15. Deployment Plan

## 15.1 Installation Script

The `install.sh` script handles system-level setup including dependency installation, virtual environment creation, and desktop integration. The script is idempotent and includes extensive error checking with informative progress messages.

**Installation Steps:**
1. Install system dependencies: python3-venv, python3-tk, espeak-ng, ffmpeg, alsa-utils
2. Create Python virtual environment in project directory
3. Install Python dependencies: kokoro-onnx, openvino, pydub, pygame, requests, soundfile
4. Install file extraction dependencies: pdfplumber, python-docx, ebooklib, beautifulsoup4
5. Install security dependencies: pikepdf, msoffcrypto-tool, py7zr, keyring
6. Create `~/.ttsvoices/` directory structure for models, themes, and configuration
7. Download default Kokoro voice models from HuggingFace
8. Create launcher script and desktop menu integration
9. Run hardware detection and benchmark for optimal configuration

---

# 16. Risk Assessment & Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| GPU driver issues | Medium | Medium | CPU fallback, driver detection, user documentation |
| Model download failures | High | Medium | Retry logic, local mirrors, offline mode support |
| Audio device incompatibility | High | Low | 4-tier fallback chain, device selection UI |
| Memory exhaustion | High | Medium | Generator-based processing, chunk limits, monitoring |
| API quota exceeded | Low | Medium | Usage tracking, warnings, offline fallback |
| Encrypted file handling errors | Medium | Low | Password caching, format validation, user feedback |
| Theme rendering issues | Low | Low | Fallback to default theme, validation before apply |

*Table 9: Risk Assessment Matrix*

---

# 17. Development Roadmap

## Phase 1: Core Foundation (Week 1-2)
- Set up project structure and virtual environment handling
- Implement basic Tkinter UI with text area and control buttons
- Create voice synthesis module with Kokoro ONNX support
- Implement audio playback with pygame fallback chain
- Add basic configuration file handling

## Phase 2: Enhanced Features (Week 3-4)
- Implement GPU/CPU toggle with OpenVINO integration
- Add theme system with Dark, Light, and extended themes
- Implement voice controls (speed, pitch, volume)
- Add processing time display and ETA calculation
- Create Voice Library UI for model management

## Phase 3: Integration (Week 5-6)
- Implement Settings panel with ElevenLabs API integration
- Add multi-format file extraction (PDF, DOCX, EPUB)
- Implement model caching for GPU and CPU modes
- Add custom theme support and theme editor
- Create comprehensive error handling and logging
- Implement single-window tabbed architecture
- Build themed file browser module
- Add security manager with keyring support

## Phase 4: Polish and Release (Week 7-8)
- Conduct user acceptance testing on target hardware
- Optimize performance for Intel integrated graphics
- Create README and user documentation
- Package distribution with installer
- Release version 2.1 with all features

---

# 18. Conclusion

The TTS Voices application Version 2.1 represents a comprehensive solution for Linux desktop text-to-speech needs, addressing the gap between basic system TTS and expensive cloud-based services. Building upon the solid foundation of Version 2.0's GPU/CPU toggle, theme customization, voice controls, and real-time ETA display, Version 2.1 introduces critical architectural improvements including a unified single-window interface, extensive file format support with decryption capabilities, and a fully themed file browser.

Key enhancements in this version include **Text Highlighting During Playback**, which provides real-time visual feedback as text is being spoken, supporting multiple highlighting modes (word, sentence, paragraph, chunk) with customizable appearance and accessibility features. The **Universal Theme Application** ensures that every single UI element—from main windows to popups, dialogs, alerts, tooltips, and context menus—follows the selected theme, creating a consistent and professional user experience throughout.

The **Save Point System** provides convenient playback position management, allowing users to bookmark their position in long audio content and resume later, with a reset toggle option to start playback from the beginning when needed. This is particularly useful for audiobook listening, long document review, language learning, and any scenario requiring position persistence across sessions.

The expanded **Encryption Support** now covers a wide range of formats including LibreOffice/OpenDocument files (ODT, ODS, ODP), Microsoft Office documents (DOCX, XLSX, PPTX), legacy Office formats (DOC, XLS), and encrypted archives (ZIP, 7Z, RAR), with secure memory-only decryption and password caching for efficient batch operations.

The modular architecture enables future enhancements while the comprehensive settings panel with ElevenLabs integration ensures flexibility for both offline and cloud-based workflows. Performance optimizations for Intel integrated graphics ensure optimal performance on the target platform, with seamless fallback to CPU processing when GPU acceleration is unavailable.

Key technical decisions—Python with Tkinter, Kokoro ONNX with OpenVINO acceleration, multi-tier fallback chains, comprehensive UI controls, text highlighting with accessibility support, save point playback management, universal theme theming, and the new single-window tabbed architecture—balance functionality, performance, and maintainability. This combined creation plan provides a clear roadmap for building a production-ready TTS application that serves users' needs with professional quality and reliability.

---

# 19. Appendix A: Keyboard Shortcuts

## General Shortcuts
| Shortcut | Action |
|----------|--------|
| Ctrl+O | Open file |
| Ctrl+S | Save audio |
| Ctrl+, | Open settings |
| Ctrl+1 | Switch to Main tab |
| Ctrl+2 | Switch to Voice Library tab |
| Ctrl+3 | Switch to Themes tab |
| Ctrl+4 | Switch to Settings tab |
| Ctrl+5 | Switch to Logs tab |
| F | Toggle fullscreen |
| F1 | Open help/documentation |

## Playback Controls
| Shortcut | Action |
|----------|--------|
| Space | Play/Pause |
| Escape | Stop playback |
| Left Arrow | Skip back 5 seconds |
| Right Arrow | Skip forward 5 seconds |
| Up Arrow | Increase volume 10% |
| Down Arrow | Decrease volume 10% |

## Text Highlighting Controls
| Shortcut | Action |
|----------|--------|
| H | Toggle highlighting on/off |
| Shift+H | Cycle highlight mode (Word → Sentence → Paragraph → Chunk) |
| Ctrl+H | Open highlight settings |
| Ctrl+Shift+H | Toggle auto-scroll |
| Alt+H | Toggle high-contrast highlight mode |

## Save Point Controls
| Shortcut | Action |
|----------|--------|
| S | Set save point at current position |
| G | Go to save point |
| R | Toggle reset save point (start from beginning) |
| Ctrl+Shift+S | Save all current positions |
| Ctrl+Shift+R | Clear current save point |

## File Browser Controls
| Shortcut | Action |
|----------|--------|
| Ctrl+Plus | Zoom in (file browser) |
| Ctrl+Minus | Zoom out (file browser) |
| Ctrl+0 | Reset zoom |
| Ctrl+D | Add to bookmarks |
| Backspace | Go up one directory |
| Alt+Left | Go back in history |
| Alt+Right | Go forward in history |

---

# 20. Appendix B: Supported File Formats Detail

## Document Formats
| Format | Extension | Library | Encryption Support |
|--------|-----------|---------|-------------------|
| PDF | .pdf | pdfplumber, pikepdf | Yes (RC4, AES-128/256) |
| Microsoft Word | .docx, .doc | python-docx, msoffcrypto | Yes (ECMA-376, RC4, XOR) |
| Microsoft Excel | .xlsx, .xls | openpyxl, xlrd, msoffcrypto | Yes (ECMA-376, RC4, XOR) |
| Microsoft PowerPoint | .pptx | python-pptx, msoffcrypto | Yes (ECMA-376 Agile) |
| LibreOffice Writer | .odt | odfpy | Yes (Blowfish, AES) |
| LibreOffice Calc | .ods | odfpy | Yes (Blowfish, AES) |
| LibreOffice Impress | .odp | odfpy | Yes (Blowfish, AES) |
| OpenDocument Text | .odt | odfpy | Yes (Blowfish, AES) |
| OpenDocument Spreadsheet | .ods | odfpy | Yes (Blowfish, AES) |
| OpenDocument Presentation | .odp | odfpy | Yes (Blowfish, AES) |
| Rich Text | .rtf | pypandoc | No |
| Plain Text | .txt | Native | No |
| Markdown | .md | Native | No |

## Ebook Formats
| Format | Extension | Library | Notes |
|--------|-----------|---------|-------|
| EPUB | .epub | ebooklib | Standard ebook |
| MOBI | .mobi | mobi | Kindle format |
| AZW3 | .azw3 | ebooklib | Kindle format |
| FB2 | .fb2 | beautifulsoup4 | FictionBook |

## Data Formats
| Format | Extension | Library | Notes |
|--------|-----------|---------|-------|
| CSV | .csv | pandas | Spreadsheet data |
| Excel | .xlsx, .xls | openpyxl, xlrd | Spreadsheet |
| JSON | .json | Native | Data interchange |
| XML | .xml | lxml | Structured data |

## Archive Formats
| Format | Extension | Library | Encryption Support |
|--------|-----------|---------|-------------------|
| ZIP | .zip | zipfile, pyzipper | Yes (AES-256, ZipCrypto) |
| 7-Zip | .7z | py7zr | Yes (AES-256) |
| RAR | .rar | rarfile | Yes (AES-256) |
| TAR | .tar | tarfile | No |
| GZIP | .gz, .tgz | gzip, tarfile | No |

## Audio Formats
| Format | Extension | Import | Export | Notes |
|--------|-----------|--------|--------|-------|
| WAV | .wav | Yes | Yes | PCM, lossless |
| MP3 | .mp3 | Yes | Yes | CBR/VBR |
| OGG | .ogg | Yes | Yes | Vorbis codec |
| FLAC | .flac | Yes | Yes | Lossless |
| M4A | .m4a | Yes | Yes | AAC codec |
| AAC | .aac | Yes | No | Import only |

---

*Document generated by Z.ai - Combined from TTS_Voices_Creation_Plan_V2.pdf and TTS_Voices_Creation_Plan_V2.1.md*
