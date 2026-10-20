from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from applications.models import Application
from cv.models import CVProfile

from . import services
from .forms import SORT_FIELDS, JobFilterForm
from .models import Job, JobSearch
from .sources import ALL_SOURCES, enabled_sources

PAGE_SIZE = 50


def _latest_search():
    return JobSearch.objects.first()


def _confirmed_profile(request):
    profile = CVProfile.peek()
    if not (profile and profile.confirmed):
        messages.info(request, "Confirm your CV details before searching for jobs.")
        return None
    return profile


def job_search(request):
    profile = _confirmed_profile(request)
    if profile is None:
        return redirect("cv_confirm" if CVProfile.peek() else "cv_upload")
    search = _latest_search()
    return render(request, "jobs/search.html", {
        "profile": profile, "search": search,
        "filter_form": JobFilterForm(request.GET or None),
        "optional_off": [s.label for s in ALL_SOURCES.values() if not s.is_enabled()],
        "page_hint": "Search remote jobs matching your CV, tick the ones you like, then review the emails.",
        **_results_context(request, search),
    })


@require_POST
def job_start(request):
    profile = _confirmed_profile(request)
    if profile is None:
        return redirect("cv_confirm" if CVProfile.peek() else "cv_upload")
    current = _latest_search()
    if current and current.is_running:
        messages.info(request, "A search is already running.")
        return redirect("job_search")
    services.start_search(profile)
    return redirect("job_search")


@require_GET
def job_progress(request):
    search = _latest_search()
    if search:
        services.expire_stale_search(search)
        search.refresh_from_db()
    response = render(request, "jobs/_progress.html", {"search": search, "sources": search.sources.all() if search else []})
    if search and not search.is_running:
        response["HX-Trigger"] = "searchDone"   # tells the results table to load
    return response


@require_GET
def job_results(request):
    if not CVProfile.peek():
        return HttpResponse(status=204)
    # Filter/paging requests only swap the table (so the filter inputs keep focus).
    template = "jobs/_table.html" if request.headers.get("HX-Target") == "table" else "jobs/_results.html"
    return render(request, template, _results_context(request, _latest_search()))


def _results_context(request, search):
    form = JobFilterForm(request.GET or None)
    ctx = {"search": search, "filter_form": form, "page_obj": None, "total_matching": 0,
           "email_ids": [], "base_query": "", "attributions": [s.attribution for s in enabled_sources() if s.attribution]}
    if not search or search.is_running:
        return ctx
    form.is_valid()
    data = form.cleaned_data if form.is_valid() else {}
    qs = search.jobs.all()
    if data.get("q"):
        q = data["q"]
        qs = qs.filter(Q(title__icontains=q) | Q(company__icontains=q) | Q(description__icontains=q))
    if data.get("source"):
        qs = qs.filter(source=data["source"])
    if data.get("posted"):
        qs = qs.filter(posted_at__gte=timezone.now() - timedelta(days=int(data["posted"])))
    if data.get("min_score"):
        qs = qs.filter(match_score__gte=data["min_score"])
    if data.get("has_email"):
        qs = qs.exclude(application_email="")
    qs = qs.order_by(*SORT_FIELDS[data.get("sort") or "score"])

    applied = set(Application.objects.exclude(status=Application.DRAFT).values_list("job_key", flat=True))
    email_ids = [j for j, k in qs.exclude(application_email="").values_list("id", "job_key") if k not in applied]
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    for job in page:
        job.is_applied = job.job_key in applied
    params = request.GET.copy()
    params.pop("page", None)
    ctx.update(page_obj=page, total_matching=page.paginator.count, applied=applied,
               email_ids=email_ids, base_query=params.urlencode())
    return ctx
