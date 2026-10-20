"""Features 2 and 3: paste-a-post, do-not-contact, 30-day contact cap, honest post emails, UI."""
from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from applications import contacts, sending
from applications.emailing import OPT_OUT, generate_body
from applications.models import Application, DoNotContact
from core.models import SMTPConfig
from cv.models import CVProfile
from jobs import paste, services
from jobs.dedupe import post_key
from jobs.models import Job, JobSearch, SearchCache, SearchSettings

from .base import AppTestCase
from .test_flow import FakeConn, make_job, make_profile

POST = ("Acme Robotics is hiring a Senior Python Developer (Remote). We use Python and Django.\n"
        "Send your CV to talent@acme.io")


def mx_ok(addr):
    return not addr.endswith("@nomx.test")


class PasteTests(AppTestCase):
    def setUp(self):
        self.make_smtp()
        make_profile()
        p = mock.patch("jobs.post_emails.deliverable", side_effect=mx_ok)
        p.start(); self.addCleanup(p.stop)

    def test_split_posts(self):
        self.assertEqual(len(paste.split_posts("one\n---\ntwo\n  ---  \n\nthree\n----\n")), 4 - 1)
        self.assertEqual(paste.split_posts("only one"), ["only one"])
        self.assertEqual(paste.split_posts("  \n---\n  "), [])
        self.assertEqual(len(paste.split_posts("\n---\n".join(["x"] * 50))), 20)     # capped

    def test_parse_extracts_email_title_company_skills(self):
        p = paste.parse_post(POST)
        self.assertEqual((p.email, p.title, p.company), ("talent@acme.io", "Senior Python Developer", "Acme Robotics"))
        self.assertIn("Python", p.skills)

    def test_parse_null_when_unsure(self):
        p = paste.parse_post("exciting news, mail me soon")
        self.assertEqual((p.email, p.title, p.company), ("", "", ""))
        self.assertIn("No usable email", p.note)

    @override_settings(ANTHROPIC_API_KEY="sk")
    def test_llm_values_must_appear_in_the_post(self):
        from core import llm
        fake = {"title": "Staff Wizard", "company": "Acme Robotics", "skills": ["Python", "Haskell"]}
        with mock.patch.object(llm, "complete_json", return_value=fake):
            p = paste.parse_post(POST)
        self.assertEqual(p.company, "Acme Robotics")          # in the text: accepted
        self.assertEqual(p.title, "Senior Python Developer")  # invented title rejected, regex value kept
        self.assertEqual(p.skills, ["Python"])                # Haskell not in the post

    def test_two_step_flow_saves_pinned_jobs_and_survives_new_search(self):
        two = POST + "\n---\nGlobex hiring a Backend Engineer. Email jobs@globex.io"
        r = self.client.post("/jobs/paste/", {"text": two, "url": ""})
        self.assertContains(r, "talent@acme.io")
        self.assertContains(r, "jobs@globex.io")
        data = {"form-TOTAL_FORMS": 2, "form-INITIAL_FORMS": 0, "form-MIN_NUM_FORMS": 0, "form-MAX_NUM_FORMS": 1000,
                "form-0-keep": "on", "form-0-email": "talent@acme.io", "form-0-title": "Senior Python Developer",
                "form-0-company": "Acme Robotics", "form-0-skills": "Python, Django", "form-0-url": "https://www.linkedin.com/posts/a_1", "form-0-text": POST,
                "form-1-email": "jobs@globex.io", "form-1-title": "Backend Engineer", "form-1-company": "", "form-1-skills": "",
                "form-1-url": "", "form-1-text": "Globex hiring"}          # row 1 not ticked -> skipped
        r = self.client.post("/jobs/paste/", data)
        self.assertRedirects(r, "/jobs/", fetch_redirect_response=False)
        job = Job.objects.get()
        self.assertEqual((job.source, job.application_email, job.pinned, job.company), ("linkedin_paste", "talent@acme.io", True, "Acme Robotics"))
        self.assertEqual(job.job_key, post_key("talent@acme.io", "https://www.linkedin.com/posts/a_1", POST))
        self.assertGreater(job.match_score, 40)
        # the pasted job moves into a brand-new search
        with mock.patch("jobs.services.group"):
            new = services.start_search(CVProfile.load())
        self.assertEqual(list(new.jobs.values_list("id", flat=True)), [job.id])
        self.assertEqual(JobSearch.objects.count(), 1)

    def test_save_rejects_noreply_and_domain_without_mx(self):
        base = {"form-TOTAL_FORMS": 1, "form-INITIAL_FORMS": 0, "form-MIN_NUM_FORMS": 0, "form-MAX_NUM_FORMS": 1000,
                "form-0-keep": "on", "form-0-title": "Dev", "form-0-text": "t", "form-0-company": "", "form-0-skills": "", "form-0-url": ""}
        for bad in ("noreply@acme.io", "x@nomx.test", "x@linkedin.com"):
            r = self.client.post("/jobs/paste/", {**base, "form-0-email": bad})
            self.assertEqual(r.status_code, 200, bad)
            self.assertFalse(Job.objects.exists(), bad)

    def test_duplicate_paste_is_not_added_twice(self):
        data = {"form-TOTAL_FORMS": 1, "form-INITIAL_FORMS": 0, "form-MIN_NUM_FORMS": 0, "form-MAX_NUM_FORMS": 1000,
                "form-0-keep": "on", "form-0-email": "talent@acme.io", "form-0-title": "Dev", "form-0-text": POST,
                "form-0-company": "", "form-0-skills": "", "form-0-url": ""}
        self.client.post("/jobs/paste/", data)
        self.client.post("/jobs/paste/", data)
        self.assertEqual(Job.objects.count(), 1)

    def test_requires_confirmed_cv(self):
        CVProfile.objects.update(confirmed=False)
        self.assertRedirects(self.client.get("/jobs/paste/"), "/cv/confirm/", fetch_redirect_response=False)


