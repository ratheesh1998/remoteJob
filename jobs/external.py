"""Links that open LinkedIn's own search in the user's browser. These are never fetched by RemoteApply."""
from urllib.parse import urlencode


def linkedin_remote_search_url(title: str) -> str:
    # f_WT=2 is LinkedIn's "Remote" work-type filter.
    return "https://www.linkedin.com/jobs/search/?" + urlencode({"keywords": title, "f_WT": "2"})


def linkedin_posts_search_url(title: str) -> str:
    """Posts (not job ads) from people hiring; many include an email address. Needs the user's own login."""
    return "https://www.linkedin.com/search/results/content/?" + urlencode({
        "keywords": f"hiring remote {title}", "datePosted": '"past-week"', "sortBy": '"date_posted"'})


def linkedin_links(titles: list[str], limit: int = 3) -> list[dict]:
    return [{"title": t, "url": linkedin_remote_search_url(t), "posts_url": linkedin_posts_search_url(t)}
            for t in titles[:limit] if t.strip()]
