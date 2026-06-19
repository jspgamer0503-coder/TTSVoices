"""
ODF Decryption Module for LibreOffice ODT files.

All cryptographic primitives delegate to audited libraries:
  - pycryptodome (Crypto.Cipher.AES) or cryptography.hazmat.primitives.ciphers
  - cryptography.hazmat.primitives.kdf.pbkdf2 or hashlib.pbkdf2_hmac
  - argon2-cffi (argon2.low_level)

NO hand-rolled AES, SHA, or padding implementations.
ODF uses the encrypted ZIP structure (not msoffcrypto-tool, which is for DOCX/XLSX).

Confirmed algorithm for LibreOffice 24.x - 26.x:
  1. start_key = SHA-256(password.encode("utf-8"))
  2. key       = PBKDF2-HMAC-SHA1(start_key, salt, iterations, key_size)
  3. plaintext = AES-256-CBC-decrypt(key, iv, ciphertext)
  4. xml       = zlib.decompress(remove_pkcs7_padding(plaintext), -15)
"""
import base64, hashlib, zipfile, zlib, xml.etree.ElementTree as ET, re

_MNS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
_NS  = {"manifest": _MNS}
_A   = "{%s}" % _MNS


# ── AES-256-CBC ───────────────────────────────────────────────────────────────
def _aes_cbc_decrypt(key, iv, ciphertext):
    try:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
    except ImportError:
        pass
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        c = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        d = c.decryptor()
        return d.update(ciphertext) + d.finalize()
    except ImportError:
        pass
    raise RuntimeError("Install pycryptodome: pip install pycryptodome")


# ── Key derivation ────────────────────────────────────────────────────────────
def _derive_key_deterministic(password, params, variant="sha256_sha1"):
    """
    Derive decryption key using the algorithm specified in the manifest.
    Relies solely on audited hashlib/cryptography primitives.

    Variants (confirmed against LibreOffice versions):
      "sha256_sha1" (default): SHA-256(start_key) + PBKDF2-HMAC-SHA1  — LO 24.x-26.x
      "sha1_sha1":   SHA-1(start_key)   + PBKDF2-HMAC-SHA1  — LO < 5.4
      "raw_sha256":  raw password bytes + PBKDF2-HMAC-SHA256 — third-party
    """
    pw   = password.encode("utf-8")
    salt = params["salt"]
    n    = params["iterations"]
    ksz  = params["key_size"]

    if variant == "sha256_sha1":
        start_key = hashlib.sha256(pw).digest()
        prf = "sha1"
    elif variant == "sha1_sha1":
        start_key = hashlib.sha1(pw).digest()
        prf = "sha1"
    elif variant == "raw_sha256":
        start_key = pw
        prf = "sha256"
    else:
        start_key = hashlib.sha256(pw).digest()
        prf = "sha1"

    return _pbkdf2_sha1(start_key, salt, n, ksz, prf)


def _pbkdf2_sha1(pw_bytes, salt, iterations, key_len, prf="sha1"):
    """PBKDF2 with configurable PRF — always delegates to audited libraries."""
    try:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.backends import default_backend
        hash_algo = {"sha1": hashes.SHA1(), "sha256": hashes.SHA256()}.get(prf, hashes.SHA1())
        kdf = PBKDF2HMAC(algorithm=hash_algo, length=key_len,
                          salt=salt, iterations=iterations,
                          backend=default_backend())
        return kdf.derive(pw_bytes)
    except ImportError:
        return hashlib.pbkdf2_hmac(prf, pw_bytes, salt, iterations, dklen=key_len)


# Backward-compatible alias for bug_tracker.py
_derive_key = _derive_key_deterministic

