from ..text import format_salary, parse_date
from .base import BaseSource, SearchQuery


class HimalayasSource(BaseSource):
    name = "himalayas"
    label = "Himalayas"
    homepage = "https://himalayas.app"
    attribution = 'Jobs via <a href="https://himalayas.app" target="_blank" rel="noopener">Himalayas</a>'
    url = "https://himalayas.app/jobs/api/search"

    def fetch(self, query: SearchQuery):
        data = self.http_get(self.url, params={"q": query.designation, "sort": "relevant"}).json()
        jobs = []
        for j in data.get("jobs", []):
            locations = j.get("locationRestrictions") or []
            region = ", ".join(str(x) for x in locations) if isinstance(locations, list) else str(locations)
            job = self.make_job(
                title=j.get("title"), company=j.get("companyName"),
                url=j.get("applicationLink") or j.get("guid"),
                description_html=j.get("description") or j.get("excerpt", ""),
                region=region, salary=format_salary(j.get("minSalary"), j.get("maxSalary"), j.get("currency", "")),
                posted_at=parse_date(j.get("pubDate")),
                tags=(j.get("categories") or []) + (j.get("parentCategories") or []),
            )
            if job:
                jobs.append(job)
        return jobs
