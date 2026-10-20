import feedparser

from ..text import parse_date
from .base import BaseSource, SearchQuery


class WeWorkRemotelySource(BaseSource):
    name = "weworkremotely"
    label = "We Work Remotely"
    homepage = "https://weworkremotely.com"
    attribution = 'Jobs via <a href="https://weworkremotely.com" target="_blank" rel="noopener">We Work Remotely</a>'
    url = "https://weworkremotely.com/remote-jobs.rss"

    def fetch(self, query: SearchQuery):
        resp = self.http_get(self.url)
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            raise ValueError("The We Work Remotely feed could not be parsed.")
        jobs = []
        for e in feed.entries:
            company, sep, title = (e.get("title") or "").partition(": ")
            if not sep:
                company, title = e.get("author") or "", e.get("title", "")
            job = self.make_job(
                title=title, company=company, url=e.get("link"), description_html=e.get("summary", ""),
                region=e.get("region") or "Anywhere in the World", posted_at=parse_date(e.get("published")),
            )
            if job:
                jobs.append(job)
        return jobs
