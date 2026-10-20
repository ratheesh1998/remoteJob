"""Source adapters with mocked HTTP: normalization, remote-only handling, failure isolation."""
from pathlib import Path
from unittest import mock

import requests
from django.test import SimpleTestCase, TestCase, override_settings

from jobs.models import JobSearch, SourceResult
from jobs.sources import ALL_SOURCES, SearchQuery, enabled_sources
from jobs.sources.base import BaseSource

Q = SearchQuery(designation="Python Developer", titles=["Python Developer"], skills=["Python"])


def fake_response(payload=None, content=None):
    r = mock.Mock()
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value=payload)
    r.content = content
    return r


class AdapterTests(SimpleTestCase):
    def test_all_adapters_share_interface(self):
        for src in ALL_SOURCES.values():
            self.assertIsInstance(src, BaseSource)
            self.assertTrue(src.name and src.label)

    def test_optional_sources_off_without_keys(self):
        with override_settings(ADZUNA_APP_ID="", ADZUNA_APP_KEY="", JSEARCH_API_KEY=""):
            names = [s.name for s in enabled_sources()]
        self.assertEqual(names, ["remotive", "remoteok", "himalayas", "weworkremotely", "linkedin"])
        with override_settings(ADZUNA_APP_ID="a", ADZUNA_APP_KEY="b", JSEARCH_API_KEY="k"):
            self.assertEqual(len(enabled_sources()), 7)

    def test_remotive(self):
        payload = {"jobs": [{
            "title": "Python Developer", "company_name": "Acme", "url": "https://remotive.com/j/1",
            "description": "<p>Apply: <a href='mailto:jobs@acme.com'>us</a></p><script>x</script>",
            "candidate_required_location": "Europe", "salary": "$90k", "publication_date": "2026-10-01T10:00:00",
            "tags": ["python"]}]}
        with mock.patch("requests.get", return_value=fake_response(payload)):
            jobs = ALL_SOURCES["remotive"].fetch(Q)
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual((j.title, j.company, j.region, j.source), ("Python Developer", "Acme", "Europe", "remotive"))
        self.assertEqual(j.application_email, "jobs@acme.com")
        self.assertNotIn("<", j.description)
        self.assertIsNotNone(j.posted_at)

    def test_remoteok_skips_legal_notice_and_sets_attribution(self):
        payload = [{"legal": "notice"},
                   {"position": "Backend Dev", "company": "Beta", "url": "https://remoteok.com/1",
                    "description": "<b>Remote</b> role", "location": "", "salary_min": 80000, "salary_max": 120000,
                    "epoch": 1790000000, "tags": ["python"]}]
        with mock.patch("requests.get", return_value=fake_response(payload)):
            jobs = ALL_SOURCES["remoteok"].fetch(Q)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].salary, "$80,000 - $120,000")
        self.assertIn("remoteok.com", ALL_SOURCES["remoteok"].attribution)

    def test_himalayas(self):
        payload = {"jobs": [{"title": "Python Engineer", "companyName": "Gamma", "applicationLink": "https://himalayas.app/j/1",
                             "description": "<p>Great job</p>", "locationRestrictions": ["Portugal", "Spain"],
                             "minSalary": 50000, "maxSalary": 70000, "currency": "EUR", "pubDate": 1790000000,
                             "categories": ["Engineering"]}]}
        with mock.patch("requests.get", return_value=fake_response(payload)):
            jobs = ALL_SOURCES["himalayas"].fetch(Q)
        self.assertEqual(jobs[0].region, "Portugal, Spain")
        self.assertEqual(jobs[0].salary, "€50,000 - €70,000")

    def test_weworkremotely_rss(self):
        rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>Delta Inc: Python Developer</title><link>https://weworkremotely.com/j/1</link>
        <description>&lt;p&gt;Email talent@delta.com&lt;/p&gt;</description><pubDate>Mon, 05 Oct 2026 12:00:00 +0000</pubDate></item>
        </channel></rss>"""
        with mock.patch("requests.get", return_value=fake_response(content=rss)):
            jobs = ALL_SOURCES["weworkremotely"].fetch(Q)
        self.assertEqual((jobs[0].company, jobs[0].title), ("Delta Inc", "Python Developer"))
        self.assertEqual(jobs[0].application_email, "talent@delta.com")

    @override_settings(ADZUNA_APP_ID="a", ADZUNA_APP_KEY="b")
    def test_adzuna_keeps_remote_only(self):
        payload = {"results": [
            {"title": "Python Developer (Remote)", "company": {"display_name": "Eps"}, "redirect_url": "https://adzuna/1",
             "description": "fully remote", "location": {"display_name": "Anywhere"}, "created": "2026-10-01T00:00:00Z"},
            {"title": "Python Developer", "company": {"display_name": "OnSite"}, "redirect_url": "https://adzuna/2",
             "description": "office in Berlin", "location": {"display_name": "Berlin"}}]}
        with mock.patch("requests.get", return_value=fake_response(payload)):
            jobs = ALL_SOURCES["adzuna"].fetch(Q)
        self.assertEqual([j.company for j in jobs], ["Eps"])

    @override_settings(JSEARCH_API_KEY="k")
    def test_jsearch_keeps_remote_only(self):
        payload = {"data": [
            {"job_title": "Py Dev", "employer_name": "Zeta", "job_apply_link": "https://z.com/1", "job_is_remote": True,
             "job_description": "desc", "job_country": "US"},
            {"job_title": "Py Dev", "employer_name": "Onsite", "job_apply_link": "https://o.com/1", "job_is_remote": False}]}
        with mock.patch("requests.get", return_value=fake_response(payload)) as get:
            jobs = ALL_SOURCES["jsearch"].fetch(Q)
        self.assertEqual([j.company for j in jobs], ["Zeta"])
        self.assertEqual(get.call_args.kwargs["headers"]["X-RapidAPI-Key"], "k")

    def test_malformed_entries_skipped(self):
        with mock.patch("requests.get", return_value=fake_response({"jobs": [{"title": "", "company_name": ""}, {}]})):
            self.assertEqual(ALL_SOURCES["remotive"].fetch(Q), [])

    def test_javascript_urls_rejected(self):
        payload = {"jobs": [{"title": "T", "company_name": "C", "url": "javascript:alert(1)", "description": ""}]}
        with mock.patch("requests.get", return_value=fake_response(payload)):
            self.assertEqual(ALL_SOURCES["remotive"].fetch(Q), [])


class TaskIsolationTests(TestCase):
    """A source that raises must be reported as failed while the others still complete the search."""

    def test_failing_source_isolated(self):
        from jobs.tasks import fetch_source
        from jobs.types import JobData

        search = JobSearch.objects.create(designation="Python Developer", titles=["Python Developer"], skills=["Python"])
        SourceResult.objects.create(search=search, name="remotive", label="Remotive")
        SourceResult.objects.create(search=search, name="remoteok", label="RemoteOK")
        ok_job = JobData(title="Python Developer", company="Acme", url="https://a.com/1", source="remotive")
        with mock.patch.object(ALL_SOURCES["remotive"], "fetch", return_value=[ok_job]), \
                mock.patch.object(ALL_SOURCES["remoteok"], "fetch", side_effect=requests.Timeout("slow")):
            fetch_source.apply(args=[search.id, "remotive"])
            result = fetch_source.apply(args=[search.id, "remoteok"])   # eager: retries exhaust, then reports failed
        search.refresh_from_db()
        self.assertEqual(search.status, JobSearch.DONE)
        by_name = {s.name: s for s in search.sources.all()}
        self.assertEqual(by_name["remotive"].status, "ok")
        self.assertEqual(by_name["remoteok"].status, "failed")
        self.assertIn("Timeout", by_name["remoteok"].error)
        self.assertEqual(search.jobs.count(), 1)


FIXTURES = Path(__file__).parent / "fixtures"


def li_resp(text="", status=200):
    r = mock.Mock(status_code=status, text=text)
    r.raise_for_status = mock.Mock()
    return r


@override_settings(LINKEDIN_REQUEST_DELAY=0)
class LinkedInTests(SimpleTestCase):
    """Fixtures are real LinkedIn guest-page HTML, so these tests catch markup drift in the parser."""
    search_html = (FIXTURES / "linkedin_search.html").read_text()
    job_html = (FIXTURES / "linkedin_job.html").read_text()
    src = ALL_SOURCES["linkedin"]

    def test_parse_cards(self):
        cards = self.src.parse_cards(self.search_html)
        self.assertEqual(len(cards), 3)
        c = cards[0]
        self.assertTrue(c["id"].isdigit() and c["title"] and c["company"])
        self.assertNotIn("?", c["url"])                     # tracking string removed
        self.assertTrue(c["url"].startswith("https://"))

    def test_parse_detail(self):
        d = self.src.parse_detail(self.job_html)
        self.assertIn("Python", d["description_html"])

    def test_fetch_is_remote_filtered_and_extracts_email(self):
        def fake(url, **kw):
            if "seeMoreJobPostings" in url:
                return li_resp(self.search_html if kw["params"]["start"] == 0 else "")
            return li_resp(self.job_html)

        with mock.patch.object(self.src, "http_get_raw", side_effect=fake) as get:
            jobs = self.src.fetch(Q)
        first = get.call_args_list[0]
        self.assertEqual(first.kwargs["params"]["f_WT"], "2")           # LinkedIn's remote-only filter
        self.assertEqual(len(jobs), 3)
        self.assertTrue(all(j.source == "linkedin" and "linkedin.com" in j.url for j in jobs))
        self.assertEqual(jobs[0].application_email, "careers@fpt-example.com")
        self.assertNotIn("<", jobs[0].description)

    def test_throttled_first_request_raises_clear_error(self):
        with mock.patch.object(self.src, "http_get_raw", return_value=li_resp(status=429)):
            with self.assertRaises(Exception) as ctx:
                self.src.fetch(Q)
        self.assertIn("throttled", str(ctx.exception))

    def test_throttled_mid_search_returns_partial_results(self):
        calls = {"n": 0}

        def fake(url, **kw):
            calls["n"] += 1
            if "seeMoreJobPostings" in url:
                return li_resp(self.search_html) if calls["n"] == 1 else li_resp(status=429)
            return li_resp(status=429)

        with mock.patch.object(self.src, "http_get_raw", side_effect=fake):
            jobs = self.src.fetch(Q)
        self.assertEqual(len(jobs), 3)                  # cards kept even though details were blocked
        self.assertEqual(jobs[0].description, "")

    def test_one_throttled_detail_is_retried_after_backoff(self):
        state = {"blocked_once": False}

        def fake(url, **kw):
            if "seeMoreJobPostings" in url:
                return li_resp(self.search_html if kw["params"]["start"] == 0 else "")
            if not state["blocked_once"]:
                state["blocked_once"] = True
                return li_resp(status=429)
            return li_resp(self.job_html)

        with mock.patch.object(self.src, "http_get_raw", side_effect=fake):
            jobs = self.src.fetch(Q)
        self.assertEqual(len(jobs), 3)
        self.assertTrue(all(j.description for j in jobs))   # the retry recovered the first description

    def test_disabled_by_setting(self):
        with override_settings(LINKEDIN_ENABLED=False):
            self.assertNotIn("linkedin", [s.name for s in enabled_sources()])
