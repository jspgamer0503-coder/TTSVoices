import pytest
import sys
sys.path.insert(0, ".")

from odf_crypto import (
    _aes_cbc_decrypt,
    _derive_key_deterministic,
    _xml_to_text_odf,
)


class TestODFCrypto:
    def test_aes_cbc_decrypt_with_pycryptodome(self):
        key = b"0123456789abcdef0123456789abcdef"
        iv = b"0123456789abcdef"
        plain = b"1234567890abcdef"  # 16 bytes, block-aligned
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_CBC, iv)
        ct = cipher.encrypt(plain)
        result = _aes_cbc_decrypt(key, iv, ct)
        assert result == plain

    def test_xml_to_text_odf_basic(self):
        xml = '<text:p>Hello</text:p><text:p>World</text:p>'
        result = _xml_to_text_odf(xml)
        assert "Hello" in result
        assert "World" in result

    def test_derive_key_deterministic(self):
        params = {
            "salt": b"0123456789abcdef",
            "iterations": 1000,
            "key_size": 32,
        }
        k1 = _derive_key_deterministic("password", params, "sha256_sha1")
        k2 = _derive_key_deterministic("password", params, "sha256_sha1")
        assert k1 == k2
        k3 = _derive_key_deterministic("wrong", params, "sha256_sha1")
        assert k1 != k3
