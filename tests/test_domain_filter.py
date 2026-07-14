"""Tests for app.utils.domain_filter (domain allowlist/blocklist — feature 18)."""

from app.utils.domain_filter import is_domain_allowed, merge_domain_rules


def test_block_wins_over_allow():
    assert not is_domain_allowed(
        "https://evil.com/x", allow={"evil.com"}, block={"evil.com"}
    )


def test_subdomain_matches_suffix():
    assert is_domain_allowed(
        "https://blog.example.com", allow={"example.com"}, block=set()
    )
    assert not is_domain_allowed(
        "https://blog.example.com", allow=set(), block={"example.com"}
    )


def test_exact_host_match():
    assert is_domain_allowed("https://example.com", allow={"example.com"}, block=set())


def test_empty_allowlist_allows_all():
    assert is_domain_allowed("https://anything.org", allow=set(), block=set())


def test_allowlist_rejects_non_member():
    assert not is_domain_allowed(
        "https://other.com", allow={"example.com"}, block=set()
    )


def test_lookalike_not_matched():
    # notexample.com must NOT match example.com
    assert not is_domain_allowed(
        "https://notexample.com", allow={"example.com"}, block=set()
    )


def test_deep_subdomain_blocked():
    assert not is_domain_allowed(
        "https://a.b.c.example.com/page", allow=set(), block={"example.com"}
    )


def test_leading_dot_and_case_insensitive():
    assert not is_domain_allowed(
        "https://WWW.Example.COM", allow=set(), block={".example.com"}
    )


def test_invalid_host_rejected():
    # No parseable host -> rejected (empty host is unsafe).
    assert not is_domain_allowed("not a url", allow=set(), block=set())
    assert not is_domain_allowed("", allow=set(), block=set())
    # Note: domain_filter only inspects the host, not the scheme; scheme
    # validation is handled separately by security.validate_url_safe.


def test_merge_rules_normalizes_and_unions():
    merged = merge_domain_rules({"a.com"}, ["B.com", " c.com ", "", "  "])
    assert merged == {"a.com", "b.com", "c.com"}


def test_merge_rules_empty_request():
    assert merge_domain_rules({"a.com"}, []) == {"a.com"}


if __name__ == "__main__":
    import sys, os

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"\nAll {len(fns)} domain_filter checks passed.")
