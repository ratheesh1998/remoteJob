import csv
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.models import SMTPConfig
from cv.models import CVProfile
from jobs.models import Job, JobSearch

from . import sending
from .emailing import MAX_WORDS, word_count
from .forms import DraftForm
from .models import Application
from .tasks import fill_draft, generate_draft

MAX_SELECTION = 200
STALE_DRAFT_SECONDS = 60


@require_POST
def prepare(request):
    """Create drafts for the selected jobs and start generating their emails."""
    profile = CVProfile.peek()
    if not (profile and profile.confirmed):
        return redirect("cv_confirm")
    try:
        ids = {int(i) for i in request.POST.getlist("job_ids")}
    except ValueError:
        return HttpResponseBadRequest("Invalid selection.")
    if not ids:
        messages.info(request, "Select at least one job first.")
        return redirect("job_search")
    if len(ids) > MAX_SELECTION:
        messages.error(request, f"Select at most {MAX_SELECTION} jobs at a time.")
        return redirect("job_search")

    search = JobSearch.objects.first()
    jobs = Job.objects.filter(pk__in=ids, search=search).exclude(application_email="") if search else Job.objects.none()
    Application.objects.filter(status=Application.DRAFT).delete()   # start a fresh review
    drafts, skipped = sending.create_drafts(jobs, profile)
    if skipped or len(jobs) < len(ids):
        messages.info(request, f"{len(ids) - len(drafts)} selected job(s) were skipped (no email address, or already applied/queued).")
    if not drafts:
        return redirect("job_search")
    for draft in drafts:
        generate_draft.delay(draft.id)
    return redirect("applications_review")


def _pending(drafts):
    return [d for d in drafts if not d.draft_ready]


def _drafts():
    return list(Application.objects.filter(status=Application.DRAFT).select_related("job").order_by("id"))


def _finish_stale(drafts):
    """If a worker is not generating drafts, fall back to generating them here instead of hanging."""
    cutoff = timezone.now() - timedelta(seconds=STALE_DRAFT_SECONDS)
    for d in drafts:
        if not d.draft_ready and d.created_at < cutoff:
            fill_draft(d)


def review(request):
    drafts = _drafts()
    if not drafts:
        messages.info(request, "Pick some jobs first, then review their emails here.")
        return redirect("job_search")
    return render(request, "applications/review.html", {
        "page_hint": "Check each email, edit anything you like, remove jobs you don't want. Nothing sends until you confirm.",
        **_review_context(drafts),
    })


@require_GET
def review_drafts(request):
    """HTMX partial: polls while emails are still being generated."""
    drafts = _drafts()
    _finish_stale(drafts)
    drafts = _drafts()
    return render(request, "applications/_drafts.html", _review_context(drafts))


def _review_context(drafts, forms=None):
    config = SMTPConfig.peek()
    pending = _pending(drafts)
    limit = config.daily_limit if config else 0
    sent = sending.sent_today_count()
    cards = []
    for d in drafts:
        form = (forms or {}).get(d.id) or DraftForm(instance=d, prefix=str(d.id))
        cards.append({"app": d, "form": form, "words": word_count(d.body)})
    return {
        "cards": cards, "pending_count": len(pending), "total": len(drafts),
        "limit": limit, "sent_today": sent, "remaining_today": max(0, limit - sent),
        "over_limit": len(drafts) > max(0, limit - sent), "max_words": MAX_WORDS,
    }


@require_POST
def remove_draft(request, pk):
    get_object_or_404(Application, pk=pk, status=Application.DRAFT).delete()
    if request.headers.get("HX-Request"):
        return HttpResponse("")
    return redirect("applications_review")


@require_POST
def confirm(request):
    drafts = _drafts()
    if not drafts:
        return redirect("job_search")
    if _pending(drafts):
        messages.info(request, "Emails are still being prepared. Wait a moment and confirm again.")
        return redirect("applications_review")
    forms = {d.id: DraftForm(request.POST, instance=d, prefix=str(d.id)) for d in drafts}
    if not all(f.is_valid() for f in forms.values()):
        return render(request, "applications/review.html", {
            "page_hint": "Fix the highlighted problems, then confirm again.", **_review_context(drafts, forms)})
    for f in forms.values():
        f.save()
    batch = sending.queue_batch(_drafts())
    messages.success(request, "Applications queued. They will go out one by one with a short random delay.")
    return redirect("applications_progress", batch=batch)


def progress(request, batch):
    apps = Application.objects.filter(batch_id=batch)
    if not apps.exists():
        return redirect("applications_history")
    return render(request, "applications/progress.html", {
        "batch": batch, "page_hint": "Your emails are being sent one at a time. You can leave this page; sending continues.",
        **_progress_context(batch)})


@require_GET
def progress_partial(request, batch):
    return render(request, "applications/_progress.html", {"batch": batch, **_progress_context(batch)})


def _progress_context(batch):
    apps = list(Application.objects.filter(batch_id=batch).order_by("scheduled_at", "id"))
    counts = {s: 0 for s, _ in Application.STATUS_CHOICES}
    for a in apps:
        counts[a.status] += 1
    active = counts[Application.QUEUED] + counts[Application.SENDING]
    limit_hit = counts[Application.DEFERRED] > 0
    config = SMTPConfig.peek()
    next_eta = min((a.scheduled_at for a in apps if a.status == Application.QUEUED and a.scheduled_at), default=None)
    return {"apps": apps, "counts": counts, "active": active, "total": len(apps), "limit_hit": limit_hit,
            "limit": config.daily_limit if config else 0, "next_eta": next_eta}


STATUS_FILTERS = [s for s, _ in Application.STATUS_CHOICES if s != Application.DRAFT]


def history(request):
    status = request.GET.get("status", "")
    qs = Application.objects.exclude(status=Application.DRAFT)
    counts = dict(qs.values_list("status").annotate(n=Count("id")))
    if status in STATUS_FILTERS:
        qs = qs.filter(status=status)
    return render(request, "applications/history.html", {
        "apps": qs, "status": status, "counts": counts, "statuses": Application.STATUS_CHOICES[1:],
        "any_active": Application.objects.filter(status__in=Application.ACTIVE).exists(),
        "page_hint": "Every application you queued. Retry failed ones, or export the list as CSV.",
    })


@require_POST
def retry(request, pk):
    app = get_object_or_404(Application, pk=pk)
    if not app.can_retry:
        messages.error(request, "Only failed or deferred applications can be retried.")
    else:
        sending.requeue(app)
        messages.success(request, "Queued for another attempt.")
    return redirect(request.POST.get("next") if request.POST.get("next", "").startswith("/applications/") else "applications_history")


def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection in exported cells."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@require_GET
def export_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="applications.csv"'
    w = csv.writer(response)
    w.writerow(["Job", "Company", "Recipient", "Status", "Scheduled", "Sent", "Retries", "Error",
                "Job URL", "Subject", "Body"])
    for a in Application.objects.exclude(status=Application.DRAFT).order_by("-created_at"):
        w.writerow([_csv_safe(v) for v in (
            a.job_title, a.company, a.recipient, a.get_status_display(),
            a.scheduled_at.isoformat() if a.scheduled_at else "", a.sent_at.isoformat() if a.sent_at else "",
            a.retry_count, a.error, a.job_url, a.subject, a.body)])
    return response
