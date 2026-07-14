"""
URL Security Utilities

Functions to validate URLs before fetching, preventing Server-Side Request
Forgery (SSRF) by blocking requests to internal/private IP addresses.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from app.utils.helpers import get_logger

logger = get_logger(__name__)

# ── IP ranges that should NEVER be accessed ────────────────────────

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4 loopback
    ipaddress.IPv4Network("127.0.0.0/8"),
    # IPv6 loopback
    ipaddress.IPv6Network("::1/128"),
    # IPv4 private (RFC 1918)
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    # IPv6 private (RFC 4193 — Unique Local Addresses)
    ipaddress.IPv6Network("fc00::/7"),
    # IPv6 link-local
    ipaddress.IPv6Network("fe80::/10"),
    # IPv4 link-local (APIPA) + AWS metadata
    ipaddress.IPv4Network("169.254.0.0/16"),
    # IPv4 multicast
    ipaddress.IPv4Network("224.0.0.0/4"),
    # IPv6 multicast
    ipaddress.IPv6Network("ff00::/8"),
    # Other special-use / documentation
    ipaddress.IPv4Network("0.0.0.0/8"),  # "This host on this network"
    ipaddress.IPv4Network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.IPv4Network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.IPv4Network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.IPv4Network("100.64.0.0/10"),  # Carrier-grade NAT
    ipaddress.IPv4Network("198.18.0.0/15"),  # Benchmarking
]

# Hostnames that resolve to local/internal (defense-in-depth)
_BLOCKED_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
        "metadata.google.internal",  # GCP metadata
    }
)


def _resolve_ips(hostname: str) -> list[str]:
    """Resolve a hostname to all its IPv4 and IPv6 addresses.

    Uses ``AF_UNSPEC`` so a host that only publishes an AAAA record
    (IPv6) is still checked — otherwise an IPv6-only host pointing at
    ``::1`` or a private ULA would bypass the SSRF gate.
    """
    try:
        info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        return [entry[4][0] for entry in info]
    except (socket.gaierror, OSError):
        return []


def is_private_ip(hostname: str) -> bool:
    """
    Return True if *hostname* resolves to a blocked IP or matches a blocked host.

    This is the main SSRF gate.  Call before making any outbound request
    with a user-supplied URL.
    """
    if not hostname:
        return True  # empty = unsafe

    hostname = hostname.strip("[]")  # strip IPv6 brackets

    # ── Check literal hostname blacklist ─────────────────────────
    if hostname.lower() in _BLOCKED_HOSTS:
        return True

    # ── Try parsing as literal IP ─────────────────────────────────
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        pass  # Not a literal IP, need DNS resolution

    # ── DNS resolution check (IPv4 + IPv6) ────────────────────────
    ips = _resolve_ips(hostname)
    if not ips:
        logger.debug("Cannot resolve hostname for SSRF check", extra={"host": hostname})
        return False  # Can't resolve, let the request proceed (it'll fail anyway)

    for ip_str in ips:
        # IPv6 scoped addresses (fe80::1%eth0) — drop the zone id.
        ip_str = ip_str.split("%", 1)[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if any(addr in net for net in _BLOCKED_NETWORKS):
            logger.warning(
                "Blocked internal IP via DNS",
                extra={"hostname": hostname, "resolved_ip": ip_str},
            )
            return True

    return False


def validate_url_safe(url: str) -> str | None:
    """
    Validate that a URL is safe to fetch (no SSRF).

    Returns the normalized URL if safe, or None if blocked/invalid.
    """
    if not url or not url.strip():
        return None

    url = url.strip()
    parsed = urlparse(url)

    # ── Scheme check ─────────────────────────────────────────────
    if parsed.scheme not in ("http", "https"):
        logger.warning("Blocked non-HTTP scheme", extra={"url": url[:200]})
        return None

    # ── Hostname/IP check ───────────────────────────────
    hostname = parsed.hostname or ""
    if is_private_ip(hostname):
        logger.warning("Blocked internal URL (SSRF)", extra={"url": url[:200]})
        return None

    return url


def is_safe_url(url: str) -> bool:
    """Boolean SSRF gate for internally-derived URLs.

    Same checks as :func:`validate_url_safe` but returns a bool, for use
    on URLs discovered mid-flight (crawl children, post-redirect targets,
    sitemap entries) where a raise/return-None distinction is unneeded.
    """
    return validate_url_safe(url) is not None
