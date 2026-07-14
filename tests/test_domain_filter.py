"""
Self-check for app.utils.domain_filter — run directly: `python tests/test_domain_filter.py`

No test framework; plain asserts. Covers the block>allow precedence,
subdomain suffix matching, empty-allowlist behavior, and invalid hosts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.domain_filter import is_domain_allowed, merge_domain_rules  # noqa: E402


def test_block_wins_over_allow():
    assert not is_domain_allowed(
        "https://evil.com/x", allow={"evil.com"}, block={"evil.com"}
    )


def test_subdomain_matches_suffix():
    assert is_domain_allowed("https://blog.example.com", allow={"example.com"}, block=set())
    assert not is_domain_allowed("https://blog.example.com", allow=set(), block={"example.com"})


def test_empty_allowlist_allows_all():
    assert is_domain_allowed("https://anything.org", allow=set(), block=set())


def test_allowlist_rejects_non_member():
    assert not is_domain_allowed("https://other.com", allow={"example.com"}, block=set())


def test_lookalike_not_matched():
    # notexample.com must NOT match example.com
    assert not is_domain_allowed("https://notexample.com", allow={"example.com"}, block=set())


def test_invalid_host_rejected():
    assert not is_domain_allowed("not a url", allow=set(), block=set())
    assert not is_domain_allowed("", allow=set(), block=set())


def test_merge_rules():
    merged = merge_domain_rules({"a.com"}, ["B.com", " c.com "])
    assert merged == {"a.com", "b.com", "c.com"}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"\nAll {len(fns)} domain_filter checks passed.")
