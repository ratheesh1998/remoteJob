"""LinkedIn public job search (no login): remote-only listings from LinkedIn's guest job pages.

LinkedIn's terms of use prohibit automated access, and it throttles aggressively, so this adapter keeps
the request count low, sleeps between requests, returns partial results when throttled mid-way, and can
be switched off with LINKEDIN_ENABLED=false. Posts (feed content) need a logged-in session and are not read.
"""
import logging
import re
import time

import requests
from bs4 import BeautifulSoup
from django.conf import settings

from ..scoring import title_score
from ..text import parse_date
from .base import BaseSource, SearchQuery

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
PAGE_SIZE = 10            # LinkedIn returns about 10 cards per request on this endpoint
QUERY_TITLES = 2          # designation + first alternate title
PAGES_PER_TITLE = 2
MAX_DETAILS = 20          # descriptions fetched (needed for scoring and for application emails)
BACKOFF_SECONDS = 12      # wait once after a throttle before giving up on the remaining details
URN_RE = re.compile(r"jobPosting:(\d+)")


class LinkedInBlocked(Exception):
    """LinkedIn throttled or refused the request (HTTP 429/999/403)."""


class LinkedInSource(BaseSource):
    name = "linkedin"
    label = "LinkedIn"
    homepage = "https://www.linkedin.com/jobs"
    attribution = 'Jobs from <a href="https://www.linkedin.com/jobs" target="_blank" rel="noopener">LinkedIn</a>'

    def is_enabled(self) -> bool:
        return settings.LINKEDIN_ENABLED

    # -- HTTP -------------------------------------------------------------------------------------
    def _get(self, url: str, **kwargs):
        resp = self.http_get_raw(url, **kwargs)
        if resp.status_code in (429, 999, 403):
            raise LinkedInBlocked(f"LinkedIn throttled the request (HTTP {resp.status_code}). Try again in a few minutes.")
        resp.raise_for_status()
        return resp

    def http_get_raw(self, url: str, **kwargs):
        headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}
        return requests.get(url, headers=headers, timeout=settings.JOB_SOURCE_TIMEOUT, **kwargs)

    def _pause(self):
        time.sleep(settings.LINKEDIN_REQUEST_DELAY)

    # -- parsing ----------------------------------------------------------------------------------
    @staticmethod
    def parse_cards(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        cards = []
        for div in soup.select("div.base-search-card, div.job-search-card"):
            urn = URN_RE.search(div.get("data-entity-urn", ""))
            link = div.select_one("a.base-card__full-link")
            title = div.select_one(".base-search-card__title")
            company = div.select_one(".base-search-card__subtitle")
            if not (urn and link and title and company):
                continue
            location = div.select_one(".job-search-card__location")
            posted = div.select_one("time")
            cards.append({
                "id": urn.group(1),
                "title": title.get_text(" ", strip=True),
                "company": company.get_text(" ", strip=True),
                "location": location.get_text(" ", strip=True) if location else "",
                "posted": (posted.get("datetime") if posted else None),
                # Drop LinkedIn's tracking query string; the path alone identifies the posting.
                "url": link["href"].split("?")[0],
            })
        return cards

    @staticmethod
    def parse_detail(html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        desc = soup.select_one(".show-more-less-html__markup")
        salary = soup.select_one(".compensation__salary")
        return {"description_html": desc.decode_contents() if desc else "",
                "salary": salary.get_text(" ", strip=True) if salary else ""}

    # -- fetch ------------------------------------------------------------------------------------
    def fetch(self, query: SearchQuery):
        titles = (query.titles or [query.designation])[:QUERY_TITLES]
        cards: dict[str, dict] = {}
        first_request = True
        for title in titles:
            for page in range(PAGES_PER_TITLE):
                if not first_request:
                    self._pause()
                first_request = False
                try:
                    resp = self._get(SEARCH_URL, params={
                        "keywords": title, "location": "Worldwide", "f_WT": "2", "start": page * PAGE_SIZE})
                except LinkedInBlocked:
                    if cards:     # keep what we already have
                        log.warning("LinkedIn throttled mid-search; returning %d partial results", len(cards))
                        return self._build(cards, query)
                    raise
                page_cards = self.parse_cards(resp.text)
                for c in page_cards:
                    cards.setdefault(c["id"], c)
                if len(page_cards) < PAGE_SIZE:
                    break
        return self._build(cards, query)

    def _build(self, cards: dict[str, dict], query: SearchQuery):
        titles = query.titles or [query.designation]
        ranked = sorted(cards.values(), key=lambda c: -title_score(c["title"], titles[0], titles[1:]))
        jobs = []
        blocked = False
        backed_off = False
        for i, c in enumerate(ranked):
            detail = {"description_html": "", "salary": ""}
            if i < MAX_DETAILS and not blocked:
                self._pause()
                for attempt in (1, 2):
                    try:
                        detail = self.parse_detail(self._get(DETAIL_URL.format(id=c["id"])).text)
                        break
                    except LinkedInBlocked:
                        if backed_off or attempt == 2:
                            blocked = True      # stop fetching details, keep the cards we have
                            log.warning("LinkedIn throttled while reading job details")
                            break
                        backed_off = True       # one patient retry, then give up for this search
                        time.sleep(BACKOFF_SECONDS * (settings.LINKEDIN_REQUEST_DELAY > 0))
                    except Exception as exc:   # one broken detail page must not drop the job
                        log.warning("LinkedIn detail %s failed: %s", c["id"], exc)
                        break
            job = self.make_job(
                title=c["title"], company=c["company"], url=c["url"], description_html=detail["description_html"],
                region=c["location"] or "Remote", salary=detail["salary"], posted_at=parse_date(c["posted"]),
            )
            if job:
                jobs.append(job)
        return jobs