class DoNotContactTests(AppTestCase):
    def setUp(self):
        self.make_smtp()
        self.profile = make_profile()
        self.search = JobSearch.objects.create(designation="Python Developer", status=JobSearch.DONE)

    def test_normalize(self):
        self.assertEqual(contacts.normalize_entry(" Jane@Acme.COM "), "jane@acme.com")
        self.assertEqual(contacts.normalize_entry("@www.Acme.com"), "acme.com")
        for bad in ("", "nope", "a b.com", "x@"):
            with self.assertRaises(ValidationError):
                contacts.normalize_entry(bad)

    def test_matching_email_domain_and_subdomain(self):
        DoNotContact.objects.create(value="jane@acme.com")
        DoNotContact.objects.create(value="blocked.io")
        g = contacts.ContactGuard()
        for blocked in ("jane@acme.com", "JANE@acme.com", "anyone@blocked.io", "x@mail.blocked.io"):
            self.assertTrue(g.on_list(blocked), blocked)
        for ok in ("bob@acme.com", "x@notblocked.io", "x@blocked.io.evil.com"):
            self.assertFalse(g.on_list(ok), ok)

    def test_listed_address_never_becomes_a_draft_and_row_is_disabled(self):
        job = make_job(self.search, "Python Developer", "Acme", "hr@acme.com")
        DoNotContact.objects.create(value="acme.com")
        drafts, skipped = sending.create_drafts([job], self.profile)
        self.assertEqual((drafts, skipped), ([], ["on your do-not-contact list"]))
        html = self.client.get("/jobs/").content.decode()
        self.assertIn("On your do-not-contact list", html)
        self.assertNotIn(f'value="{job.id}"', html)
        r = self.client.get("/jobs/results/")
        self.assertEqual(r.context["email_ids"], [])               # "Select all with email" skips it

    def test_prepare_view_explains_skips(self):
        job = make_job(self.search, "Python Developer", "Acme", "hr@acme.com")
        DoNotContact.objects.create(value="hr@acme.com")
        r = self.client.post("/applications/prepare/", {"job_ids": [job.id]}, follow=True)
        self.assertContains(r, "do-not-contact")
        self.assertFalse(Application.objects.exists())

    def test_blocked_after_queueing_is_cancelled_at_send_time(self):
        FakeConn.sent = []
        job = make_job(self.search, "Python Developer", "Acme", "hr@acme.com")
        self.client.post("/applications/prepare/", {"job_ids": [job.id]})
        with mock.patch("applications.tasks.send_application.apply_async"):
            sending.queue_batch(list(Application.objects.all()))
        app = Application.objects.get()
        DoNotContact.objects.create(value="hr@acme.com")          # added directly, bypassing cancel_queued_for
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()):
            sending.send_one(app.id)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.CANCELLED)
        self.assertEqual(FakeConn.sent, [])

    def test_one_click_from_history_blocks_and_cancels_queued(self):
        a = Application.objects.create(job_key="k1", job_title="T", company="Acme", recipient="hr@acme.com", status=Application.QUEUED)
        b = Application.objects.create(job_key="k2", job_title="T2", company="Acme", recipient="HR@acme.com ".strip(), status=Application.SENT, sent_at=timezone.now())
        other = Application.objects.create(job_key="k3", job_title="T", company="Other", recipient="x@other.com", status=Application.QUEUED)
        self.assertContains(self.client.get("/applications/"), "Do not contact")
        self.client.post(f"/applications/{b.id}/do-not-contact/")
        a.refresh_from_db(); other.refresh_from_db()
        self.assertTrue(DoNotContact.objects.filter(value="hr@acme.com").exists())
        self.assertEqual((a.status, other.status), (Application.CANCELLED, Application.QUEUED))

    def test_list_page_add_and_remove(self):
        self.client.post("/applications/do-not-contact/", {"value": "Spam@Corp.com", "note": "asked to stop"})
        entry = DoNotContact.objects.get()
        self.assertEqual(entry.value, "spam@corp.com")
        self.assertContains(self.client.get("/applications/do-not-contact/"), "spam@corp.com")
        self.client.post("/applications/do-not-contact/", {"value": "not valid"})
        self.assertEqual(DoNotContact.objects.count(), 1)
        self.client.post(f"/applications/do-not-contact/{entry.id}/remove/")
        self.assertEqual(DoNotContact.objects.count(), 0)


