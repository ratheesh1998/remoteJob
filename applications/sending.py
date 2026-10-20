"""Queueing and sending. Every outgoing email goes through send_application()."""
import logging
import random
import uuid
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core import mailer
from core.models import SMTPConfig
from cv.models import CVProfile

from .models import Application

log = logging.getLogger(__name__)


def sent_today_count() -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return Application.objects.filter(status=Application.SENT, sent_at__gte=start).count()


def build_email(config: SMTPConfig, profile: CVProfile, app: Application) -> EmailMessage:
    msg = mailer.build_message(config, app.recipient, app.subject, app.body)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(config.from_email.split("@")[-1] or None))
    if profile and profile.file:
        profile.file.open("rb")
        try:
            data = profile.file.read()
        finally:
            profile.file.close()
        ext = profile.file.name.rsplit(".", 1)[-1].lower()
        mime = ("application", "pdf") if ext == "pdf" else (
            "application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
        name = f"{(profile.full_name or 'CV').replace(' ', '_')}_CV.{ext}"
        msg.add_attachment(data, maintype=mime[0], subtype=mime[1], filename=name)
    return msg


def transcript(msg: EmailMessage) -> str:
    """The exact email as sent, with the attachment shown by name and size instead of base64."""
    body = msg.get_body(preferencelist=("plain",))
    lines = [f"{h}: {msg[h]}" for h in ("From", "To", "Subject", "Date", "Message-ID") if msg[h]]
    text = "\n".join(lines) + "\n\n" + (body.get_content() if body else "")
    for att in msg.iter_attachments():
        size = len(att.get_payload(decode=True) or b"")
        text += f"\n[Attachment: {att.get_filename()} ({size // 1024} KB)]"
    return text


def random_delay() -> int:
    return random.randint(settings.SEND_DELAY_MIN, settings.SEND_DELAY_MAX)


def queue_batch(apps: list[Application]) -> uuid.UUID:
    """Mark drafts as queued with staggered send times, then hand them to Celery."""
    from .tasks import send_application

    batch = uuid.uuid4()
    when = timezone.now()
    scheduled = []
    with transaction.atomic():
        for i, app in enumerate(apps):
            if i:
                when += timedelta(seconds=random_delay())
            app.status = Application.QUEUED
            app.batch_id = batch
            app.scheduled_at = when
            app.error = ""
            app.retry_count = 0
            app.save()
            scheduled.append((app.id, when))
    # Dispatch only after the rows are committed so a fast worker always finds them.
    for app_id, eta in scheduled:
        send_application.apply_async(args=[app_id], eta=eta)
    return batch


def requeue(app: Application) -> None:
    from .tasks import send_application

    app.status = Application.QUEUED
    app.error = ""
    app.retry_count = 0
    app.scheduled_at = timezone.now()
    app.batch_id = app.batch_id or uuid.uuid4()
    app.save()
    send_application.apply_async(args=[app.id])


def send_one(app_id: int) -> str | None:
    """Send one queued application. Returns 'retry' when the caller should schedule another attempt."""
    # Claim: only a QUEUED row can be picked up, and only once (guards against duplicate task delivery).
    if not Application.objects.filter(pk=app_id, status=Application.QUEUED).update(status=Application.SENDING):
        return None
    app = Application.objects.get(pk=app_id)
    config, profile = SMTPConfig.peek(), CVProfile.peek()

    if not (config and config.is_ready):
        return _fail(app, "SMTP is not configured and verified.")

    limit = config.daily_limit
    if sent_today_count() >= limit:
        app.status = Application.DEFERRED
        app.error = f"Daily limit of {limit} emails reached. Not sent; use Retry tomorrow."
        app.save(update_fields=["status", "error"])
        return None

    try:
        msg = build_email(config, profile, app)
        conn = mailer.open_connection(config)
        try:
            conn.send_message(msg)
        finally:
            try:
                conn.quit()
            except Exception:
                pass
    except Exception as exc:
        failure = mailer.classify_error(exc, config)
        log.warning("Send failed for application %s: %s", app.id, failure.message)
        text = f"{failure.message} — {failure.hint}"
        if not failure.permanent and app.retry_count < settings.SEND_MAX_RETRIES:
            app.retry_count += 1
            app.status = Application.QUEUED
            app.error = f"Attempt {app.retry_count} failed, will retry: {text}"
            app.save(update_fields=["retry_count", "status", "error"])
            return "retry"
        return _fail(app, text)

    app.status = Application.SENT
    app.sent_at = timezone.now()
    app.error = ""
    app.sent_message = transcript(msg)
    app.save(update_fields=["status", "sent_at", "error", "sent_message"])
    return None


def _fail(app: Application, text: str) -> None:
    app.status = Application.FAILED
    app.error = text[:1000]
    app.save(update_fields=["status", "error"])
    return None


def create_drafts(jobs, profile: CVProfile) -> tuple[list[Application], int]:
    """Create draft rows for the selected jobs. Returns (drafts, skipped_count)."""
    from .emailing import build_subject

    drafts, skipped = [], 0
    existing = set(
        Application.objects.exclude(status=Application.DRAFT).values_list("recipient", "job_key")
    )
    for job in jobs:
        recipient = job.application_email.strip().lower()
        if not recipient or (recipient, job.job_key) in existing:
            skipped += 1
            continue
        try:
            with transaction.atomic():
                drafts.append(Application.objects.create(
                    job=job, job_key=job.job_key, job_title=job.title, company=job.company, job_url=job.url,
                    recipient=recipient, subject=build_subject(job.title, profile.full_name), status=Application.DRAFT,
                ))
        except IntegrityError:
            skipped += 1
    return drafts, skipped
