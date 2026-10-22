from __future__ import annotations

import runpy
from pathlib import Path

checker = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "check_privacy.py"))


def test_private_paths_and_complete_credentials_are_detected_without_echoing_values():
    values = [
        b"/" + b"Users/" + b"private-person/file.png",
        b"C:" + b"\\Users\\" + b"private-person\\file.png",
        b"gh" + b"p_" + b"A" * 36,
        b"pass" + b'word="' + b"example-secret-123" + b'"',
        b"-----BEGIN " + b"PRIVATE KEY-----\n" + b"A" * 96 + b"\n-----END PRIVATE KEY-----",
    ]
    for value in values:
        findings = checker["findings"](value)
        assert findings
        assert all(value.decode() not in finding for finding in findings)


def test_certificate_identity_and_tls_parser_strings_are_not_secrets():
    assert not checker["findings"](b"Authority=Developer ID Application: Example Author (ABC123)")
    assert not checker["findings"](b"-----BEGIN " + b"PRIVATE KEY-----")
