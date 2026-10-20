"""Gate, CV upload/confirm, search merge, review/queue, sending, history, delete-all."""
import smtplib
from datetime import timedelta
from unittest import mock

from django.core import mail  # noqa: F401  (ensures test mail backend is not used for SMTP below)
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from applications import sending
from applications.models import Application
from core.models import SMTPConfig
from cv.models import CVProfile
from jobs import services
from jobs.models import Job, JobSearch, SourceResult
from jobs.types import JobData

from .base import AppTestCase
from .helpers import make_pdf, make_scanned_pdf


class FakeConn:
    sent = []

    def __init__(self, fail=None):
        self.fail = fail

    def send_message(self, msg):
        if self.fail:
            raise self.fail
        FakeConn.sent.append(msg)

    def quit(self):
        pass


def make_profile(confirmed=True):
    from django.core.files.base import ContentFile
    p = CVProfile.load()
    p.file.save("cv.pdf", ContentFile(make_pdf()), save=False)
    p.original_filename = "cv.pdf"
    p.raw_text = "Jane Doe Python Django"
    p.full_name, p.email, p.phone = "Jane Doe", "jane@example.com", "+1 415 555 0134"
    p.designation, p.skills, p.years_experience = "Python Developer", ["Python", "Django", "Docker"], 8
    p.confirmed = confirmed
    p.save()
    return p


def make_job(search, title="Python Developer", company="Acme", email="jobs@acme.com", **kw):
    from jobs.dedupe import job_key
    return Job.objects.create(
        search=search, title=title, company=company, url=f"https://acme.com/{title}-{company}".replace(" ", "-"),
        source="remotive", application_email=email, match_score=kw.pop("score", 80),
        description=kw.pop("description", "We use Python and Django."), job_key=job_key(company, title), **kw)


