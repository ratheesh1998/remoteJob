from ..text import parse_date
from .base import BaseSource, SearchQuery


class RemotiveSource(BaseSource):
    name = "remotive"
    label = "Remotive"
    homepage = "https://remotive.com"
    attribution = "Jobs via Remotive"
    url = "https://remotive.com/api/remote-jobs"

    def fetch(self, query: SearchQuery):
        # Remotive asks API users to keep request volume low: one request for the designation,
        # a second one for the first alternate title only if the first returned nothing.
        raw = []
        for term in [query.designation, *query.titles[1:2]]:
            data = self.http_get(self.url, params={"search": term, "limit": 100}).json()
            raw = data.get("jobs", [])
            if raw:
                break
        jobs = []
        for j in raw:
            job = self.make_job(
                title=j.get("title"), company=j.get("company_name"), url=j.get("url"),
                description_html=j.get("description", ""), region=j.get("candidate_required_location"),
                salary=j.get("salary"), posted_at=parse_date(j.get("publication_date")), tags=j.get("tags"),
            )
            if job:
                jobs.append(job)
        return jobs
