from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.forms import formset_factory
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from applications.contacts import ContactGuard
from applications.models import Application
from cv.models import CVProfile

from . import external, paste, services
from .dedupe import post_key
from .forms import SORT_FIELDS, JobFilterForm, ParsedPostForm, PasteForm, SearchSettingsForm
from .models import POST_SOURCES, Job, JobSearch, SearchCache, SearchSettings
from .scoring import match_score
from .search_providers import PROVIDERS, configured_provider
from .sources import ALL_SOURCES, enabled_sources
from .types import JobData

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
        "linkedin_links": external.linkedin_links(profile.search_titles),
        "posts_panel": _posts_panel(search),
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
    guard = ContactGuard()      # do-not-contact list + the 30-day cap: those rows cannot be selected
    email_ids = [j for j, k, e in qs.exclude(application_email="").values_list("id", "job_key", "application_email")
                 if k not in applied and not guard.block_reason(e)]
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    for job in page:
        job.is_applied = job.job_key in applied
        job.blocked_reason = guard.block_reason(job.application_email) if job.application_email else None
    params = request.GET.copy()
    params.pop("page", None)
    ctx.update(page_obj=page, total_matching=page.paginator.count, applied=applied,
               has_posts=search.jobs.filter(source__in=POST_SOURCES).exists(),
               email_ids=email_ids, base_query=params.urlencode())
    return ctx


ParsedPostFormSet = formset_factory(ParsedPostForm, extra=0)


@require_http_methods(["GET", "POST"])
def job_paste(request):
    """Add from a LinkedIn post: paste text, review what was parsed, then save as jobs."""
    profile = _confirmed_profile(request)
    if profile is None:
        return redirect("cv_confirm" if CVProfile.peek() else "cv_upload")
    ctx = {"profile": profile, "paste_form": PasteForm(), "formset": None,
           "page_hint": "Paste a LinkedIn hiring post you found yourself. We pull out the email, title and company for you to check."}

    if request.method == "POST" and "form-TOTAL_FORMS" in request.POST:       # step 2: save the reviewed rows
        formset = ParsedPostFormSet(request.POST)
        if formset.is_valid():
            created = _save_pasted(formset, profile)
            if created:
                messages.success(request, f"Added {created} job{'s' if created != 1 else ''} from pasted posts. They are in your results as 'LinkedIn post (pasted)'.")
                return redirect("job_search")
            messages.info(request, "Nothing was added: no rows were ticked, or they were already in your list.")
            return redirect("job_paste")
        ctx["formset"] = formset
    elif request.method == "POST":                                            # step 1: parse
        form = PasteForm(request.POST)
        if form.is_valid():
            posts = paste.split_posts(form.cleaned_data["text"])
            url = form.cleaned_data["url"] if len(posts) == 1 else ""
            if form.cleaned_data["url"] and len(posts) != 1:
                messages.info(request, "The URL field applies to a single post, so it was ignored. Add each URL in the review step.")
            parsed = [paste.parse_post(t) for t in posts]
            ctx["formset"] = ParsedPostFormSet(initial=[{
                "keep": bool(p.email), "email": p.email, "title": p.title, "company": p.company,
                "skills": ", ".join(p.skills), "url": url, "text": p.text} for p in parsed])
            ctx["notes"] = [p.note for p in parsed]
            ctx["other_emails"] = [p.other_emails for p in parsed]
        else:
            ctx["paste_form"] = form
    if ctx["formset"] is not None:
        ctx["rows"] = list(zip(ctx["formset"].forms, ctx.get("notes") or [""] * len(ctx["formset"].forms),
                               ctx.get("other_emails") or [[]] * len(ctx["formset"].forms)))
    return render(request, "jobs/paste.html", ctx)


def _save_pasted(formset, profile) -> int:
    search = JobSearch.objects.first() or JobSearch.objects.create(
        designation=profile.designation, titles=profile.search_titles, skills=list(profile.skills or []),
        status=JobSearch.DONE, finished_at=timezone.now())
    created = 0
    for form in formset:
        c = form.cleaned_data
        if not c.get("keep"):
            continue
        key = post_key(c["email"], c["url"], c["text"])
        score = match_score(JobData(title=c["title"], company=c["company"] or "Unknown", url=c["url"], source="linkedin_paste",
                                    description=c["text"], tags=c["skills"]),
                            profile.designation, profile.alternate_titles or [], profile.skills or [])
        _, was_created = Job.objects.get_or_create(search=search, job_key=key, defaults=dict(
            title=c["title"], company=c["company"] or "Unknown", region="Remote", description=c["text"],
            source="linkedin_paste", url=c["url"], application_email=c["email"], match_score=score, pinned=True))
        created += was_created
    search.total_jobs = search.jobs.count()
    search.save(update_fields=["total_jobs"])
    return created


def _posts_panel(search) -> dict:
    """What to tell the user about LinkedIn posts: not configured, found some, or found none."""
    cfg = SearchSettings.load()
    provider = configured_provider(cfg.provider)
    found = search.jobs.filter(source="linkedin_posts").count() if search and not search.is_running else None
    src = search.sources.filter(name="linkedin_posts").first() if search else None
    return {"configured": provider is not None, "provider": provider.label if provider else "",
            "found": found, "ran": bool(src and src.status != "pending"), "failed": bool(src and src.status == "failed"),
            "error": src.error if src else ""}


@require_http_methods(["GET", "POST"])
def search_settings(request):
    cfg = SearchSettings.load()
    form = SearchSettingsForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Search settings saved.")
        return redirect("search_settings")
    cfg.refresh_from_db()
    chosen = configured_provider(cfg.provider)
    return render(request, "jobs/search_settings.html", {
        "form": form, "cfg": cfg, "providers": list(PROVIDERS.values()), "active": chosen,
        "cache_rows": SearchCache.objects.count(),
        "page_hint": "Choose which official search API finds LinkedIn hiring posts. API keys are set in the .env file and are never shown here.",
    })
