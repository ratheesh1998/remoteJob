"""Feature 1: LinkedIn posts via an official search API (all HTTP and DNS mocked)."""
from datetime import timedelta
from unittest import mock

import dns.resolver
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from jobs import post_emails, search_providers
from jobs.dedupe import dedupe_jobs, post_key
from jobs.models import SearchCache, SearchSettings
from jobs.post_emails import find_emails, rejection_reason, valid_syntax
from jobs.post_parse import clean_result_title, guess_company, guess_title
from jobs.search_providers import QuotaExhausted, SearchHit
from jobs.sources import SearchQuery
from jobs.sources.linkedin_posts import LinkedInPostsViaSearch, build_queries, is_post_url
from jobs.types import JobData


class EmailRulesTests(SimpleTestCase):
    def test_finds_plain_and_multiple_emails(self):
        self.assertEqual(find_emails("Send CV to hr@acme.io or Talent@Acme.IO, thanks"), ["hr@acme.io", "talent@acme.io"])

    def test_rejections(self):
        for bad in ("noreply@acme.com", "no-reply@acme.com", "donotreply@acme.com", "x@linkedin.com",
                    "x@mail.linkedin.com", "logo@2x.png", "user@example.com"):
            self.assertIsNotNone(rejection_reason(bad), bad)
        self.assertEqual(find_emails("noreply@acme.com logo@2x.png me@linkedin.com"), [])

    def test_syntax_check(self):
        for bad in ("a..b@x.com", ".a@x.com", "a.@x.com", "a@x", "a@-x.com", "a@x.c", "a@@x.com", "a b@x.com", ("a" * 65) + "@x.com"):
            self.assertFalse(valid_syntax(bad), bad)
        self.assertTrue(valid_syntax("first.last+tag@sub.acme.co.uk"))

    def test_truncated_snippet_email_rejected(self):
        self.assertEqual(find_emails("send your CV to hr@company.co... for more"), [])
        self.assertEqual(find_emails("send your CV to hr@company.co…"), [])
        self.assertEqual(find_emails("send your CV to hr@company.com. Thanks..."), ["hr@company.com"])

    def test_empty(self):
        self.assertEqual(find_emails(None), [])


