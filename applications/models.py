import uuid

from django.db import models
from django.utils import timezone


class Application(models.Model):
    DRAFT, QUEUED, SENDING, SENT, FAILED, DEFERRED = "draft", "queued", "sending", "sent", "failed", "deferred"
    STATUS_CHOICES = [
        (DRAFT, "Draft"), (QUEUED, "Queued"), (SENDING, "Sending"), (SENT, "Sent"),
        (FAILED, "Failed"), (DEFERRED, "Not sent (daily limit)"),
    ]
    ACTIVE = (QUEUED, SENDING)

    # SET_NULL: history survives a new search. The job details below are copied at creation.
    job = models.ForeignKey("jobs.Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="applications")
    job_key = models.CharField(max_length=40, db_index=True)
    job_title = models.CharField(max_length=500)
    company = models.CharField(max_length=300)
    job_url = models.URLField(max_length=1000, blank=True)
    recipient = models.EmailField()

    subject = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    draft_ready = models.BooleanField(default=False)    # False while the LLM is still personalizing
    personalized = models.BooleanField(default=False)   # True when the LLM wrote the middle paragraph
    draft_note = models.CharField(max_length=300, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    batch_id = models.UUIDField(null=True, blank=True, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    sent_message = models.TextField(blank=True)   # the exact email as sent (headers + body + attachment name)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # Never two applications to the same recipient for the same job.
            models.UniqueConstraint(fields=["recipient", "job_key"], name="unique_recipient_per_job"),
        ]

    def save(self, *args, **kwargs):
        self.recipient = (self.recipient or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def can_retry(self) -> bool:
        return self.status in (self.FAILED, self.DEFERRED)

    def __str__(self):
        return f"{self.job_title} -> {self.recipient} ({self.status})"
