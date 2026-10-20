"""HTML stripping, application-email extraction and other normalization helpers."""
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

MAX_DESCRIPTION_CHARS = 20000


def html_to_text(raw: str | None) -> str:
    """Strip all markup, keeping paragraph breaks and mailto addresses. Output is plain text."""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "object", "embed"]):
        tag.decompose()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr and addr.lower() not in a.get_text().lower():
                a.append(f" ({addr})")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]):
        block.append("\n")
    text = html.unescape(soup.get_text())
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_DESCRIPTION_CHARS]


EMAIL_RE = re.compile(
    r"(?<![\w.+%-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}(?![\w-])"
)
_REJECT_LOCAL = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|mailer-daemon|postmaster|unsubscribe|abuse|privacy|"
    r"webmaster|bounce|notifications?|legal|dpo|security|press|media|billing|invoices?|compliance|"
    r"\w*accommodations?|accessibility|disabilit\w*|ada|ethics|whistleblower|hello-?world)", re.I)
_REJECT_DOMAIN = re.compile(
    r"(^|\.)(example\.(com|org|net)|domain\.com|yourcompany\.com|email\.com|sentry\.io|wixpress\.com)$", re.I)
_FILE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "pdf"}
_PREFERRED_LOCAL = re.compile(r"^(jobs?|careers?|hr|recruit\w*|apply|applications?|hiring|talent|people|join|work)", re.I)
# Words near an address that say it is NOT for applications (accessibility help, privacy requests, ...).
_NEGATIVE_CONTEXT = re.compile(
    r"accommodat|disabilit|accessib|privacy|data protection|gdpr|unsubscribe|complain|whistle|report (?:a |an )?"
    r"(?:concern|issue|violation|abuse)|press inquir|media inquir|legal notice|opt[- ]out", re.I)
_POSITIVE_CONTEXT = re.compile(
    r"apply|application|resume|r\u00e9sum\u00e9|\bcv\b|cover letter|send (?:us |your )|submit|hiring|recruit|careers?", re.I)
_WINDOW = 120


def extract_application_email(text: str | None) -> str | None:
    """Return the most plausible application email address in the text, else None.

    Addresses are rejected by local part (noreply, accommodations, privacy ...) or when the surrounding
    words say they serve another purpose. Among the rest, hiring-style addresses and addresses next to
    words like "apply" / "send your CV" win; otherwise the first one is used.
    """
    if not text:
        return None
    best, best_rank = None, None
    for index, m in enumerate(EMAIL_RE.finditer(text)):
        addr = m.group(0).rstrip(".").lower()
        local, _, domain = addr.rpartition("@")
        if _REJECT_LOCAL.match(local) or _REJECT_DOMAIN.search(domain):
            continue
        if domain.rsplit(".", 1)[-1] in _FILE_TLDS:
            continue
        window = text[max(0, m.start() - _WINDOW):m.end() + _WINDOW]
        if _NEGATIVE_CONTEXT.search(window) and not _PREFERRED_LOCAL.match(local):
            continue
        lead_in = text[max(0, m.start() - 80):m.start()]    # "...to apply send your CV to <address>"
        rank = (-(2 if _PREFERRED_LOCAL.match(local) else 0) - (1 if _POSITIVE_CONTEXT.search(lead_in) else 0), index)
        if best_rank is None or rank < best_rank:
            best, best_rank = addr, rank
    return best


def parse_date(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = float(value)
            if ts > 1e11:   # milliseconds
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        value = str(value).strip()
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def format_salary(low, high, currency: str = "") -> str:
    def num(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    lo, hi = num(low), num(high)
    if not lo and not hi:
        return ""
    prefix = {"USD": "$", "EUR": "€", "GBP": "£"}.get((currency or "").upper(), "")
    suffix = "" if prefix or not currency else f" {currency.upper()}"
    fmt = lambda v: f"{prefix}{v:,.0f}"
    if lo and hi and lo != hi:
        return f"{fmt(lo)} - {fmt(hi)}{suffix}"
    return f"{fmt(lo or hi)}{suffix}"