class ContactCapTests(AppTestCase):
    def setUp(self):
        self.make_smtp()
        self.profile = make_profile()
        self.search = JobSearch.objects.create(designation="Python Developer", status=JobSearch.DONE)

    def sent(self, email, days_ago, key="old"):
        return Application.objects.create(job_key=key, job_title="Old", company="C", recipient=email, status=Application.SENT,
                                          sent_at=timezone.now() - timedelta(days=days_ago))

    def test_recent_contact_blocks_a_different_job_to_same_address(self):
        self.sent("hr@acme.com", 10)
        job = make_job(self.search, "Django Developer", "Acme", "hr@acme.com")
        drafts, skipped = sending.create_drafts([job], self.profile)
        self.assertEqual(drafts, [])
        self.assertIn("already emailed in the last 30 days", skipped[0])

    def test_older_than_cap_is_allowed(self):
        self.sent("hr@acme.com", 31)
        job = make_job(self.search, "Django Developer", "Acme", "hr@acme.com")
        self.assertEqual(len(sending.create_drafts([job], self.profile)[0]), 1)

    @override_settings(CONTACT_CAP_DAYS=5)
    def test_cap_length_is_configurable(self):
        self.sent("hr@acme.com", 10)
        job = make_job(self.search, "Django Developer", "Acme", "hr@acme.com")
        self.assertEqual(len(sending.create_drafts([job], self.profile)[0]), 1)

    def test_same_address_twice_in_one_batch_only_first_kept(self):
        j1 = make_job(self.search, "Python Developer", "Acme", "hr@acme.com")
        j2 = make_job(self.search, "Django Developer", "Beta", "hr@acme.com")
        drafts, skipped = sending.create_drafts([j1, j2], self.profile)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(len(skipped), 1)

    def test_queued_counts_and_failed_does_not(self):
        Application.objects.create(job_key="a", job_title="T", company="C", recipient="q@x.com", status=Application.QUEUED)
        Application.objects.create(job_key="b", job_title="T", company="C", recipient="f@x.com", status=Application.FAILED)
        g = contacts.ContactGuard()
        self.assertIsNotNone(g.block_reason("q@x.com"))
        self.assertIsNone(g.block_reason("f@x.com"))

    def test_send_time_cap_and_retry_not_blocked_by_itself(self):
        self.sent("hr@acme.com", 3)
        app = Application.objects.create(job_key="n", job_title="T", company="C", recipient="hr@acme.com", status=Application.QUEUED)
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()):
            sending.send_one(app.id)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.CANCELLED)
        self.assertIn("30 days", app.error)
        # a failed application being retried is not blocked by its own earlier attempt
        failed = Application.objects.create(job_key="m", job_title="T", company="C", recipient="new@y.com", status=Application.QUEUED)
        FakeConn.sent = []
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()):
            sending.send_one(failed.id)
        failed.refresh_from_db()
        self.assertEqual(failed.status, Application.SENT)


