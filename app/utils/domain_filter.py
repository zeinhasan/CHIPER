"""
Domain Allowlist / Blocklist Filtering

Decides whether a URL's host may be scraped, combining global (env) rules with
optional per-request rules. Matching is suffix-based so ``example.com`` also
covers ``www.example.com`` and ``blog.example.com``.

Rule precedence: block wins over allow.
    1. host matches blocklist            -> REJECT
    2. allowlist non-empty and no match  -> REJECT
    3. otherwise                         -> ACCEPT
"""

from urllib.parse import urlparse

from app.utils.helpers import get_logger

logger = get_logger(__name__)


def _host_of(url: str) -> str:
    """Return the lowercase hostname of *url*, or '' if unparseable."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _matches(host: str, domains: set[str]) -> bool:
    """True if *host* equals or is a subdomain of any entry in *domains*."""
    for d in domains:
        d = d.strip().lower().lstrip(".")
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False


def merge_domain_rules(global_rules: set[str], request_rules: list[str]) -> set[str]:
    """Union of global (env) and per-request domain rules."""
    merged = set(global_rules)
    merged.update(d.strip().lower() for d in request_rules if d and d.strip())
    return merged


def is_domain_allowed(url: str, allow: set[str], block: set[str]) -> bool:
    """
    Return True if *url*'s host passes the block/allow rules.

    ``block`` wins over ``allow``.  An empty ``allow`` set means "allow all"
    (subject to the blocklist).  An unparseable/empty host is rejected.
    """
    host = _host_of(url)
    if not host:
        return False

    if _matches(host, block):
        return False

    if allow and not _matches(host, allow):
        return False

    return True
