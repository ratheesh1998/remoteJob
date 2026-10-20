"""Strict email extraction for LinkedIn post text, plus the MX deliverability check."""
import logging
import re

import dns.exception
import dns.resolver

from .text import EMAIL_RE, _FILE_TLDS, _REJECT_DOMAIN, _REJECT_LOCAL

log = logging.getLogger(__name__)

_LINKEDIN_DOMAIN = re.compile(r"(^|\.)(linkedin\.com|licdn\.com|lnkd\.in)$", re.I)
_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.I)
_LOCAL_RE = re.compile(r"^[a-z0-9][a-z0-9._%+-]*$", re.I)
_ELLIPSIS_RE = re.compile(r"^\s{0,2}(\.{2,}|…)")


def valid_syntax(addr: str) -> bool:
    """RFC-ish check that is deliberately stricter than the RFC (no quoted or unusual forms)."""
    if len(addr) > 254 or addr.count("@") != 1:
        return False
    local, domain = addr.split("@")
    if not local or len(local) > 64 or ".." in local or local.endswith(".") or not _LOCAL_RE.match(local):
        return False
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(l) for l in labels):
        return False
    return labels[-1].isalpha() and len(labels[-1]) >= 2


def rejection_reason(addr: str) -> str | None:
    """Why an address must not be used, or None when it is acceptable."""
    if not valid_syntax(addr):
        return "invalid syntax"
    local, _, domain = addr.rpartition("@")
    if _REJECT_LOCAL.match(local):
        return "no-reply or non-hiring mailbox"
    if _LINKEDIN_DOMAIN.search(domain):
        return "linkedin address"
    if _REJECT_DOMAIN.search(domain):
        return "placeholder domain"
    if domain.rsplit(".", 1)[-1].lower() in _FILE_TLDS:
        return "file name, not an email"
    return None


def find_emails(text: str | None) -> list[str]:
    """Every acceptable address in the text, in order, lowercased and de-duplicated.

    An address followed by an ellipsis is rejected: search snippets are cut mid-word, so what precedes
    the ellipsis may be only the start of the real address.
    """
    found: list[str] = []
    for m in EMAIL_RE.finditer(text or ""):
        if _ELLIPSIS_RE.match((text or "")[m.end():m.end() + 4]):
            continue
        addr = m.group(0).rstrip(".").lower()
        if rejection_reason(addr) is None and addr not in found:
            found.append(addr)
    return found


_MX_CACHE: dict[str, bool] = {}


def domain_has_mx(domain: str) -> bool:
    """True when the domain can receive mail. Only a definite 'no' (no such domain, no MX, null MX) is False.

    DNS timeouts and resolver failures are not evidence that the domain is bad, so they keep the address
    (and are not cached) instead of silently dropping real recruiters.
    """
    domain = domain.lower().strip(".")
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=4)
        ok = any(str(r.exchange).rstrip(".") for r in answers)   # a null MX ("0 .") means "accepts no mail"
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        ok = False
    except (dns.exception.DNSException, OSError) as exc:
        log.warning("MX lookup for %s inconclusive (%s); keeping the address", domain, type(exc).__name__)
        return True
    _MX_CACHE[domain] = ok
    return ok


def reset_mx_cache() -> None:
    _MX_CACHE.clear()


def deliverable(addr: str) -> bool:
    return domain_has_mx(addr.rpartition("@")[2])
