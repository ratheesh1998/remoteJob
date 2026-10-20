from ..text import format_salary, parse_date
from .base import BaseSource, SearchQuery


class RemoteOKSource(BaseSource):
    name = "remoteok"
    label = "RemoteOK"
    homepage = "https://remoteok.com"
    # RemoteOK's terms require a visible link back to them and crediting them as the source.
    attribution = 'Remote jobs from <a href="https://remoteok.com" target="_blank" rel="noopener">Remote OK</a>'
    url = "https://remoteok.com/api"

    def fetch(self, query: SearchQuery):
        data = self.http_get(self.url).json()
        jobs = []
        for j in data:
            if not isinstance(j, dict) or not j.get("position"):   # first element is a legal notice
                continue
            job = self.make_job(
                title=j.get("position"), company=j.get("company"),
                url=j.get("url") or j.get("apply_url"), description_html=j.get("description", ""),
                region=j.get("location"), salary=format_salary(j.get("salary_min"), j.get("salary_max"), "USD"),
                posted_at=parse_date(j.get("epoch") or j.get("date")), tags=j.get("tags"),
            )
            if job:
                jobs.append(job)
        return jobs