class PostEmailTemplateTests(AppTestCase):
    def setUp(self):
        self.profile = make_profile()

    def test_post_email_is_honest_and_has_opt_out(self):
        body, personalized, _ = generate_body(self.profile, "Python Developer", "Unknown", "We use Python.", source="linkedin_posts")
        self.assertIn("I saw your post on LinkedIn about the Python Developer role", body)
        self.assertIn(OPT_OUT, body)
        self.assertLess(len(body.split()), 150)
        self.assertIn("Jane Doe", body)
        self.assertNotIn("Unknown", body)
        for claim in ("we met", "we spoke", "know you", "mutual", "connection", "referred"):
            self.assertNotIn(claim, body.lower())

    def test_normal_job_email_has_no_post_wording(self):
        body, _, _ = generate_body(self.profile, "Python Developer", "Acme", "We use Python.", source="remotive")
        self.assertNotIn("LinkedIn", body)
        self.assertNotIn(OPT_OUT, body)

    @override_settings(ANTHROPIC_API_KEY="sk")
    def test_llm_paragraph_inside_post_template_still_grounded(self):
        from core import llm
        bad = {"paragraph": "We worked together at Google for 12 years.", "matched_skills": ["Python"]}
        with mock.patch.object(llm, "complete_json", return_value=bad):
            body, personalized, _ = generate_body(self.profile, "Python Developer", "Unknown", "Python", source="linkedin_paste")
        self.assertFalse(personalized)
        self.assertNotIn("Google", body)
        self.assertIn(OPT_OUT, body)

    def test_post_job_drafts_use_the_post_template(self):
        self.make_smtp()
        search = JobSearch.objects.create(designation="Python Developer", status=JobSearch.DONE)
        job = make_job(search, "Python Developer", "Unknown", "talent@acme.io", source="linkedin_posts")
        self.client.post("/applications/prepare/", {"job_ids": [job.id]})
        self.assertIn("I saw your post on LinkedIn", Application.objects.get().body)


class PostsUITests(AppTestCase):
    def setUp(self):
        self.make_smtp()
        make_profile()
        self.search = JobSearch.objects.create(designation="Python Developer", status=JobSearch.DONE)

    def test_badge_view_post_link_note_and_source_filter(self):
        make_job(self.search, "Python Developer", "Unknown", "hr@acme.io", source="linkedin_posts", url="https://www.linkedin.com/posts/a_1")
        make_job(self.search, "Python Engineer", "Beta", "x@beta.io", source="remotive", url="https://beta.io/1")
        html = self.client.get("/jobs/").content.decode()
        self.assertIn("LinkedIn post", html)
        self.assertIn("View post", html)
        self.assertIn("These emails come from public hiring posts. Use them only to apply for the advertised role.", html)
        r = self.client.get("/jobs/results/", {"source": "linkedin_posts"})
        self.assertEqual([j.company for j in r.context["page_obj"]], ["Unknown"])
        r = self.client.get("/jobs/results/", {"source": "linkedin_paste"})
        self.assertEqual(r.status_code, 200)                       # pasted source is a valid filter value

    def test_note_hidden_without_post_jobs(self):
        make_job(self.search, "Python Engineer", "Beta", "x@beta.io", source="remotive")
        self.assertNotIn("public hiring posts", self.client.get("/jobs/").content.decode())

    def test_empty_state_not_configured_and_none_found(self):
        html = self.client.get("/jobs/").content.decode()
        self.assertIn("BRAVE_API_KEY", html)
        self.assertIn("/jobs/paste/", html)
        with override_settings(BRAVE_API_KEY="k"):
            from jobs.models import SourceResult
            SourceResult.objects.create(search=self.search, name="linkedin_posts", label="LinkedIn posts", status="ok", count=0)
            html = self.client.get("/jobs/").content.decode()
        self.assertIn("often cut posts short", html)

    @override_settings(BRAVE_API_KEY="super-secret-brave-key")
    def test_settings_page_never_shows_the_key(self):
        html = self.client.get("/jobs/search-settings/").content.decode()
        self.assertIn("key set", html)
        self.assertNotIn("super-secret-brave-key", html)
        r = self.client.post("/jobs/search-settings/", {"provider": "google", "max_queries_per_run": 4})
        self.assertEqual(r.status_code, 302)
        cfg = SearchSettings.load()
        self.assertEqual((cfg.provider, cfg.max_queries_per_run), ("google", 4))
        r = self.client.post("/jobs/search-settings/", {"provider": "brave", "max_queries_per_run": 99})
        self.assertEqual(r.status_code, 200)

    def test_progress_shows_partial_failure_note(self):
        from jobs.models import SourceResult
        self.search.status = JobSearch.RUNNING
        self.search.save()
        SourceResult.objects.create(search=self.search, name="linkedin_posts", label="LinkedIn posts", status="ok", count=7, note="2 of 6 queries failed")
        html = self.client.get("/jobs/progress/").content.decode()
        self.assertIn("LinkedIn posts: 7", html)
        self.assertIn("2 of 6 queries failed", html)

    def test_delete_all_wipes_posts_cache_list_and_settings(self):
        make_job(self.search, "Python Developer", "Unknown", "hr@acme.io", source="linkedin_posts")
        SearchCache.objects.create(provider="brave", query_hash="h", query="q", results=[{"snippet": "hr@acme.io"}])
        DoNotContact.objects.create(value="a@b.com")
        SearchSettings.load()
        self.client.post("/delete-all/", {"confirm": "DELETE"})
        for model in (Job, SearchCache, DoNotContact, SearchSettings, JobSearch):
            self.assertEqual(model.objects.count(), 0, model)
