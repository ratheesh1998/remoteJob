from django.db import models
from django.utils import timezone


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
    url = models.URLField(max_length=1000)
    posted_at = models.DateTimeField(null=True, blank=True)
    application_email = models.EmailField(blank=True)
    match_score = models.PositiveSmallIntegerField(default=0)
    job_key = models.CharField(max_length=40, db_index=True)   # stable hash of company + title

    class Meta:
        ordering = ["-match_score", "-posted_at"]
        constraints = [models.UniqueConstraint(fields=["search", "job_key"], name="unique_job_per_search")]

    def __str__(self):
        return f"{self.title} @ {self.company}"
