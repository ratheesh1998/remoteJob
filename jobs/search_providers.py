"""Official search APIs used to discover LinkedIn posts. This module never contacts linkedin.com."""
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .models import SearchCache, SearchSettings
from .text import html_to_text, parse_date

log = logging.getLogger(__name__)


class SearchProviderError(Exception):
    pass


class QuotaExhausted(SearchProviderError):
    pass


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    posted_at: str | None = None     # ISO string so hits are JSON-cacheable

    def to_dict(self) -> dict:
        return asdict(self)


def _record_quota(remaining: int | None, limit: int | None) -> None:
    cfg = SearchSettings.load()
    cfg.quota_remaining, cfg.quota_limit, cfg.quota_checked_at = remaining, limit, timezone.now()
    cfg.save(update_fields=["quota_remaining", "quota_limit", "quota_checked_at"])


def _last_int(header: str | None) -> int | None:
    """Brave reports 'per-second, per-month' pairs such as '1, 1999'; the last number is the monthly one."""
    if not header:
        return None
    try:
        return int(header.split(",")[-1].strip())
    except ValueError:
        return None


class BaseProvider(ABC):
    name = ""
    label = ""
    freshness_label = ""

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def search(self, query: str) -> list[SearchHit]: ...

    def key_hint(self) -> str:
        return ""


class BraveProvider(BaseProvider):
    name, label = "brave", "Brave Search API"

    def configured(self) -> bool:
        return bool(settings.BRAVE_API_KEY)

    def key_hint(self) -> str:
        return "Set BRAVE_API_KEY in .env (https://brave.com/search/api/)."

    def search(self, query: str) -> list[SearchHit]:
        resp = requests.get(
            settings.BRAVE_API_URL,
            params={"q": query, "count": 20, "freshness": "pm"},       # pm = the last ~30 days
            headers={"X-Subscription-Token": settings.BRAVE_API_KEY, "Accept": "application/json"},
            timeout=settings.JOB_SOURCE_TIMEOUT,
        )
        _record_quota(_last_int(resp.headers.get("X-RateLimit-Remaining")), _last_int(resp.headers.get("X-RateLimit-Limit")))
        if resp.status_code == 429:
            raise QuotaExhausted("Brave rate limit or monthly quota reached (HTTP 429).")
        if resp.status_code in (401, 403):
            raise SearchProviderError(f"Brave rejected the API key (HTTP {resp.status_code}).")
        resp.raise_for_status()
        hits = []
        for r in (resp.json().get("web") or {}).get("results", []):
            hits.append(SearchHit(
                title=html_to_text(r.get("title")), url=r.get("url") or "",
                snippet=html_to_text(" ".join([r.get("description") or "", *(r.get("extra_snippets") or [])])),
                posted_at=_iso(parse_date(r.get("page_age"))),
            ))
        return hits


class GoogleProvider(BaseProvider):
    name, label = "google", "Google Programmable Search"

    def configured(self) -> bool:
        return bool(settings.GOOGLE_CSE_KEY and settings.GOOGLE_CSE_ID)

    def key_hint(self) -> str:
        return "Set GOOGLE_CSE_KEY and GOOGLE_CSE_ID in .env. Note: this API is closed to new customers."

    def search(self, query: str) -> list[SearchHit]:
        cfg = SearchSettings.load()
        today = timezone.localdate()
        if cfg.google_day != today:
            cfg.google_day, cfg.google_used_today = today, 0
        if cfg.google_used_today >= settings.GOOGLE_CSE_DAILY_QUOTA:
            raise QuotaExhausted(f"Google daily quota of {settings.GOOGLE_CSE_DAILY_QUOTA} queries reached.")
        resp = requests.get(
            settings.GOOGLE_CSE_URL,
            params={"key": settings.GOOGLE_CSE_KEY, "cx": settings.GOOGLE_CSE_ID, "q": query, "num": 10,
                    "dateRestrict": "d30"},
            timeout=settings.JOB_SOURCE_TIMEOUT,
        )
        cfg.google_used_today += 1       # every request counts against the quota, successful or not
        cfg.quota_limit = settings.GOOGLE_CSE_DAILY_QUOTA
        cfg.quota_remaining = max(0, settings.GOOGLE_CSE_DAILY_QUOTA - cfg.google_used_today)
        cfg.quota_checked_at = timezone.now()
        cfg.save()
        if resp.status_code == 429:
            raise QuotaExhausted("Google quota reached (HTTP 429).")
        if resp.status_code in (400, 401, 403):
            msg = (resp.json().get("error") or {}).get("message", "") if resp.content else ""
            raise SearchProviderError(f"Google rejected the request (HTTP {resp.status_code}): {msg}"[:300])
        resp.raise_for_status()
        return [SearchHit(title=html_to_text(i.get("title")), url=i.get("link") or "",
                          snippet=html_to_text(i.get("snippet")), posted_at=None)
                for i in resp.json().get("items", [])]


PROVIDERS: dict[str, BaseProvider] = {p.name: p for p in (BraveProvider(), GoogleProvider())}


def configured_provider(preferred: str) -> BaseProvider | None:
    """The preferred provider when it has keys, else any other configured one."""
    chosen = PROVIDERS.get(preferred)
    if chosen and chosen.configured():
        return chosen
    return next((p for p in PROVIDERS.values() if p.configured()), None)


def any_provider_configured() -> bool:
    return any(p.configured() for p in PROVIDERS.values())


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _cache_key(provider: BaseProvider, query: str) -> str:
    return hashlib.sha256(f"{provider.name}|{query}".encode()).hexdigest()


def cached_search(provider: BaseProvider, query: str) -> tuple[list[SearchHit], bool]:
    """(hits, served_from_cache). Results are cached for SEARCH_CACHE_HOURS so repeats cost no quota."""
    cutoff = timezone.now() - timedelta(hours=settings.SEARCH_CACHE_HOURS)
    SearchCache.objects.filter(created_at__lt=cutoff).delete()
    key = _cache_key(provider, query)
    row = SearchCache.objects.filter(query_hash=key).first()
    if row:
        return [SearchHit(**h) for h in row.results], True
    hits = provider.search(query)
    SearchCache.objects.update_or_create(
        query_hash=key, defaults={"provider": provider.name, "query": query,
                                  "results": [h.to_dict() for h in hits], "created_at": timezone.now()})
    return hits, False
