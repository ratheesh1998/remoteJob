from django.conf import settings

from ..text import format_salary, parse_date
from .base import BaseSource, SearchQuery


class JSearchSource(BaseSource):
    """Optional: only runs when JSEARCH_API_KEY (a RapidAPI key) is set."""
    name = "jsearch"
    label = "JSearch"
    homepage = "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch"
    attribution = "Jobs via JSearch (RapidAPI)"
    url = "https://jsearch.p.rapidapi.com/search"

    def is_enabled(self) -> bool:
        return bool(settings.JSEARCH_API_KEY)

    def fetch(self, query: SearchQuery):
        data = self.http_get(
            self.url,
            params={"query": f"{query.designation} remote", "remote_jobs_only": "true", "num_pages": 1},
            headers={"X-RapidAPI-Key": settings.JSEARCH_API_KEY, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        ).json()
        jobs = []
        for j in data.get("data", []):
            if not j.get("job_is_remote"):
                continue
            region = ", ".join(x for x in (j.get("job_city"), j.get("job_country")) if x)
            job = self.make_job(
                title=j.get("job_title"), company=j.get("employer_name"), url=j.get("job_apply_link"),
                description_html=j.get("job_description", ""), region=region,
                salary=format_salary(j.get("job_min_salary"), j.get("job_max_salary"), j.get("job_salary_currency") or ""),
                posted_at=parse_date(j.get("job_posted_at_datetime_utc")),
            )
            if job:
                jobs.append(job)
        return jobs
