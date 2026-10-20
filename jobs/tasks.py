import logging

from celery import shared_task
from django.conf import settings

from .models import JobSearch
from .services import expire_stale_search, record_source_result
from .sources import SearchQuery, get_source

log = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=settings.JOB_SOURCE_RETRIES,
    soft_time_limit=settings.JOB_SOURCE_TIMEOUT * 6,   # LinkedIn makes ~25 paced requests
    time_limit=settings.JOB_SOURCE_TIMEOUT * 6 + 15,
)
def fetch_source(self, search_id: int, source_name: str):
    """Fetch one source. A failing source reports 'failed' and never breaks the search."""
    search = JobSearch.objects.filter(pk=search_id).first()
    if search is None:   # search was replaced or all data deleted
        return {"source": source_name, "ok": False}
    query = SearchQuery(designation=search.designation, titles=search.titles, skills=search.skills)
    try:
        jobs, note = get_source(source_name).fetch_report(query)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            log.warning("Source %s failed (%s); retry %d", source_name, exc, self.request.retries + 1)
            raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))
        log.error("Source %s failed permanently: %s", source_name, exc)
        record_source_result(search_id, source_name, None, error=_short_error(exc))
        return {"source": source_name, "ok": False}
    record_source_result(search_id, source_name, jobs, note=note)
    return {"source": source_name, "ok": True, "count": len(jobs)}


def _short_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


@shared_task
def expire_stale_searches():
    """Beat watchdog: a search whose worker died must not stay 'running' forever."""
    for search in JobSearch.objects.filter(status=JobSearch.RUNNING):
        expire_stale_search(search)
