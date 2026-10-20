"""Links that open another site's own search. We never fetch or scrape these sites."""
from urllib.parse import urlencode


def linkedin_remote_search_url(title: str) -> str:
    # f_WT=2 is LinkedIn's "Remote" work-type filter.
    return "https://www.linkedin.com/jobs/search/?" + urlencode({"keywords": title, "f_WT": "2"})


def linkedin_links(titles: list[str], limit: int = 3) -> list[dict]:
    return [{"title": t, "url": linkedin_remote_search_url(t)} for t in titles[:limit] if t.strip()]
