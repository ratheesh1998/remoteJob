from django.conf import settings

from ..text import format_salary, parse_date
from .base import BaseSource, SearchQuery


class AdzunaSource(BaseSource):
    """Optional: only runs when ADZUNA_APP_ID and ADZUNA_APP_KEY are set."""
    name = "adzuna"
    label = "Adzuna"
    homepage = "https://www.adzuna.com"
    attribution = 'Jobs by <a href="https://www.adzuna.com" target="_blank" rel="noopener">Adzuna</a>'

    def is_enabled(self) -> bool:
        return bool(settings.ADZUNA_APP_ID and settings.ADZUNA_APP_KEY)

    def fetch(self, query: SearchQuery):
        url = f"https://api.adzuna.com/v1/api/jobs/{settings.ADZUNA_COUNTRY}/search/1"
        data = self.http_get(url, params={
            "app_id": settings.ADZUNA_APP_ID, "app_key": settings.ADZUNA_APP_KEY,
            "results_per_page": 50, "what": f"{query.designation} remote", "content-type": "application/json",
        }).json()
        jobs = []
        for j in data.get("results", []):
            location = (j.get("location") or {}).get("display_name", "")
            text = f"{j.get('title', '')} {j.get('description', '')} {location}".lower()
            if "remote" not in text:    # remote jobs only
                continue
            job = self.make_job(
                title=j.get("title"), company=(j.get("company") or {}).get("display_name"),
                url=j.get("redirect_url"), description_html=j.get("description", ""), region=location,
                salary=format_salary(j.get("salary_min"), j.get("salary_max"), "USD" if settings.ADZUNA_COUNTRY == "us" else ""),
                posted_at=parse_date(j.get("created")),
            )
            if job:
                jobs.append(job)
        return jobs