# ── Manifest parsing ──────────────────────────────────────────────────────────
def _parse_manifest(manifest_xml):
    root    = ET.fromstring(manifest_xml)
    entries = {}

    for entry in root.findall("manifest:file-entry", _NS):
        raw_path = entry.get(f"{_A}full-path", "")
        path = raw_path.lstrip("./").lstrip("/") or raw_path

        enc = entry.find("manifest:encryption-data", _NS)
        if enc is None:
            continue

        algo_el = enc.find("manifest:algorithm", _NS)
        kd_el   = enc.find("manifest:key-derivation", _NS)
        sk_el   = enc.find("manifest:start-key-generation", _NS)

        def ga(el, attr, default=""):
            if el is None: return default
            # Must use "is not None" — empty elements are falsy in ET
            return el.get(f"{_A}{attr}", el.get(attr, default))

        def gai(el, attr, default):
            v = ga(el, attr); return int(v) if v else default

        iv_b64   = ga(algo_el, "initialisation-vector")
        salt_b64 = ga(kd_el,   "salt")
        ck_b64   = ga(enc,     "checksum")

        LOEXT = "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0"
        la    = "{%s}" % LOEXT

        entries[path] = {
            "algo":           ga(algo_el, "algorithm-name"),
            "iv":             base64.b64decode(iv_b64)   if iv_b64   else b"",
            "kd_name":        ga(kd_el,   "key-derivation-name"),
            "key_size":       gai(kd_el,  "key-size", 32),
            "iterations":     gai(kd_el,  "iteration-count", 100000),
            "salt":           base64.b64decode(salt_b64) if salt_b64 else b"",
            "checksum_type":  ga(enc,     "checksum-type"),
            "checksum":       base64.b64decode(ck_b64)   if ck_b64   else b"",
            "start_key_algo": ga(sk_el,   "start-key-generation-name"),
            "start_key_size": gai(sk_el,  "key-size", 32),
            "argon2_t":       int(kd_el.get(f"{la}argon2-iterations", "3"))    if kd_el is not None else 3,
            "argon2_m":       int(kd_el.get(f"{la}argon2-memory",     "65536")) if kd_el is not None else 65536,
            "argon2_p":       int(kd_el.get(f"{la}argon2-lanes",      "4"))    if kd_el is not None else 4,
        }
    return entries


def _parse_manifest_raw(manifest_xml, target_path="content.xml"):
    """Regex fallback when ET returns empty values."""
    text = manifest_xml.decode("utf-8", errors="replace")

    def grab(pattern):
        m = re.search(pattern, text)
        return m.group(1) if m else None

    B = r"[A-Za-z0-9+/=]+"
    iv   = grab(r"initialisation-vector=['\"](" + B + r")['\"]")
    salt = grab(r"(?:[\s>\"'])salt=['\"](" + B + r")['\"]")
    itr  = grab(r"iteration-count=['\"](\d+)['\"]")
    ksz  = grab(r"key-size=['\"](\d+)['\"]")
    ck   = grab(r"(?:[\s>])checksum=['\"](" + B + r")['\"]")
    ckt  = grab(r"checksum-type=['\"]([^'\"]+)['\"]")
    sk   = grab(r"start-key-generation-name=['\"]([^'\"]+)['\"]")

    if not iv or not salt:
        return {}
    try:
        return {
            "algo":           "http://www.w3.org/2001/04/xmlenc#aes256-cbc",
            "iv":             base64.b64decode(iv),
            "kd_name":        "PBKDF2",
            "key_size":       int(ksz) if ksz else 32,
            "iterations":     int(itr) if itr else 100000,
            "salt":           base64.b64decode(salt),
            "checksum_type":  ckt or "sha256",
            "checksum":       base64.b64decode(ck) if ck else b"",
            "start_key_algo": sk or "sha256",
            "start_key_size": 32,
            "argon2_t": 3, "argon2_m": 65536, "argon2_p": 4,
        }
    except Exception:
        return {}


def _xml_to_text_odf(xml):
    """Convert ODF content.xml to readable plain text."""
    xml = re.sub(r'<(text:p|text:h|text:list-item)[^>]*/>', '\n', xml)
    xml = re.sub(r'<(text:p|text:h|text:list-item)[^>]*>',  '\n', xml)
    xml = re.sub(r'</(text:p|text:h|text:list-item)>',       '\n', xml)
    xml = re.sub(r'<text:tab[^>]*/>',        '\t', xml)
    xml = re.sub(r'<text:line-break[^>]*/>', '\n', xml)
    xml = re.sub(r'<[^>]+>', '', xml)
    xml = (xml.replace('&amp;', '&').replace('&lt;', '<')
               .replace('&gt;', '>').replace('&quot;', '"')
               .replace('&apos;', "'").replace('&#160;', ' '))
    xml = re.sub(r'[ \t]+', ' ', xml)
    xml = re.sub(r'\n{3,}', '\n\n', xml)
    return xml.strip()


def is_odf_encrypted(path):
    """True if ODT contains ODF-level encryption."""
    try:
        with zipfile.ZipFile(path, "r") as z:
            return b"encryption-data" in z.read("META-INF/manifest.xml")
    except Exception:
        return False


