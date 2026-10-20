"""Search orchestration: start, record per-source results, merge once every source has reported."""
import logging

from celery import group
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from cv.models import CVProfile

from .dedupe import dedupe_jobs, key_for
from .models import Job, JobSearch, SourceResult
from .scoring import is_relevant, match_score
from .sources import enabled_sources
from .types import JobData

log = logging.getLogger(__name__)
STALE_AFTER_SECONDS = 300


def start_search(profile: CVProfile) -> JobSearch:
    from applications.models import Application

    from .tasks import fetch_source

    # Only the latest search is kept. Applications keep their own copy of the job details.
    Application.objects.filter(status=Application.DRAFT).delete()
    pinned_ids = list(Job.objects.filter(pinned=True).values_list("id", flat=True))   # pasted posts survive
    search = JobSearch.objects.create(
        designation=profile.designation, titles=profile.search_titles, skills=list(profile.skills or []),
    )
    Job.objects.filter(pk__in=pinned_ids).update(search=search)
    JobSearch.objects.exclude(pk=search.pk).delete()
    sources = enabled_sources()
    SourceResult.objects.bulk_create(
        [SourceResult(search=search, name=s.name, label=s.label) for s in sources]
    )
    # Parallel fan-out; each task isolates its own failures.
    group(fetch_source.s(search.id, s.name) for s in sources).apply_async()
    return search


def record_source_result(search_id: int, name: str, jobs: list[JobData] | None, error: str = "", note: str = "") -> None:
    """Called by the task. Marks one source done and merges when it was the last one."""
    SourceResult.objects.filter(search_id=search_id, name=name).update(
        status=SourceResult.FAILED if error else SourceResult.OK,
        error=error[:1000],
        note=note[:200],
        count=len(jobs or []),
        payload=[j.to_dict() for j in jobs or []],
        finished_at=timezone.now(),
    )
    finalize_if_complete(search_id)


def finalize_if_complete(search_id: int) -> None:
    if SourceResult.objects.filter(search_id=search_id, status=SourceResult.PENDING).exists():
        return
    # Atomic claim: only one worker performs the merge.
    claimed = JobSearch.objects.filter(pk=search_id, status=JobSearch.RUNNING).update(status=JobSearch.MERGING)
    if claimed:
        merge_results(search_id)


def merge_results(search_id: int) -> None:
    search = JobSearch.objects.get(pk=search_id)
    sources = list(search.sources.all())
    all_jobs: list[JobData] = []
    for src in sources:
        if src.status == SourceResult.OK:
            all_jobs.extend(JobData.from_dict(d) for d in src.payload)

    jobs = prepare_jobs(all_jobs, search.designation, search.titles[1:], search.skills)

    with transaction.atomic():
        already = set(search.jobs.values_list("job_key", flat=True))    # pinned (pasted) jobs already here
        jobs = [j for j in jobs if key_for(j) not in already]
        Job.objects.bulk_create([
            Job(search=search, title=j.title, company=j.company, region=j.region, description=j.description,
                salary=j.salary, source=j.source, url=j.url, posted_at=j.posted_at,
                application_email=j.application_email or "", match_score=j.match_score,
                job_key=key_for(j))
            for j in jobs
        ])
        per_source: dict[str, int] = {}
        for j in jobs:
            per_source[j.source] = per_source.get(j.source, 0) + 1
        for src in sources:
            src.count = per_source.get(src.name, 0) if src.status == SourceResult.OK else 0
            src.payload = []
            src.save(update_fields=["count", "payload"])
        search.total_jobs = search.jobs.count()
        search.status = JobSearch.DONE
        search.finished_at = timezone.now()
        search.save(update_fields=["total_jobs", "status", "finished_at"])


def prepare_jobs(jobs: list[JobData], designation: str, alternates: list[str], skills: list[str]) -> list[JobData]:
    """Score, drop irrelevant jobs, dedupe and sort (best match first, then newest)."""
    for j in jobs:
        j.match_score = match_score(j, designation, alternates, skills)
    relevant = [j for j in jobs
                if is_relevant(j, designation, alternates, skills, settings.MIN_MATCH_SCORE_TO_KEEP)]
    # Best-scored duplicate wins, so sort before deduping.
    relevant.sort(key=lambda j: (-j.match_score, -(j.posted_at.timestamp() if j.posted_at else 0)))
    return dedupe_jobs(relevant)


def expire_stale_search(search: JobSearch) -> None:
    """If a worker died mid-search, give up on the missing sources instead of spinning forever."""
    if not search.is_running:
        return
    if (timezone.now() - search.created_at).total_seconds() < STALE_AFTER_SECONDS:
        return
    SourceResult.objects.filter(search=search, status=SourceResult.PENDING).update(
        status=SourceResult.FAILED, error="Timed out (is the Celery worker running?)", finished_at=timezone.now())
    finalize_if_complete(search.id)