class GateTests(AppTestCase):
    def test_everything_redirects_to_smtp_until_verified(self):
        for path in ["/", "/cv/", "/cv/confirm/", "/jobs/", "/applications/", "/applications/review/"]:
            r = self.client.get(path)
            self.assertRedirects(r, "/smtp/", fetch_redirect_response=False, msg_prefix=path)

    def test_smtp_help_and_delete_reachable(self):
        for path in ["/smtp/", "/help/", "/delete-all/"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_unverified_config_still_gated(self):
        self.make_smtp(verified=False)
        self.assertRedirects(self.client.get("/cv/"), "/smtp/", fetch_redirect_response=False)

    def test_unlocked_when_verified(self):
        self.make_smtp()
        self.assertEqual(self.client.get("/cv/").status_code, 200)

    def test_banner_only_for_non_local_host(self):
        with override_settings(ALLOWED_HOSTS=["*"]):
            banner = 'role="alert"'
            self.assertNotContains(self.client.get("/help/", HTTP_HOST="127.0.0.1:8000"), banner)
            self.assertNotContains(self.client.get("/help/", HTTP_HOST="localhost"), banner)
            self.assertNotContains(self.client.get("/help/", HTTP_HOST="[::1]:8000"), banner)
            r = self.client.get("/help/", HTTP_HOST="myserver.example.com")
            self.assertContains(r, banner)
            self.assertContains(r, "This app has no login. Do not expose it to the internet.")


class SMTPSetupTests(AppTestCase):
    data = {"host": "smtp.gmail.com", "port": 465, "security": "ssl", "username": "me@gmail.com",
            "password": "app-pass-word", "from_name": "Me", "from_email": "me@gmail.com", "daily_limit": 40}

    def test_save_encrypts_and_does_not_verify(self):
        r = self.client.post("/smtp/", {**self.data, "action": "save"})
        self.assertEqual(r.status_code, 302)
        cfg = SMTPConfig.load()
        self.assertFalse(cfg.verified)
        self.assertEqual(cfg.get_password(), "app-pass-word")
        self.assertNotIn("app-pass-word", cfg.password_encrypted)

    def test_test_email_success_marks_verified(self):
        with mock.patch("core.mailer.open_connection", return_value=FakeConn()):
            r = self.client.post("/smtp/", {**self.data, "action": "test"})
        self.assertContains(r, "Test email sent")
        self.assertTrue(SMTPConfig.load().verified)

    def test_test_email_failure_shows_real_error_and_hint(self):
        err = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        with mock.patch("core.mailer.open_connection", side_effect=err):
            r = self.client.post("/smtp/", {**self.data, "action": "test"})
        self.assertContains(r, "Username and Password not accepted")
        self.assertContains(r, "App Password")
        self.assertFalse(SMTPConfig.load().verified)
        self.assertNotContains(r, "app-pass-word")

    def test_changing_connection_settings_clears_verified(self):
        self.make_smtp(verified=True)
        self.client.post("/smtp/", {**self.data, "password": "", "host": "smtp.other.test", "action": "save"})
        self.assertFalse(SMTPConfig.load().verified)

    def test_blank_password_keeps_existing(self):
        self.make_smtp()
        self.client.post("/smtp/", {**self.data, "host": "smtp.example.test", "username": "me@example.test",
                                    "from_email": "me@example.test", "from_name": "Me", "port": 465,
                                    "password": "", "action": "save"})
        self.assertEqual(SMTPConfig.load().get_password(), "secret-app-password")

    def test_validation(self):
        r = self.client.post("/smtp/", {**self.data, "port": 70000, "host": "http://x", "daily_limit": 0,
                                        "from_email": "nope", "action": "save"})
        self.assertEqual(r.status_code, 200)
        for f in ("port", "host", "daily_limit", "from_email"):
            self.assertIn(f, r.context["form"].errors)

    def test_starttls_587_accepted(self):
        r = self.client.post("/smtp/", {**self.data, "host": "smtp-mail.outlook.com", "port": 587,
                                        "security": "starttls", "action": "save"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SMTPConfig.load().security, "starttls")


class CVTests(AppTestCase):
    def setUp(self):
        self.make_smtp()

    def test_upload_extracts_and_confirm_resets(self):
        r = self.client.post("/cv/", {"file": SimpleUploadedFile("my cv.pdf", make_pdf())})
        self.assertRedirects(r, "/cv/confirm/")
        p = CVProfile.load()
        self.assertEqual(p.email, "jane.doe@example.com")
        self.assertFalse(p.confirmed)
        # confirm
        r = self.client.post("/cv/confirm/", {"designation": "Python Developer", "alternate_titles": "Backend Engineer",
                                              "skills": "Python, Django", "location": "Lisbon", "full_name": "Jane Doe",
                                              "email": "jane.doe@example.com", "phone": ""})
        self.assertRedirects(r, "/jobs/", fetch_redirect_response=False)
        p.refresh_from_db()
        self.assertTrue(p.confirmed)
        self.assertEqual(p.alternate_titles, ["Backend Engineer"])
        # re-upload resets confirmation
        self.client.post("/cv/", {"file": SimpleUploadedFile("cv2.pdf", make_pdf())})
        p.refresh_from_db()
        self.assertFalse(p.confirmed)

    def test_scanned_pdf_rejected_and_old_cv_kept(self):
        make_profile()
        r = self.client.post("/cv/", {"file": SimpleUploadedFile("scan.pdf", make_scanned_pdf())})
        self.assertContains(r, "text-based")
        self.assertEqual(CVProfile.load().raw_text, "Jane Doe Python Django")

    def test_non_pdf_rejected(self):
        r = self.client.post("/cv/", {"file": SimpleUploadedFile("cv.pdf", b"not a pdf")})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(CVProfile.objects.exists())

    def test_search_blocked_until_confirmed(self):
        make_profile(confirmed=False)
        self.assertRedirects(self.client.get("/jobs/"), "/cv/confirm/", fetch_redirect_response=False)
        with mock.patch("jobs.services.start_search") as start:
            self.client.post("/jobs/start/")
        start.assert_not_called()

    def test_stepper_states(self):
        r = self.client.get("/cv/")
        states = [(s.label, s.done, s.current, s.locked) for s in r.context["steps"]]
        self.assertEqual(states[0][1], True)       # email done
        self.assertEqual(states[1][2], True)       # CV is the next step
        self.assertTrue(all(s[3] for s in states[2:]))   # the rest locked


class SearchMergeTests(AppTestCase):
    def setUp(self):
        self.make_smtp()
        self.profile = make_profile()

    def test_one_failing_source_does_not_break_search(self):
        search = JobSearch.objects.create(designation="Python Developer", titles=["Python Developer"], skills=["Python", "Django"])
        for name in ("remotive", "remoteok", "himalayas"):
            SourceResult.objects.create(search=search, name=name, label=name.title())
        good = [JobData(title="Python Developer", company="Acme", url="https://a.com/1", source="remotive",
                        description="Python and Django", application_email="jobs@acme.com"),
                JobData(title="Chef", company="Diner", url="https://d.com/1", source="remotive", description="Cook")]
        services.record_source_result(search.id, "remotive", good)
        search.refresh_from_db()
        self.assertTrue(search.is_running)   # still waiting on the others
        services.record_source_result(search.id, "remoteok", None, error="ReadTimeout: slow")
        services.record_source_result(search.id, "himalayas", [
            JobData(title="python developer", company="ACME", url="https://h.com/9", source="himalayas",
                    description="Python", salary="$100")])
        search.refresh_from_db()
        self.assertEqual(search.status, JobSearch.DONE)
        self.assertEqual(search.total_jobs, 1)      # chef irrelevant, duplicate merged
        job = search.jobs.get()
        self.assertEqual(job.application_email, "jobs@acme.com")
        self.assertEqual(job.salary, "$100")
        statuses = {s.name: (s.status, s.count) for s in search.sources.all()}
        self.assertEqual(statuses["remotive"], ("ok", 1))
        self.assertEqual(statuses["remoteok"][0], "failed")

    def test_finalize_runs_only_once(self):
        search = JobSearch.objects.create(designation="Python Developer", titles=["Python Developer"], skills=[])
        SourceResult.objects.create(search=search, name="remotive", label="Remotive")
        j = JobData(title="Python Developer", company="Acme", url="https://a.com/1", source="remotive")
        services.record_source_result(search.id, "remotive", [j])
        services.finalize_if_complete(search.id)
        self.assertEqual(search.jobs.count(), 1)

    def test_stale_search_is_expired(self):
        search = JobSearch.objects.create(designation="Python Developer", titles=["Python Developer"], skills=[],
                                          created_at=timezone.now() - timedelta(minutes=10))
        SourceResult.objects.create(search=search, name="remotive", label="Remotive")
        services.expire_stale_search(search)
        search.refresh_from_db()
        self.assertEqual(search.status, JobSearch.DONE)
        self.assertEqual(search.sources.get().status, SourceResult.FAILED)

    def test_results_filters_and_pages(self):
        search = JobSearch.objects.create(designation="Python Developer", status=JobSearch.DONE, total_jobs=3)
        make_job(search, "Python Developer", "Acme", "jobs@acme.com", score=90)
        make_job(search, "Python Engineer", "Beta", "", score=60)
        make_job(search, "Backend Developer", "Gamma", "x@gamma.com", score=20)
        r = self.client.get("/jobs/results/", {"has_email": "on", "min_score": 50})
        self.assertEqual([j.company for j in r.context["page_obj"]], ["Acme"])
        r = self.client.get("/jobs/results/", {"q": "engineer"})
        self.assertEqual([j.company for j in r.context["page_obj"]], ["Beta"])
        r = self.client.get("/jobs/results/", {"sort": "company"})
        self.assertEqual([j.company for j in r.context["page_obj"]], ["Acme", "Beta", "Gamma"])
        html = self.client.get("/jobs/").content.decode()
        # The ids for "Select all with email" must be real JSON array data, not a JSON-encoded string.
        self.assertRegex(html, r'<script id="email-ids" type="application/json">\[\d+(, \d+)*\]</script>')
        self.assertIn("Email apply", html)
        self.assertIn("Apply on site", html)       # Beta has no email

    def test_description_html_is_never_rendered_raw(self):
        search = JobSearch.objects.create(designation="x", status=JobSearch.DONE, total_jobs=1)
        make_job(search, description="<script>alert(1)</script><b>hi</b>")
        html = self.client.get("/jobs/").content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)


class ApplicationFlowTests(AppTestCase):
    def setUp(self):
        self.make_smtp()
        self.profile = make_profile()
        self.search = JobSearch.objects.create(designation="Python Developer", status=JobSearch.DONE, total_jobs=3)
        self.j1 = make_job(self.search, "Python Developer", "Acme", "jobs@acme.com")
        self.j2 = make_job(self.search, "Django Developer", "Beta", "hr@beta.com")
        self.j3 = make_job(self.search, "Python Engineer", "NoEmail", "")
        FakeConn.sent = []

    def prepare(self, *jobs):
        return self.client.post("/applications/prepare/", {"job_ids": [j.id for j in jobs]})

    def test_prepare_creates_drafts_with_subject_body_signature(self):
        self.assertRedirects(self.prepare(self.j1, self.j2, self.j3), "/applications/review/")
        drafts = Application.objects.filter(status=Application.DRAFT)
        self.assertEqual(drafts.count(), 2)          # job without email never becomes a draft
        d = drafts.get(recipient="jobs@acme.com")
        self.assertEqual(d.subject, "Application for Python Developer - Jane Doe")
        self.assertLess(len(d.body.split()), 150)
        self.assertIn("Jane Doe", d.body)
        self.assertIn("+1 415 555 0134", d.body)
        self.assertIn("jane@example.com", d.body)
        self.assertIn("Python", d.body)
        self.assertTrue(d.draft_ready)

    def test_review_edit_remove_and_nothing_sent_before_confirm(self):
        self.prepare(self.j1, self.j2)
        page = self.client.get("/applications/review/")
        self.assertContains(page, "Confirm and queue")
        d1, d2 = Application.objects.filter(status=Application.DRAFT).order_by("id")
        self.client.post(f"/applications/{d2.id}/remove/")
        self.assertEqual(Application.objects.filter(status=Application.DRAFT).count(), 1)
        self.assertEqual(FakeConn.sent, [])
        self.assertFalse(Application.objects.filter(status__in=Application.ACTIVE).exists())

    def test_confirm_validates_header_injection(self):
        self.prepare(self.j1)
        d = Application.objects.get()
        r = self.client.post("/applications/confirm/", {f"{d.id}-subject": "Hi\nBcc: evil@x.com", f"{d.id}-body": "text"})
        self.assertEqual(r.status_code, 200)
        d.refresh_from_db()
        self.assertEqual(d.status, Application.DRAFT)

    @override_settings(SEND_DELAY_MIN=20, SEND_DELAY_MAX=60)
    def test_confirm_queues_with_random_spacing_and_sends(self):
        self.prepare(self.j1, self.j2)
        d1, d2 = Application.objects.order_by("id")
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()), \
                mock.patch("applications.tasks.send_application.apply_async") as apply_async:
            r = self.client.post("/applications/confirm/", {
                f"{d1.id}-subject": "Custom subject", f"{d1.id}-body": "Edited body",
                f"{d2.id}-subject": d2.subject, f"{d2.id}-body": d2.body})
        self.assertEqual(r.status_code, 302)
        d1.refresh_from_db(); d2.refresh_from_db()
        self.assertEqual((d1.status, d1.subject, d1.body), (Application.QUEUED, "Custom subject", "Edited body"))
        gap = (d2.scheduled_at - d1.scheduled_at).total_seconds()
        self.assertTrue(20 <= gap <= 60, gap)
        self.assertEqual(apply_async.call_count, 2)
        # run the sender
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()):
            sending.send_one(d1.id)
        d1.refresh_from_db()
        self.assertEqual(d1.status, Application.SENT)
        msg = FakeConn.sent[0]
        self.assertEqual(msg["Subject"], "Custom subject")
        self.assertEqual(msg["To"], "jobs@acme.com")
        self.assertEqual(len(list(msg.iter_attachments())), 1)     # CV attached
        self.assertIn("Edited body", d1.sent_message)
        self.assertIn("[Attachment:", d1.sent_message)

    def _queue(self, *jobs):
        self.prepare(*jobs)
        apps = list(Application.objects.order_by("id"))
        with mock.patch("applications.tasks.send_application.apply_async"):
            sending.queue_batch(apps)
        return apps

    def test_never_two_applications_same_recipient_and_job(self):
        self._queue(self.j1)
        # A new search yields a new Job row for the same real posting.
        s2 = JobSearch.objects.create(designation="Python Developer", status=JobSearch.DONE)
        again = make_job(s2, "Python Developer", "Acme", "jobs@acme.com")
        self.prepare(again)
        self.assertEqual(Application.objects.filter(status=Application.DRAFT).count(), 0)
        self.assertEqual(Application.objects.count(), 1)
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            Application.objects.create(job_key=self.j1.job_key, job_title="x", company="y", recipient="JOBS@acme.com")

    def test_daily_limit_defers(self):
        cfg = SMTPConfig.load(); cfg.daily_limit = 1; cfg.save()
        a1, a2 = self._queue(self.j1, self.j2)
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()):
            sending.send_one(a1.id)
            sending.send_one(a2.id)
        a1.refresh_from_db(); a2.refresh_from_db()
        self.assertEqual(a1.status, Application.SENT)
        self.assertEqual(a2.status, Application.DEFERRED)
        self.assertIn("Daily limit", a2.error)
        self.assertEqual(len(FakeConn.sent), 1)
        page = self.client.get(f"/applications/progress/{a1.batch_id}/")
        self.assertContains(page, "Daily limit of 1 emails reached")

    def test_yesterdays_sends_do_not_count(self):
        cfg = SMTPConfig.load(); cfg.daily_limit = 1; cfg.save()
        a1, = self._queue(self.j1)
        old = Application.objects.create(job_key="old", job_title="o", company="o", recipient="o@o.com",
                                         status=Application.SENT, sent_at=timezone.now() - timedelta(days=1, hours=1))
        self.assertEqual(sending.sent_today_count(), 0)
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()):
            sending.send_one(a1.id)
        a1.refresh_from_db()
        self.assertEqual(a1.status, Application.SENT)

    def test_transient_error_retries_at_most_twice_then_fails(self):
        a1, = self._queue(self.j1)
        err = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        with mock.patch("core.mailer.open_connection", side_effect=err):
            results = []
            for _ in range(5):
                results.append(sending.send_one(a1.id))
        a1.refresh_from_db()
        self.assertEqual(results[:3], ["retry", "retry", None])
        self.assertEqual(a1.retry_count, 2)
        self.assertEqual(a1.status, Application.FAILED)
        self.assertIn("closed", a1.error)

    def test_permanent_error_fails_immediately(self):
        a1, = self._queue(self.j1)
        err = smtplib.SMTPAuthenticationError(535, b"bad credentials")
        with mock.patch("core.mailer.open_connection", side_effect=err):
            self.assertIsNone(sending.send_one(a1.id))
        a1.refresh_from_db()
        self.assertEqual((a1.status, a1.retry_count), (Application.FAILED, 0))
        self.assertIn("bad credentials", a1.error)

    def test_duplicate_task_delivery_sends_once(self):
        a1, = self._queue(self.j1)
        with mock.patch("core.mailer.open_connection", side_effect=lambda c: FakeConn()):
            sending.send_one(a1.id)
            sending.send_one(a1.id)
        self.assertEqual(len(FakeConn.sent), 1)

    def test_retry_button_requeues_failed_only(self):
        a1, a2 = self._queue(self.j1, self.j2)
        Application.objects.filter(pk=a1.pk).update(status=Application.FAILED, error="x")
        with mock.patch("applications.tasks.send_application.apply_async") as aa:
            self.client.post(f"/applications/{a1.id}/retry/")
            self.client.post(f"/applications/{a2.id}/retry/")   # still queued: must be ignored
        a1.refresh_from_db(); a2.refresh_from_db()
        self.assertEqual(a1.status, Application.QUEUED)
        self.assertEqual(aa.call_count, 1)

    def test_history_and_csv_export(self):
        a1, = self._queue(self.j1)
        Application.objects.filter(pk=a1.pk).update(job_title="=HYPERLINK(\"http://evil\")", status=Application.FAILED, error="boom")
        page = self.client.get("/applications/")
        self.assertContains(page, "Retry")
        self.assertContains(page, "boom")
        csv_resp = self.client.get("/applications/export.csv")
        body = csv_resp.content.decode()
        self.assertEqual(csv_resp["Content-Type"], "text/csv")
        self.assertIn("jobs@acme.com", body)
        self.assertNotIn(",=HYPERLINK", body)        # formula neutralized
        self.assertIn("'=HYPERLINK", body)

    def test_delete_all_data(self):
        self._queue(self.j1)
        self.assertEqual(self.client.post("/delete-all/", {"confirm": "nope"}).status_code, 200)
        self.assertTrue(CVProfile.objects.exists())
        r = self.client.post("/delete-all/", {"confirm": "DELETE"})
        self.assertRedirects(r, "/smtp/", fetch_redirect_response=False)
        for model in (CVProfile, SMTPConfig, Job, JobSearch, Application):
            self.assertEqual(model.objects.count(), 0, model)


