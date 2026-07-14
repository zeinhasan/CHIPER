"""Tests for app.utils.security (SSRF prevention — NB-1, NB-6)."""

from app.utils.security import is_private_ip, is_safe_url, validate_url_safe


# ── is_private_ip: literal IPs (no DNS needed) ──────────────────────


def test_loopback_ipv4_blocked():
    assert is_private_ip("127.0.0.1")
    assert is_private_ip("127.5.5.5")


def test_private_ranges_blocked():
    assert is_private_ip("10.0.0.1")
    assert is_private_ip("172.16.0.1")
    assert is_private_ip("192.168.1.1")


def test_link_local_and_metadata_blocked():
    assert is_private_ip("169.254.169.254")  # cloud metadata


def test_ipv6_loopback_and_ula_blocked():
    assert is_private_ip("::1")
    assert is_private_ip("[::1]")  # bracketed form
    assert is_private_ip("fc00::1")  # unique local
    assert is_private_ip("fe80::1")  # link-local


def test_blocked_hostnames():
    assert is_private_ip("localhost")
    assert is_private_ip("metadata.google.internal")


def test_empty_host_is_unsafe():
    assert is_private_ip("")


def test_public_ip_allowed():
    assert not is_private_ip("8.8.8.8")
    assert not is_private_ip("1.1.1.1")


# ── validate_url_safe ───────────────────────────────────────────────


def test_scheme_rejected():
    assert validate_url_safe("ftp://example.com") is None
    assert validate_url_safe("file:///etc/passwd") is None
    assert validate_url_safe("javascript:alert(1)") is None


def test_empty_url_rejected():
    assert validate_url_safe("") is None
    assert validate_url_safe("   ") is None


def test_internal_url_rejected():
    assert validate_url_safe("http://127.0.0.1:8080/admin") is None
    assert validate_url_safe("http://169.254.169.254/latest/meta-data/") is None


def test_public_url_allowed():
    assert validate_url_safe("https://8.8.8.8") == "https://8.8.8.8"


# ── is_safe_url (boolean gate used for redirect/child URLs) ─────────


def test_is_safe_url_bool():
    assert is_safe_url("https://8.8.8.8") is True
    assert is_safe_url("http://127.0.0.1") is False
    assert is_safe_url("ftp://x") is False
