#!/usr/bin/env python3
"""
health_check.py — TTS Voices Codebase Health Monitor
=====================================================

Maintained by the opencode AI assistant — see README.md.
Run from inside the TTSVoices project directory:

    python3 health_check.py              # check only
    python3 health_check.py --fix        # check + auto-fix safe issues
    python3 health_check.py --json       # machine-readable output (CI)
    python3 health_check.py --fix --json # fix then emit JSON

Exit codes:
    0  — all checks passed (or all failures were auto-fixed)
    1  — one or more FAIL items remain after any fixes
    2  — script usage error
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# ── Colours ──────────────────────────────────────────────────────────────────
_NO_COLOR = not sys.stdout.isatty() or os.environ.get("NO_COLOR")

def _c(code: str, text: str) -> str:
    return text if _NO_COLOR else f"\033[{code}m{text}\033[0m"

RED    = lambda t: _c("31;1", t)
GREEN  = lambda t: _c("32;1", t)
YELLOW = lambda t: _c("33;1", t)
CYAN   = lambda t: _c("36;1", t)
BLUE   = lambda t: _c("34;1", t)
DIM    = lambda t: _c("2",    t)
BOLD   = lambda t: _c("1",    t)

# ── Result dataclass ──────────────────────────────────────────────────────────
@dataclass
class Result:
    status: str          # PASS | FAIL | WARN | FIXED | INFO | SKIP
    category: str
    check: str
    detail: str = ""
    fix_applied: str = ""

    @property
    def icon(self) -> str:
        return {
            "PASS":  GREEN("✔"),
            "FAIL":  RED("✘"),
            "WARN":  YELLOW("⚠"),
            "FIXED": CYAN("⚙"),
            "INFO":  BLUE("ℹ"),
            "SKIP":  DIM("–"),
        }.get(self.status, "?")

    @property
    def label(self) -> str:
        return {
            "PASS":  GREEN("PASS "),
            "FAIL":  RED("FAIL "),
            "WARN":  YELLOW("WARN "),
            "FIXED": CYAN("FIXED"),
            "INFO":  BLUE("INFO "),
            "SKIP":  DIM("SKIP "),
        }.get(self.status, self.status)

# ── Registry ──────────────────────────────────────────────────────────────────
_checks: List[Callable] = []

def check(fn: Callable) -> Callable:
    """Decorator: register a check function."""
    _checks.append(fn)
    return fn

# ── Source cache ──────────────────────────────────────────────────────────────
_src_cache: dict = {}

def src(filename: str) -> str:
    if filename not in _src_cache:
        p = ROOT / filename
        _src_cache[filename] = p.read_text(errors="replace") if p.exists() else ""
    return _src_cache[filename]

def src_bytes(filename: str) -> bytes:
    p = ROOT / filename
    return p.read_bytes() if p.exists() else b""

ROOT = Path(__file__).parent.resolve()

# ═══════════════════════════════════════════════════════════════════════════════
# CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Category: Syntax ──────────────────────────────────────────────────────────

@check
def syntax_all_python(fix: bool) -> List[Result]:
    """All .py files parse without SyntaxError or SyntaxWarning."""
    results = []
    for pyfile in sorted(ROOT.glob("*.py")):
        try:
            ast.parse(pyfile.read_text(errors="replace"))
            results.append(Result("PASS", "Syntax", f"{pyfile.name} parses clean"))
        except SyntaxError as e:
            results.append(Result("FAIL", "Syntax", f"{pyfile.name}",
                                  detail=f"Line {e.lineno}: {e.msg}"))
    return results


@check
def syntax_no_invalid_escapes(fix: bool) -> List[Result]:
    """No SyntaxWarning: invalid escape sequences in any .py file."""
    import warnings, ast as _ast
    results = []
    for pyfile in sorted(ROOT.glob("*.py")):
        text = pyfile.read_text(errors="replace")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", SyntaxWarning)
            try:
                _ast.parse(text)
            except SyntaxError:
                pass
        sw = [w for w in caught if issubclass(w.category, SyntaxWarning)]
        if sw:
            detail = "; ".join(str(w.message) for w in sw[:3])
            results.append(Result("FAIL", "Syntax", f"{pyfile.name} invalid escapes",
                                  detail=detail))
        else:
            results.append(Result("PASS", "Syntax", f"{pyfile.name} no invalid escapes"))
    return results


@check
def syntax_c_compiles(fix: bool) -> List[Result]:
    """audio_fast.c compiles without errors."""
    c_file = ROOT / "audio_fast.c"
    if not c_file.exists():
        return [Result("SKIP", "Syntax", "audio_fast.c not found")]
    if not shutil.which("gcc"):
        return [Result("WARN", "Syntax", "gcc not found — cannot compile audio_fast.c",
                       detail="Install build-essential: sudo apt install build-essential")]
    result = subprocess.run(
        ["gcc", "-O2", "-shared", "-fPIC", "-o", "/dev/null", str(c_file)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return [Result("PASS", "Syntax", "audio_fast.c compiles")]
    return [Result("FAIL", "Syntax", "audio_fast.c compile error",
                   detail=result.stderr.strip()[:300])]

# ── Category: Security ────────────────────────────────────────────────────────

@check
def sec_plugins_dir_0700(fix: bool) -> List[Result]:
    """PLUGINS_DIR created with mode=0o700 at all three sites."""
    text = src("ttsvoices.py")
    sites = text.count("PLUGINS_DIR.mkdir")
    with_mode = text.count("PLUGINS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)")
    with_chmod = text.count("_os.chmod(PLUGINS_DIR, 0o700)")

    if with_mode >= 3 and with_chmod >= 3:
        return [Result("PASS", "Security", "PLUGINS_DIR 0700 at all mkdir sites")]

    detail = f"mkdir calls: {sites}, with mode=0o700: {with_mode}, with chmod: {with_chmod}"
    if fix:
        # Auto-fix: replace any bare mkdir
        fixed = text.replace(
            "PLUGINS_DIR.mkdir(parents=True, exist_ok=True)\n",
            "PLUGINS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)\n"
            "        try: import os as _os; _os.chmod(PLUGINS_DIR, 0o700)\n"
            "        except Exception: pass\n"
        )
        if fixed != text:
            (ROOT / "ttsvoices.py").write_text(fixed)
            _src_cache.pop("ttsvoices.py", None)
            return [Result("FIXED", "Security", "PLUGINS_DIR 0700", fix_applied="Added mode=0o700 + chmod to bare mkdir calls")]
    return [Result("FAIL", "Security", "PLUGINS_DIR missing 0700 at some sites", detail=detail)]


@check
def sec_savepoints_dir_0700(fix: bool) -> List[Result]:
    """SAVEPOINTS_DIR created with mode=0o700."""
    text = src("save_point_manager.py")
    if "mode=0o700" in text and "chmod" in text:
        return [Result("PASS", "Security", "SAVEPOINTS_DIR created 0700")]
    if fix:
        fixed = text.replace(
            "os.makedirs(SAVEPOINTS_DIR, exist_ok=True)",
            "os.makedirs(SAVEPOINTS_DIR, mode=0o700, exist_ok=True)\n        "
            "try:\n            os.chmod(SAVEPOINTS_DIR, 0o700)\n        "
            "except OSError:\n            pass"
        )
        if fixed != text:
            (ROOT / "save_point_manager.py").write_text(fixed)
            _src_cache.pop("save_point_manager.py", None)
            return [Result("FIXED", "Security", "SAVEPOINTS_DIR 0700",
                           fix_applied="Added mode=0o700 + chmod")]
    return [Result("FAIL", "Security", "SAVEPOINTS_DIR not locked to 0700",
                   detail="Other local users can read/modify save point files")]


@check
def sec_no_dev_path(fix: bool) -> List[Result]:
    """No hardcoded developer machine paths in update.sh."""
    text = src("update.sh")
    bad = "AI WRITTEN GAME CODE"
    if bad not in text:
        return [Result("PASS", "Security", "No developer path in update.sh")]
    if fix:
        lines = text.splitlines(keepends=True)
        cleaned = [l for l in lines if bad not in l]
        fixed = "".join(cleaned)
        (ROOT / "update.sh").write_text(fixed)
        _src_cache.pop("update.sh", None)
        return [Result("FIXED", "Security", "Developer path removed from update.sh",
                       fix_applied="Deleted hardcoded path line")]
    return [Result("FAIL", "Security", "Hardcoded developer path in update.sh",
                   detail="Exposes local directory structure; will fail on all other machines")]


@check
def sec_sha256_configured(fix: bool) -> List[Result]:
    """SHA-256 hashes are filled in for Kokoro model files."""
    text = src("voice_library.py")
    hashes = re.findall(r'"sha256":\s*"([^"]*)"', text)
    empty = [i+1 for i, h in enumerate(hashes) if not h.strip()]
    if not empty:
        return [Result("PASS", "Security", f"SHA-256 hashes present ({len(hashes)} models)")]
    return [Result("WARN", "Security",
                   f"SHA-256 hashes empty for {len(empty)}/{len(hashes)} model(s)",
                   detail="Run:  sha256sum ~/.ttsvoices/models/kokoro-v1.0.onnx\n"
                          "      sha256sum ~/.ttsvoices/models/voices-v1.0.bin\n"
                          "Then paste into KOKORO_MODELS in voice_library.py")]


@check
def sec_atomic_download(fix: bool) -> List[Result]:
    """Model downloads use .tmp staging file + os.replace() (atomic)."""
    text = src("voice_library.py")
    if "dest_tmp" in text and "os.replace(dest_tmp, dest)" in text:
        return [Result("PASS", "Security", "Downloads use atomic .tmp → rename")]
    return [Result("FAIL", "Security", "Download writes directly to final path",
                   detail="Crash mid-download leaves corrupt file that looks valid on next start")]


@check
def sec_pkcs7_strict(fix: bool) -> List[Result]:
    """odf_crypto.py uses strict PKCS7 padding validation."""
    text = src("odf_crypto.py")
    # Strict check: validates ALL padding bytes equal pad value
    if "dec[-pad:] != bytes([pad]) * pad" in text:
        return [Result("PASS", "Security", "PKCS7 strict padding validation present")]
    if fix:
        old = "            pad = dec[-1] if dec else 0\n            unc = dec[:-pad] if 1 <= pad <= 16 else dec"
        new = ("            if not dec: continue\n"
               "            pad = dec[-1]\n"
               "            if not (1 <= pad <= 16): continue\n"
               "            if dec[-pad:] != bytes([pad]) * pad: continue\n"
               "            unc = dec[:-pad]")
        text2 = src("odf_crypto.py")
        if old in text2:
            (ROOT / "odf_crypto.py").write_text(text2.replace(old, new))
            _src_cache.pop("odf_crypto.py", None)
            return [Result("FIXED", "Security", "PKCS7 strict padding",
                           fix_applied="Added full padding byte validation")]
    return [Result("FAIL", "Security", "PKCS7 padding not strictly validated",
                   detail="Theoretical padding oracle — replace dec[-1] check with full verification")]

# ── Category: Bug Fixes ───────────────────────────────────────────────────────

@check
def bug_stop_race(fix: bool) -> List[Result]:
    """Stop-button race: begin_session() exists; _stop_event NOT cleared in play_wav."""
    audio = src("audio_handler.py")
    has_fn = "def begin_session():" in audio
    play_wav_block = audio.split("def play_wav")[1][:400] if "def play_wav" in audio else ""
    clears_in_play = "_stop_event.clear()" in play_wav_block

    results = []
    if has_fn:
        results.append(Result("PASS", "Bug Fixes", "begin_session() exists in audio_handler.py"))
    else:
        results.append(Result("FAIL", "Bug Fixes", "begin_session() missing",
                               detail="Add begin_session() to audio_handler.py"))

    if clears_in_play:
        results.append(Result("FAIL", "Bug Fixes",
                               "_stop_event.clear() inside play_wav() — race condition",
                               detail="Move clear() to begin_session(), add early-exit check in play_wav"))
    else:
        results.append(Result("PASS", "Bug Fixes", "_stop_event not cleared inside play_wav"))

    tts = src("ttsvoices.py")
    if "audio_handler.begin_session()" in tts:
        results.append(Result("PASS", "Bug Fixes", "begin_session() called before worker in _on_speak"))
    else:
        results.append(Result("FAIL", "Bug Fixes", "begin_session() not called in _on_speak",
                               detail="Add audio_handler.begin_session() before self._stop_flag.clear()"))
    return results


@check
def bug_c_oob(fix: bool) -> List[Result]:
    """audio_fast.c has OOB bounds check in BOTH the size-count and copy passes."""
    text = src("audio_fast.c")
    check_str = "if (sz > (uint32_t)(end - p - 8)) break;"
    count = text.count(check_str)
    if count >= 2:
        return [Result("PASS", "Bug Fixes", f"C OOB check present in both passes ({count}×)")]
    return [Result("FAIL", "Bug Fixes", f"C OOB check missing or incomplete ({count}/2 passes)",
                   detail="Add:  if (sz > (uint32_t)(end - p - 8)) break;\nbefore p += 8 + sz in BOTH while loops")]


@check
def bug_save_point_wired(fix: bool) -> List[Result]:
    """SavePointManager is instantiated and wired into speak/stop lifecycle."""
    tts = src("ttsvoices.py")
    checks = [
        ("_ensure_save_mgr",         "_ensure_save_mgr() helper"),
        ("mgr.load_for_file(path)",  "load_for_file() called on file load"),
        ("mgr.set_save_point(",      "set_save_point() called on stop"),
        ("mgr.get_start_chunk()",    "get_start_chunk() used in _on_speak"),
        ("mgr.clear_save_point()",   "clear_save_point() called on natural finish"),
    ]
    results = []
    for needle, label in checks:
        if needle in tts:
            results.append(Result("PASS", "Bug Fixes", f"SavePointManager: {label}"))
        else:
            results.append(Result("FAIL", "Bug Fixes", f"SavePointManager: {label} MISSING",
                                   detail=f"Search for '{needle}' in ttsvoices.py"))
    return results


@check
def bug_google_stt_duration(fix: bool) -> List[Result]:
    """Google STT uses src.duration (lowercase), not src.DURATION."""
    text = src("ttsvoices.py")
    if "src.DURATION" in text:
        if fix:
            fixed = text.replace(
                "src.DURATION if hasattr(src, \"DURATION\")",
                "src.duration if hasattr(src, \"duration\")"
            )
            (ROOT / "ttsvoices.py").write_text(fixed)
            _src_cache.pop("ttsvoices.py", None)
            return [Result("FIXED", "Bug Fixes", "Google STT src.DURATION → src.duration",
                           fix_applied="Fixed attribute case")]
        return [Result("FAIL", "Bug Fixes", "Google STT uses src.DURATION (wrong case)",
                       detail="speech_recognition uses lowercase .duration — long files always time out")]
    return [Result("PASS", "Bug Fixes", "Google STT uses src.duration (correct)")]


@check
def bug_camelcase_regex(fix: bool) -> List[Result]:
    """voices.py CamelCase split regex is r'\\1 \\2' (not r' \\1 \\2' with leading space)."""
    text = src("voices.py")
    bad  = r"r'\1 \2'"        # produces double space before uppercase letter
    good = r"r'\1 \2'"        # wait — these look the same in raw repr
    # The actual issue: r' \1 \2' adds space BEFORE the lowercase letter too
    if r"r' \1 \2'" in text:
        if fix:
            fixed = text.replace(r"r' \1 \2'", r"r'\1 \2'")
            (ROOT / "voices.py").write_text(fixed)
            _src_cache.pop("voices.py", None)
            return [Result("FIXED", "Bug Fixes", "CamelCase regex leading space removed",
                           fix_applied="r' \\1 \\2' → r'\\1 \\2'")]
        return [Result("FAIL", "Bug Fixes", "CamelCase regex adds extra leading space",
                       detail="r' \\1 \\2' causes stuttering; should be r'\\1 \\2'")]
    return [Result("PASS", "Bug Fixes", "CamelCase regex correct (r'\\1 \\2')")]


@check
def bug_vosk_wav_header(fix: bool) -> List[Result]:
    """Vosk branch uses wave module, not raw f.read(44) skip."""
    text = src("ttsvoices.py")
    if "f.read(44)" in text:
        return [Result("FAIL", "Bug Fixes", "Vosk still uses hardcoded f.read(44) header skip",
                       detail="WAVs with LIST/fact chunks fail. Replace with wave.open().readframes()")]
    if "_wave.open(" in text or "wave.open(" in text:
        return [Result("PASS", "Bug Fixes", "Vosk uses wave module (correct)")]
    return [Result("WARN", "Bug Fixes", "Could not confirm Vosk WAV reading method",
                   detail="Manually verify _transcribe_worker Vosk branch in ttsvoices.py")]


@check
def bug_espeak_data_path(fix: bool) -> List[Result]:
    """voices.py resolves ESPEAK_DATA_PATH at import time (espeakng-loader fix)."""
    text = src("voices.py")
    checks = [
        ("_find_espeak_data_dir",  "espeak data dir resolver"),
        ("_resolve_espeak_binary", "espeak binary resolver"),
        ("_ESPEAK_DATA_DIR",       "_ESPEAK_DATA_DIR module-level var"),
        ("_ESPEAK_BINARY",         "_ESPEAK_BINARY module-level var"),
    ]
    results = []
    for needle, label in checks:
        s = "PASS" if needle in text else "FAIL"
        results.append(Result(s, "Bug Fixes", f"espeak: {label}",
                              detail="" if s == "PASS" else f"'{needle}' missing from voices.py"))
    return results

# ── Category: Features ────────────────────────────────────────────────────────

@check
def feat_tooltips(fix: bool) -> List[Result]:
    """Tooltip class and attach_tooltip() helper are present in ttsvoices.py."""
    text = src("ttsvoices.py")
    results = []
    for sym, label in [("class Tooltip:", "Tooltip class"),
                        ("def attach_tooltip(", "attach_tooltip() helper"),
                        ("_DELAY_MS", "Tooltip delay constant")]:
        results.append(Result(
            "PASS" if sym in text else "FAIL",
            "Features", f"Tooltips: {label}"
        ))
    return results


@check
def feat_voice_preview_rename(fix: bool) -> List[Result]:
    """Voice ▶ preview and ✎ rename buttons are wired up."""
    text = src("ttsvoices.py")
    results = []
    for sym, label in [("_preview_btn", "preview button widget"),
                        ("_preview_voice", "preview voice method"),
                        ("_rename_btn",    "rename button widget"),
                        ("_rename_voice",  "rename voice method")]:
        results.append(Result(
            "PASS" if sym in text else "FAIL",
            "Features", f"Voice UI: {label}"
        ))
    return results


@check
def feat_gpu_toggle(fix: bool) -> List[Result]:
    """⚡ CPU/GPU toggle button exists."""
    text = src("ttsvoices.py")
    has = "_toggle_gpu" in text and "_gpu_btn" in text
    return [Result("PASS" if has else "FAIL", "Features", "GPU toggle button")]


@check
def feat_batch_export(fix: bool) -> List[Result]:
    """synthesize_batch() is available in voices.py."""
    text = src("voices.py")
    return [Result(
        "PASS" if "def synthesize_batch(" in text else "FAIL",
        "Features", "synthesize_batch() for parallel export"
    )]

# ── Category: Code Quality ────────────────────────────────────────────────────

@check
def quality_no_bare_except_in_critical(fix: bool) -> List[Result]:
    """No bare 'except:' (catch-all) in synthesis or playback paths."""
    results = []
    for fname in ("voices.py", "audio_handler.py"):
        text = src(fname)
        # bare except: catches ALL exceptions including SystemExit, KeyboardInterrupt
        matches = [(i+1, l.strip()) for i, l in enumerate(text.splitlines())
                   if re.match(r'\s+except\s*:', l)]
        if matches:
            detail = "; ".join(f"line {ln}" for ln, _ in matches[:5])
            results.append(Result("WARN", "Quality",
                                   f"{fname} has bare except: at {len(matches)} sites",
                                   detail=detail))
        else:
            results.append(Result("PASS", "Quality", f"{fname} no bare except:"))
    return results


@check
def quality_no_print_statements(fix: bool) -> List[Result]:
    """No stray print() calls in library modules (they bypass bug_tracker)."""
    results = []
    lib_files = ["voices.py", "audio_handler.py", "voice_library.py",
                 "save_point_manager.py", "odf_crypto.py", "file_extractor.py"]
    for fname in lib_files:
        text = src(fname)
        lines = [(i+1, l) for i, l in enumerate(text.splitlines())
                 if re.search(r'\bprint\s*\(', l) and not l.strip().startswith('#')]
        if lines:
            detail = ", ".join(f"line {n}" for n, _ in lines[:5])
            results.append(Result("WARN", "Quality",
                                   f"{fname} has print() at {len(lines)} sites",
                                   detail=f"Use bug_tracker.info/warning/error instead ({detail})"))
        else:
            results.append(Result("PASS", "Quality", f"{fname} no stray print()"))
    return results


@check
def quality_version_consistency(fix: bool) -> List[Result]:
    """VERSION file, ttsvoices.py __version__, and CHANGELOG top entry agree."""
    results = []
    version_file = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else ""
    tts = src("ttsvoices.py")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', tts)
    code_ver = m.group(1) if m else ""
    changelog = src("CHANGELOG.md")
    cl_m = re.search(r'##\s+v?(\d+\.\d+\.\d+)', changelog)
    cl_ver = cl_m.group(1) if cl_m else ""

    if version_file:
        results.append(Result("INFO", "Quality", f"VERSION file: {version_file}"))
    if code_ver:
        results.append(Result("INFO", "Quality", f"__version__ in code: {code_ver}"))
    if cl_ver:
        results.append(Result("INFO", "Quality", f"Latest CHANGELOG entry: {cl_ver}"))

    versions = [v for v in (version_file, code_ver, cl_ver) if v]
    if len(set(versions)) <= 1:
        if versions:
            results.append(Result("PASS", "Quality", f"All version strings agree: {versions[0]}"))
    else:
        results.append(Result("WARN", "Quality", "Version strings disagree",
                               detail=f"VERSION={version_file!r}  __version__={code_ver!r}  CHANGELOG={cl_ver!r}"))
    return results


@check
def quality_requirements_complete(fix: bool) -> List[Result]:
    """requirements.txt exists and is non-empty."""
    p = ROOT / "requirements.txt"
    if not p.exists():
        return [Result("WARN", "Quality", "requirements.txt missing")]
    lines = [l for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]
    return [Result("PASS" if lines else "WARN", "Quality",
                   f"requirements.txt has {len(lines)} package entries")]

# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_all(do_fix: bool) -> List[Result]:
    all_results: List[Result] = []
    for fn in _checks:
        try:
            res = fn(fix=do_fix)
            if isinstance(res, list):
                all_results.extend(res)
            else:
                all_results.append(res)
        except Exception as e:
            all_results.append(Result("FAIL", "Runner",
                                       f"Check {fn.__name__} threw exception",
                                       detail=str(e)))
    return all_results


def print_report(results: List[Result], do_fix: bool) -> None:
    # Group by category
    cats: dict = {}
    for r in results:
        cats.setdefault(r.category, []).append(r)

    width = 72
    print()
    print(BOLD("━" * width))
    print(BOLD(f"  TTS Voices — Codebase Health Check"))
    print(BOLD(f"  Root: {ROOT}"))
    print(BOLD("━" * width))

    for cat, items in cats.items():
        print(f"\n  {BOLD(CYAN(cat.upper()))}")
        for r in items:
            # Pad check name
            name = r.check[:56].ljust(56)
            line = f"  {r.icon} {r.label}  {name}"
            print(line)
            if r.detail:
                for dline in r.detail.splitlines():
                    print(f"           {DIM(dline)}")
            if r.fix_applied:
                print(f"           {CYAN('→ Fixed: ')}{r.fix_applied}")

    # Summary
    counts = {s: sum(1 for r in results if r.status == s)
              for s in ("PASS", "FAIL", "WARN", "FIXED", "INFO", "SKIP")}
    print()
    print(BOLD("━" * width))
    print(f"  {GREEN(str(counts['PASS']))} passed  "
          f"{RED(str(counts['FAIL']))} failed  "
          f"{YELLOW(str(counts['WARN']))} warnings  "
          f"{CYAN(str(counts['FIXED']))} auto-fixed  "
          f"{BLUE(str(counts['INFO']))} info")
    print(BOLD("━" * width))
    print()

    remaining_fails = [r for r in results if r.status == "FAIL"]
    if remaining_fails:
        print(RED(f"  ✘ {len(remaining_fails)} issue(s) require manual attention:\n"))
        for r in remaining_fails:
            print(f"    {RED('•')} [{r.category}] {r.check}")
            if r.detail:
                for dl in r.detail.splitlines()[:2]:
                    print(f"      {DIM(dl)}")
        print()
    elif counts["WARN"]:
        print(YELLOW("  ⚠ All critical checks passed. Review warnings above.\n"))
    else:
        print(GREEN("  ✔ Codebase is fully clean. Nothing to fix.\n"))


def emit_json(results: List[Result]) -> None:
    out = {
        "summary": {s: sum(1 for r in results if r.status == s)
                    for s in ("PASS", "FAIL", "WARN", "FIXED", "INFO", "SKIP")},
        "checks": [
            {"status": r.status, "category": r.category,
             "check": r.check, "detail": r.detail, "fix_applied": r.fix_applied}
            for r in results
        ]
    }
    print(json.dumps(out, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    args = sys.argv[1:]
    do_fix  = "--fix"  in args
    do_json = "--json" in args
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    if do_fix and not do_json:
        print(CYAN("\n  Running in FIX mode — safe issues will be patched automatically.\n"))

    results = run_all(do_fix=do_fix)

    if do_json:
        emit_json(results)
    else:
        print_report(results, do_fix)

    fails = sum(1 for r in results if r.status == "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
