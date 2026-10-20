from django.test import SimpleTestCase

from jobs.dedupe import dedupe_jobs, job_key, normalize_url
from jobs.types import JobData


def job(title="Python Developer", company="Acme", url="https://acme.com/jobs/1", **kw):
    return JobData(title=title, company=company, url=url, source=kw.pop("source", "remotive"), **kw)


class DedupeTests(SimpleTestCase):
    def test_exact_duplicates_removed(self):
        self.assertEqual(len(dedupe_jobs([job(), job()])), 1)

    def test_same_company_title_different_url(self):
        out = dedupe_jobs([job(url="https://a.com/1"), job(url="https://b.com/2", source="remoteok")])
        self.assertEqual(len(out), 1)

    def test_same_url_different_title_case_and_tracking(self):
        out = dedupe_jobs([job(url="https://acme.com/jobs/1"),
                           job(title="Different", url="https://acme.com/jobs/1/?utm_source=x")])
        self.assertEqual(len(out), 1)

    def test_case_punctuation_and_suffix_insensitive(self):
        a = job(title="Python  Developer!", company="Acme, Inc.")
        b = job(title="python developer", company="ACME", url="https://other.com/x")
        self.assertEqual(len(dedupe_jobs([a, b])), 1)

    def test_different_jobs_kept(self):
        out = dedupe_jobs([job(), job(title="Go Developer", url="https://acme.com/jobs/2"),
                           job(company="Other", url="https://other.com/1")])
        self.assertEqual(len(out), 3)

    def test_merge_fills_missing_email(self):
        out = dedupe_jobs([job(), job(url="https://x.com/2", application_email="jobs@acme.com", salary="$100")])
        self.assertEqual(out[0].application_email, "jobs@acme.com")
        self.assertEqual(out[0].salary, "$100")

    def test_order_preserved(self):
        titles = [j.title for j in dedupe_jobs([job(title="B", url="u1"), job(title="A", url="u2")])]
        self.assertEqual(titles, ["B", "A"])

    def test_key_stable(self):
        self.assertEqual(job_key("Acme Inc", "Python Dev"), job_key("acme", "python dev"))
        self.assertEqual(normalize_url("HTTPS://Acme.com/x/?utm_medium=a&id=3#frag"), "acme.com/x?id=3")