class MXTests(SimpleTestCase):
    def setUp(self):
        post_emails.reset_mx_cache()

    def answer(self, exchange):
        return [mock.Mock(exchange=exchange)]

    def test_has_mx(self):
        with mock.patch("dns.resolver.resolve", return_value=self.answer("mx.acme.com.")):
            self.assertTrue(post_emails.deliverable("hr@acme.com"))

    def test_nxdomain_and_no_mx_dropped(self):
        for exc in (dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()):
            post_emails.reset_mx_cache()
            with mock.patch("dns.resolver.resolve", side_effect=exc):
                self.assertFalse(post_emails.deliverable("hr@nope.example"))

    def test_null_mx_dropped(self):
        with mock.patch("dns.resolver.resolve", return_value=self.answer(".")):
            self.assertFalse(post_emails.deliverable("hr@nomail.test"))

    def test_dns_timeout_keeps_address_and_is_not_cached(self):
        import dns.exception
        with mock.patch("dns.resolver.resolve", side_effect=dns.exception.Timeout()):
            self.assertTrue(post_emails.deliverable("hr@slow.test"))
        with mock.patch("dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN()):
            self.assertFalse(post_emails.deliverable("hr@slow.test"))     # not remembered as good

    def test_cached_per_domain(self):
        with mock.patch("dns.resolver.resolve", return_value=self.answer("mx.a.com.")) as r:
            post_emails.deliverable("a@a.com"); post_emails.deliverable("b@a.com")
        self.assertEqual(r.call_count, 1)


class ParseTests(SimpleTestCase):
    def test_title_and_company(self):
        t = "Acme Robotics is hiring a Senior Python Developer (Remote). Send your CV to jobs@acme.com"
        self.assertEqual(guess_title(t), "Senior Python Developer")
        self.assertEqual(guess_company(t), "Acme Robotics")

    def test_unsure_is_none(self):
        self.assertIsNone(guess_title("great weather today, we are hiring soon"))
        self.assertIsNone(guess_company("we are hiring! mail hr@x.com"))

    def test_result_title_cleanup(self):
        self.assertEqual(clean_result_title("Jane Doe on LinkedIn: We are hiring a Python Developer | LinkedIn"),
                         "We are hiring a Python Developer")


class QueryTests(SimpleTestCase):
    def test_variants_cover_titles_and_respect_cap(self):
        qs = build_queries(["Python Developer", "Backend Engineer"], 4)
        self.assertEqual(len(qs), 4)
        self.assertTrue(all(q.startswith("site:linkedin.com/posts") for q in qs))
        self.assertIn('"Python Developer"', qs[0]); self.assertIn('"Backend Engineer"', qs[1])
        self.assertEqual(len(build_queries(["A B"], 99)), 5)             # 5 variants per title
        self.assertEqual(len(build_queries(["A B"], 0)), 1)

    def test_post_url_filter(self):
        self.assertTrue(is_post_url("https://www.linkedin.com/posts/jane-doe_hiring-activity-123"))
        self.assertTrue(is_post_url("https://in.linkedin.com/feed/update/urn:li:activity:1/"))
        for bad in ("https://www.linkedin.com/in/jane", "https://evil.com/posts/x", "https://www.linkedin.com.evil.com/posts/x", "javascript:alert(1)"):
            self.assertFalse(is_post_url(bad), bad)


def hit(text, url="https://www.linkedin.com/posts/jane_hiring-1", title="Jane on LinkedIn: hiring"):
    return SearchHit(title=title, url=url, snippet=text)


@override_settings(BRAVE_API_KEY="k", SEARCH_QUERY_DELAY=0)
class AdapterTests(TestCase):
    q = SearchQuery(designation="Python Developer", titles=["Python Developer"], skills=["Python"])

    def setUp(self):
        post_emails.reset_mx_cache()
        patcher = mock.patch("dns.resolver.resolve", side_effect=lambda d, *a, **k: [mock.Mock(exchange="mx.x.")]
                             if d != "nomx.test" else (_ for _ in ()).throw(dns.resolver.NXDOMAIN()))
        patcher.start(); self.addCleanup(patcher.stop)


    def test_extracts_filters_and_normalizes(self):
        hits = [
            hit("Acme Robotics is hiring a Senior Python Developer. Remote. Send CV to jobs@acme.com"),
            hit("Please email noreply@acme.com", url="https://www.linkedin.com/posts/x_2"),          # rejected address
            hit("send CV to hr@nomx.test", url="https://www.linkedin.com/posts/x_3"),                  # domain without MX
            hit("no email in this one", url="https://www.linkedin.com/posts/x_4"),                      # no email at all
            hit("mail jobs@acme.com", url="https://www.linkedin.com/in/someone"),                       # not a post URL
        ]
        with mock.patch.object(search_providers.BraveProvider, "search", return_value=hits):
            jobs, note = LinkedInPostsViaSearch().fetch_report(self.q)
        self.assertEqual([j.application_email for j in jobs], ["jobs@acme.com"])
        j = jobs[0]
        self.assertEqual((j.source, j.title, j.company, j.url), ("linkedin_posts", "Senior Python Developer", "Acme Robotics",
                                                              "https://www.linkedin.com/posts/jane_hiring-1"))
        self.assertIn("jobs@acme.com", j.description)
        self.assertEqual(note, "")

    def test_fallback_title_and_company(self):
        with mock.patch.object(search_providers.BraveProvider, "search", return_value=[hit("Hiring! mail me: hr@acme.com")]):
            jobs, _ = LinkedInPostsViaSearch().fetch_report(self.q)
        self.assertEqual((jobs[0].title, jobs[0].company), ("Python Developer", "Unknown"))

    def test_dedupe_on_email_plus_url_across_queries(self):
        same = hit("send CV to hr@acme.com")
        with mock.patch.object(search_providers.BraveProvider, "search", return_value=[same]):
            jobs, _ = LinkedInPostsViaSearch().fetch_report(self.q)     # up to 5 queries return the same hit
        self.assertEqual(len(jobs), 1)
        other = hit("send CV to hr@acme.com", url="https://www.linkedin.com/posts/other_9")
        self.assertEqual(len(dedupe_jobs([jobs[0], JobData(title="T", company="Unknown", url=other.url, source="linkedin_posts",
                                                          application_email="hr@acme.com", dedupe_key=post_key("hr@acme.com", other.url))])), 2)
        self.assertEqual(post_key("HR@acme.com", "https://www.linkedin.com/posts/a?utm_source=x"),
                         post_key("hr@acme.com", "https://www.linkedin.com/posts/a"))

    def test_one_failing_query_does_not_break_the_search(self):
        results = [RuntimeError("boom"), [hit("send CV to hr@acme.com")], [hit("send CV to hr@acme.com")]]
        def fake(self_, q):
            r = results.pop(0) if results else []
            if isinstance(r, Exception):
                raise r
            return r
        with override_settings(SEARCH_MAX_QUERIES=3):
            SearchSettings.load().__class__.objects.filter(pk=1).delete()
            with mock.patch.object(search_providers.BraveProvider, "search", fake):
                jobs, note = LinkedInPostsViaSearch().fetch_report(self.q)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(note, "1 of 3 queries failed")

    def test_all_queries_failing_reports_failure(self):
        with mock.patch.object(search_providers.BraveProvider, "search", side_effect=RuntimeError("down")):
            with self.assertRaises(Exception) as ctx:
                LinkedInPostsViaSearch().fetch_report(self.q)
        self.assertIn("down", str(ctx.exception))

    def test_quota_exhausted_stops_further_queries(self):
        with mock.patch.object(search_providers.BraveProvider, "search", side_effect=QuotaExhausted("monthly quota")) as m:
            with self.assertRaises(Exception):
                LinkedInPostsViaSearch().fetch_report(self.q)
        self.assertEqual(m.call_count, 1)

    def test_no_key_means_disabled_and_clear_error(self):
        with override_settings(BRAVE_API_KEY="", GOOGLE_CSE_KEY="", GOOGLE_CSE_ID=""):
            self.assertFalse(LinkedInPostsViaSearch().is_enabled())
            with self.assertRaises(Exception) as ctx:
                LinkedInPostsViaSearch().fetch_report(self.q)
            self.assertIn("API key", str(ctx.exception))

    def test_cache_prevents_second_api_call_and_expires(self):
        with mock.patch.object(search_providers.BraveProvider, "search", return_value=[hit("hr@acme.com")]) as m:
            LinkedInPostsViaSearch().fetch_report(self.q)
            first = m.call_count
            LinkedInPostsViaSearch().fetch_report(self.q)
            self.assertEqual(m.call_count, first)                       # second run fully served from cache
            SearchCache.objects.update(created_at=timezone.now() - timedelta(hours=25))
            LinkedInPostsViaSearch().fetch_report(self.q)
            self.assertGreater(m.call_count, first)                     # cache older than 24h is ignored

    def test_never_contacts_linkedin_directly(self):
        import requests
        with mock.patch.object(search_providers.BraveProvider, "search", return_value=[hit("hr@acme.com")]), \
                mock.patch.object(requests, "get", side_effect=AssertionError("network call")):
            LinkedInPostsViaSearch().fetch_report(self.q)


@override_settings(BRAVE_API_KEY="k", BRAVE_API_URL="https://brave.test/search")
class ProviderTests(TestCase):
    def test_brave_request_shape_quota_and_parsing(self):
        resp = mock.Mock(status_code=200, headers={"X-RateLimit-Remaining": "1, 1998", "X-RateLimit-Limit": "1, 2000"})
        resp.raise_for_status = mock.Mock()
        resp.json.return_value = {"web": {"results": [{"title": "<b>Jane</b> on LinkedIn: hiring", "url": "https://www.linkedin.com/posts/a",
                                                       "description": "Send CV to <strong>hr@acme.com</strong>", "page_age": "2026-10-01T10:00:00"}]}}
        with mock.patch("requests.get", return_value=resp) as get:
            hits = search_providers.BraveProvider().search("q")
        kw = get.call_args.kwargs
        self.assertEqual(kw["headers"]["X-Subscription-Token"], "k")
        self.assertEqual((kw["params"]["freshness"], kw["params"]["count"]), ("pm", 20))
        self.assertEqual(hits[0].snippet, "Send CV to hr@acme.com")
        self.assertTrue(hits[0].posted_at.startswith("2026-10-01"))
        cfg = SearchSettings.load()
        self.assertEqual((cfg.quota_remaining, cfg.quota_limit), (1998, 2000))

    def test_429_is_quota_error(self):
        resp = mock.Mock(status_code=429, headers={})
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(QuotaExhausted):
                search_providers.BraveProvider().search("q")

    @override_settings(GOOGLE_CSE_KEY="g", GOOGLE_CSE_ID="cx", GOOGLE_CSE_DAILY_QUOTA=2, GOOGLE_CSE_URL="https://g.test/")
    def test_google_params_and_local_daily_quota(self):
        resp = mock.Mock(status_code=200, headers={}, content=b"{}")
        resp.raise_for_status = mock.Mock()
        resp.json.return_value = {"items": [{"title": "t", "link": "https://www.linkedin.com/posts/a", "snippet": "s"}]}
        p = search_providers.GoogleProvider()
        with mock.patch("requests.get", return_value=resp) as get:
            p.search("q"); p.search("q2")
            self.assertEqual(get.call_args.kwargs["params"]["dateRestrict"], "d30")
            with self.assertRaises(QuotaExhausted):
                p.search("q3")
        self.assertEqual(SearchSettings.load().quota_remaining, 0)

    def test_provider_choice_falls_back_to_configured_one(self):
        with override_settings(BRAVE_API_KEY="", GOOGLE_CSE_KEY="g", GOOGLE_CSE_ID="c"):
            self.assertEqual(search_providers.configured_provider("brave").name, "google")
