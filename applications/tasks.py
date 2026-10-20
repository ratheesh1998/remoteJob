import logging

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from core.models import SMTPConfig
from cv.models import CVProfile

from .emailing import generate_body
from .models import Application
from .sending import send_one

log = logging.getLogger(__name__)


def fill_draft(app: Application) -> None:
    """Generate (or regenerate) the body of a draft. Never raises: falls back to the template."""
    profile = CVProfile.peek()
    config = SMTPConfig.peek()
    description = app.job.description if app.job else ""
    body, personalized, note = generate_body(
        profile, app.job_title, app.company, description, fallback_email=config.from_email if config else "")
    app.body, app.personalized, app.draft_note, app.draft_ready = body, personalized, note, True
    app.save(update_fields=["body", "personalized", "draft_note", "draft_ready"])


@shared_task
def generate_draft(app_id: int):
    app = Application.objects.select_related("job").filter(pk=app_id, status=Application.DRAFT).first()
    if app and not app.draft_ready:
        fill_draft(app)


@shared_task(bind=True)
def send_application(self, app_id: int):
    result = send_one(app_id)
    if result == "retry":
        # Back off a little longer on each retry (at most SEND_MAX_RETRIES retries).
        app = Application.objects.get(pk=app_id)
        send_application.apply_async(args=[app_id], countdown=60 * app.retry_count)


@shared_task
def rescue_orphaned_applications():
    """Beat watchdog: re-dispatch queued emails whose task was lost (e.g. Redis was flushed).
    Safe to over-dispatch: send_one() claims each row atomically, so an email is never sent twice."""
    cutoff = timezone.now() - timedelta(minutes=15)
    for app_id in Application.objects.filter(status=Application.QUEUED, scheduled_at__lt=cutoff).values_list("id", flat=True):
        send_application.apply_async(args=[app_id])
