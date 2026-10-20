from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import SingletonModel


POST_SOURCES = ("linkedin_posts", "linkedin_paste")
SOURCE_LABELS = {"linkedin_posts": "LinkedIn post", "linkedin_paste": "LinkedIn post (pasted)",
                 "linkedin": "LinkedIn", "remotive": "Remotive", "remoteok": "RemoteOK",
                 "himalayas": "Himalayas", "weworkremotely": "We Work Remotely",
                 "adzuna": "Adzuna", "jsearch": "JSearch"}


def default_search_provider() -> str:
    return settings.SEARCH_PROVIDER if settings.SEARCH_PROVIDER in ("brave", "google") else "brave"


def default_max_queries() -> int:
    return settings.SEARCH_MAX_QUERIES


class JobSearch(models.Model):
    RUNNING, MERGING, DONE = "running", "merging", "done"
    STATUS_CHOICES = [(RUNNING, "Running"), (MERGING, "Merging"), (DONE, "Done")]

    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RUNNING)
    # Snapshot of the confirmed CV details used for this search.
    designation = models.CharField(max_length=255)
    titles = models.JSONField(default=list)
    skills = models.JSONField(default=list)
    total_jobs = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_running(self) -> bool:
        return self.status in (self.RUNNING, self.MERGING)


class SourceResult(models.Model):
    PENDING, OK, FAILED = "pending", "ok", "failed"
    STATUS_CHOICES = [(PENDING, "Pending"), (OK, "OK"), (FAILED, "Failed")]

    search = models.ForeignKey(JobSearch, on_delete=models.CASCADE, related_name="sources")
    name = models.CharField(max_length=30)
    label = models.CharField(max_length=60)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    note = models.CharField(max_length=200, blank=True)    # e.g. "2 of 6 queries failed"
    payload = models.JSONField(default=list, blank=True)   # raw normalized jobs until the merge step
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["search", "name"], name="unique_source_per_search")]
        ordering = ["id"]


class Job(models.Model):
    search = models.ForeignKey(JobSearch, on_delete=models.CASCADE, related_name="jobs")
    title = models.CharField(max_length=500)
    company = models.CharField(max_length=300)
    region = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)   # plain text only; HTML is stripped at ingest
    salary = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=30)
    url = models.URLField(max_length=1000, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    application_email = models.EmailField(blank=True)
    match_score = models.PositiveSmallIntegerField(default=0)
    job_key = models.CharField(max_length=40, db_index=True)   # stable hash of company + title (posts: email + URL)
    pinned = models.BooleanField(default=False)   # pasted posts: carried into every new search

    class Meta:
        ordering = ["-match_score", "-posted_at"]
        constraints = [models.UniqueConstraint(fields=["search", "job_key"], name="unique_job_per_search")]

    @property
    def is_post(self) -> bool:
        return self.source in POST_SOURCES

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source) or self.source

    def __str__(self):
        return f"{self.title} @ {self.company}"


class SearchSettings(SingletonModel):
    """Which search API finds LinkedIn posts. API keys live in .env only and are never stored or shown."""
    BRAVE, GOOGLE = "brave", "google"
    PROVIDER_CHOICES = [(BRAVE, "Brave Search API"), (GOOGLE, "Google Programmable Search (ends 2027-01-01)")]

    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES, default=default_search_provider)
    max_queries_per_run = models.PositiveSmallIntegerField(default=default_max_queries)
    quota_remaining = models.IntegerField(null=True, blank=True)
    quota_limit = models.IntegerField(null=True, blank=True)
    quota_checked_at = models.DateTimeField(null=True, blank=True)
    google_day = models.DateField(null=True, blank=True)
    google_used_today = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name="searchsettings_singleton")]


class SearchCache(models.Model):
    """Search API results kept for 24 hours so repeated searches do not spend quota. Wiped by Delete all data."""
    provider = models.CharField(max_length=10)
    query_hash = models.CharField(max_length=64, unique=True)
    query = models.TextField()
    results = models.JSONField(default=list)     # [{title, url, snippet, posted_at}]
    created_at = models.DateTimeField(default=timezone.now)
