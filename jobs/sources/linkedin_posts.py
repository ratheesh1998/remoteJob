"""LinkedIn hiring posts that contain an application email, found ONLY through an official search API.

Nothing here fetches linkedin.com. We read the title, URL and snippet that the API returns, extract emails
from that text with strict rules, and verify each domain can receive mail (MX). Many posts are cut short in
snippets, so only a fraction of results contain an email.
"""
import logging
import time
from urllib.parse import urlsplit

from django.conf import settings

from ..dedupe import post_key
from ..models import SearchSettings
from ..post_emails import deliverable, find_emails
from ..post_parse import clean_result_title, guess_company, guess_title
from ..search_providers import QuotaExhausted, SearchProviderError, any_provider_configured, cached_search, configured_provider
from ..text import parse_date
from ..types import JobData
from .base import BaseSource, SearchQuery

log = logging.getLogger(__name__)

SITE = "site:linkedin.com/posts"
# Variants per title; the run uses as many as the query cap allows, spread across titles.
VARIANTS = [
    SITE + ' ("hiring" OR "we are hiring" OR "opening") "remote" "{t}" ("@gmail.com" OR "send your resume" OR "email your CV")',
    SITE + ' "{t}" "work from home" ("hiring" OR "opening") "@"',
    SITE + ' "{t}" "WFH" "hiring" ("send CV to" OR "email")',
    SITE + ' "{t}" "remote" "send CV to" "@"',
    SITE + ' "we are hiring" "{t}" "remote" ("mail your resume" OR "drop your CV")',
]


def build_queries(titles: list[str], cap: int) -> list[str]:
    """Round-robin the variants across the CV titles so every title is covered before any repeats."""
    titles = [t for t in titles if t.strip()][:3] or []
    queries: list[str] = []
    for v in VARIANTS:
        for t in titles:
            q = v.format(t=t.replace('"', ""))
            if q not in queries:
                queries.append(q)
    return queries[:max(1, cap)]


def is_post_url(url: str) -> bool:
    p = urlsplit(url or "")
    host = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and (host == "linkedin.com" or host.endswith(".linkedin.com")) \
        and ("/posts/" in p.path or "/feed/update/" in p.path)


class LinkedInPostsViaSearch(BaseSource):
    name = "linkedin_posts"
    label = "LinkedIn posts"
    homepage = "https://www.linkedin.com/feed/"
    attribution = ""

    def is_enabled(self) -> bool:
        return any_provider_configured()

    def fetch(self, query: SearchQuery):
        return self.fetch_report(query)[0]

    def fetch_report(self, query: SearchQuery):
        cfg = SearchSettings.load()
        provider = configured_provider(cfg.provider)
        if provider is None:
            raise SearchProviderError("No search API key is set (BRAVE_API_KEY, or GOOGLE_CSE_KEY + GOOGLE_CSE_ID).")
        queries = build_queries(query.titles or [query.designation], cfg.max_queries_per_run)
        jobs: dict[str, JobData] = {}
        failed, errors, live_calls = 0, [], 0
        for q in queries:
            try:
                hits, from_cache = cached_search(provider, q)
            except QuotaExhausted as exc:
                failed += 1
                errors.append(str(exc))
                break                                     # no point spending more calls
            except Exception as exc:                      # one failing query must not break the search
                failed += 1
                errors.append(f"{type(exc).__name__}: {exc}"[:200])
                log.warning("LinkedIn posts query failed: %s", exc)
                continue
            if not from_cache:
                live_calls += 1
                time.sleep(settings.SEARCH_QUERY_DELAY)   # stay inside the providers' per-second limits
            for hit in hits:
                for job in self.jobs_from_hit(hit, query.designation):
                    jobs.setdefault(job.dedupe_key, job)
        if errors and not jobs:    # nothing found AND something went wrong: report it instead of a silent 0
            raise SearchProviderError(errors[0] if errors else "All queries failed.")
        note = f"{failed} of {len(queries)} queries failed" if failed else ""
        return list(jobs.values()), note

    def jobs_from_hit(self, hit, designation: str) -> list[JobData]:
        if not is_post_url(hit.url):
            return []
        text = f"{clean_result_title(hit.title)}\n{hit.snippet}"
        out = []
        for email in find_emails(f"{hit.title}\n{hit.snippet}"):
            if not deliverable(email):
                continue
            out.append(JobData(
                title=(guess_title(text) or designation)[:500], company=(guess_company(text) or "Unknown")[:300],
                url=hit.url[:1000], source=self.name, region="Remote", description=hit.snippet[:2000],
                posted_at=parse_date(hit.posted_at), application_email=email,
                dedupe_key=post_key(email, hit.url),
            ))
        return out