def decrypt_odt(path, password):
    """
    Decrypt LibreOffice password-protected ODT.
    Algorithm confirmed against LibreOffice 24.x-26.x.
    """
    with zipfile.ZipFile(path, "r") as z:
        try:
            manifest_xml = z.read("META-INF/manifest.xml")
        except KeyError:
            raise RuntimeError("Not a valid ODF file — manifest not found.")

        entries = _parse_manifest(manifest_xml)

        # Find content.xml params — try all path variants
        params = None
        for key in ("content.xml", "./content.xml", "/content.xml"):
            p = entries.get(key)
            if p and p.get("iv") and p.get("salt"):
                params = p
                break

        # Regex fallback if ET parser returned empty values
        if params is None or not params.get("iv") or not params.get("salt"):
            params = _parse_manifest_raw(manifest_xml)

        if not params or not params.get("iv") or not params.get("salt"):
            # Not encrypted — read directly
            for cp in ("content.xml", "./content.xml"):
                try:
                    raw  = z.read(cp)
                    text = _xml_to_text_odf(raw.decode("utf-8", errors="replace"))
                    if text.strip():
                        return text
                except KeyError:
                    continue
            raise RuntimeError("content.xml not found — file may be corrupt.")

        # Read encrypted bytes
        enc_data = None
        for cp in ("content.xml", "./content.xml", "/content.xml"):
            try:
                enc_data = z.read(cp)
                break
            except KeyError:
                continue
        if enc_data is None:
            raise RuntimeError("content.xml not found in archive.")

    # ── Argon2id + AES-GCM (LO 24.8+ opt-in) ────────────────────────────────
    if "argon2" in params.get("kd_name", "").lower():
        return _decrypt_argon2_gcm(password, params, enc_data)

    # ── Standard: SHA256 → PBKDF2-SHA1 → AES-256-CBC ─────────────────────────
    # Try variants in deterministic order: confirmed first, then fallbacks.
    for variant in ("sha256_sha1", "sha1_sha1", "raw_sha256"):
        try:
            key = _derive_key_deterministic(password, params, variant)
            dec = _aes_cbc_decrypt(key, params["iv"], enc_data)
            if not dec:
                continue

            pad = dec[-1]
            if not (1 <= pad <= 16):
                continue
            if dec[-pad:] != bytes([pad]) * pad:
                continue
            unc = dec[:-pad]

            # Try decompression variants
            for decomp in (
                lambda d: zlib.decompress(d, -zlib.MAX_WBITS),
                lambda d: zlib.decompress(d),
                lambda d: d,
            ):
                try:
                    xml_bytes = decomp(unc)
                    stripped  = xml_bytes.lstrip(b"\xef\xbb\xbf\x00")
                    if stripped[:5] in (b"<?xml", b"<offi", b"<text", b"<mani"):
                        text = _xml_to_text_odf(xml_bytes.decode("utf-8", errors="replace"))
                        if text.strip():
                            return text
                except Exception:
                    continue
        except Exception:
            continue

    raise RuntimeError(
        "Incorrect password — the file could not be decrypted.\n"
        "Please check your password and try again."
    )


def _decrypt_argon2_gcm(password, params, enc_data):
    """Argon2id + AES-256-GCM (LibreOffice 24.8+ wholesome encryption, opt-in)."""
    try:
        from argon2.low_level import hash_secret_raw, Type
    except ImportError:
        raise RuntimeError("Install argon2-cffi: pip install argon2-cffi")

    start_key = hashlib.sha256(password.encode("utf-8")).digest()
    key = hash_secret_raw(
        secret=start_key,
        salt=params["salt"],
        time_cost=params.get("argon2_t", 3),
        memory_cost=params.get("argon2_m", 65536),
        parallelism=params.get("argon2_p", 4),
        hash_len=params.get("key_size", 32),
        type=Type.ID,
    )

    try:
        from Crypto.Cipher import AES
        plain = AES.new(key, AES.MODE_GCM, nonce=params["iv"]).decrypt_and_verify(
            enc_data[:-16], enc_data[-16:])
    except Exception:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            plain = AESGCM(key).decrypt(params["iv"], enc_data, None)
        except Exception as e:
            raise RuntimeError(f"Argon2/AES-GCM failed: {e}")

    for decomp in (lambda d: zlib.decompress(d, -zlib.MAX_WBITS), lambda d: d):
        try:
            xml_bytes = decomp(plain)
            if xml_bytes.lstrip(b"\xef\xbb\xbf")[:5] in (b"<?xml", b"<offi"):
                return _xml_to_text_odf(xml_bytes.decode("utf-8", errors="replace"))
        except Exception:
            continue
    raise RuntimeError("Argon2 decryption succeeded but XML not readable.")
