"""Deduplication. Two jobs are the same if they share a normalized URL, or the same company + title."""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse

from .types import JobData

_TRACKING = re.compile(r"^(utm_|ref$|source$|src$|gclid$|fbclid$)", re.I)
_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|gmbh|corp|co|limited|plc)\b\.?", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def normalize_url(url: str) -> str:
    p = urlparse((url or "").strip())
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)])
    path = p.path.rstrip("/")
    return f"{p.netloc.lower()}{path}{'?' + query if query else ''}"


def job_key(company: str, title: str) -> str:
    """Stable across searches; also the identity used by the one-application-per-recipient-per-job rule."""
    company_norm = _norm(_COMPANY_SUFFIX.sub("", company or ""))
    return hashlib.sha1(f"{company_norm}|{_norm(title)}".encode()).hexdigest()


def post_key(email: str, url: str, text: str = "") -> str:
    """Identity of a LinkedIn post job: the address plus the post URL (or the text when there is no URL)."""
    where = normalize_url(url) or hashlib.sha1((text or "").strip().lower().encode()).hexdigest()[:16]
    return hashlib.sha1(f"post|{(email or '').strip().lower()}|{where}".encode()).hexdigest()


def key_for(job: JobData) -> str:
    return job.dedupe_key or job_key(job.company, job.title)


def dedupe_jobs(jobs: list[JobData]) -> list[JobData]:
    """Keep the first occurrence; fill its gaps from later duplicates (e.g. an email address)."""
    by_key: dict[str, JobData] = {}
    by_url: dict[str, JobData] = {}
    result: list[JobData] = []
    for job in jobs:
        key, url = key_for(job), normalize_url(job.url)
        # Two different posts can share a URL host but never a post URL + email; posts only merge on their own key.
        existing = by_key.get(key) or (by_url.get(url) if url and not job.dedupe_key else None)
        if existing is None:
            result.append(job)
            by_key[key] = job
            if url and not job.dedupe_key:
                by_url[url] = job
            continue
        for attr in ("application_email", "salary", "posted_at", "region"):
            if not getattr(existing, attr) and getattr(job, attr):
                setattr(existing, attr, getattr(job, attr))
        if len(job.description) > len(existing.description):
            existing.description = job.description
    return result