class PersonalizationTests(AppTestCase):
    def setUp(self):
        self.profile = make_profile()

    @override_settings(ANTHROPIC_API_KEY="sk-test")
    def test_grounded_llm_paragraph_is_used(self):
        from applications import emailing
        from core import llm
        out = {"paragraph": "I have built Python and Django services for years.", "matched_skills": ["Python", "Django"]}
        with mock.patch.object(llm, "complete_json", return_value=out):
            body, personalized, note = emailing.generate_body(
                self.profile, "Python Developer", "Acme", "We need Python and Django.")
        self.assertTrue(personalized)
        self.assertIn("built Python and Django services", body)

    @override_settings(ANTHROPIC_API_KEY="sk-test")
    def test_ungrounded_llm_output_rejected(self):
        from applications import emailing
        from core import llm
        bad_skill = {"paragraph": "I love Kubernetes.", "matched_skills": ["Kubernetes"]}
        invented_number = {"paragraph": "I have 15 years of Python.", "matched_skills": ["Python"]}
        for out in (bad_skill, invented_number):
            with mock.patch.object(llm, "complete_json", return_value=out):
                body, personalized, note = emailing.generate_body(
                    self.profile, "Python Developer", "Acme", "We need Python.")
            self.assertFalse(personalized)
            self.assertNotIn("Kubernetes", body)
            self.assertNotIn("15 years", body)
            self.assertIn("verified", note)

    @override_settings(ANTHROPIC_API_KEY="sk-test")
    def test_llm_error_falls_back_to_template(self):
        from applications import emailing
        from core import llm
        with mock.patch.object(llm, "complete_json", side_effect=llm.LLMError("down")):
            body, personalized, note = emailing.generate_body(self.profile, "Python Developer", "Acme", "Python")
        self.assertFalse(personalized)
        self.assertIn("Jane Doe", body)

    def test_template_body_under_150_words_and_only_real_skills(self):
        from applications import emailing
        body, _, _ = emailing.generate_body(self.profile, "Python Developer", "Acme", "Python Rust Go")
        self.assertLess(len(body.split()), 150)
        self.assertIn("Python", body)
        self.assertNotIn("Rust", body)


class UnencryptedSMTPTests(AppTestCase):
    data = {"host": "smtp.gmail.com", "port": 25, "security": "none", "username": "u", "password": "p",
            "from_name": "", "from_email": "me@example.com", "daily_limit": 5, "action": "save"}

    def test_none_rejected_for_real_hosts(self):
        r = self.client.post("/smtp/", self.data)
        self.assertEqual(r.status_code, 200)
        self.assertIn("security", r.context["form"].errors)

    def test_none_allowed_for_mailhog(self):
        r = self.client.post("/smtp/", {**self.data, "host": "mailhog", "port": 1025})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SMTPConfig.load().security, "none")

