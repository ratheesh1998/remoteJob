from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests
from django.conf import settings

from ..text import extract_application_email, html_to_text
from ..types import JobData

USER_AGENT = "RemoteApply/1.0 (personal job search tool)"


@dataclass
class SearchQuery:
    designation: str
    titles: list[str] = field(default_factory=list)   # designation + alternates
    skills: list[str] = field(default_factory=list)


class BaseSource(ABC):
    """Common interface for every job source. fetch() returns normalized, remote-only jobs."""
    name: str = ""
    label: str = ""
    homepage: str = ""
    attribution: str = ""   # shown in the UI where the source's terms require it

    def is_enabled(self) -> bool:
        return True

    @abstractmethod
    def fetch(self, query: SearchQuery) -> list[JobData]:
        ...

    def fetch_report(self, query: SearchQuery):
        """(jobs, note). The note is shown next to the source status, e.g. '2 of 6 queries failed'."""
        return self.fetch(query), ""

    def http_get(self, url: str, **kwargs) -> requests.Response:
        headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
        resp = requests.get(url, headers=headers, timeout=settings.JOB_SOURCE_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp

    def make_job(self, *, title, company, url, description_html="", region="", salary="",
                 posted_at=None, tags=None) -> JobData | None:
        title = " ".join(str(title or "").split())
        company = " ".join(str(company or "").split())
        url = str(url or "").strip()
        if not (title and company and url.startswith(("http://", "https://"))):
            return None
        description = html_to_text(description_html)
        return JobData(
            title=title[:500], company=company[:300], url=url[:1000], source=self.name,
            region=" ".join(str(region or "").split())[:300] or "Worldwide",
            description=description, salary=str(salary or "")[:200], posted_at=posted_at,
            application_email=extract_application_email(description),
            tags=[str(t) for t in (tags or []) if t][:20],
        )
