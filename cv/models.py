from django.db import models
from django.utils import timezone

from core.models import SingletonModel


class CVProfile(SingletonModel):
    file = models.FileField(upload_to="cv/")
    original_filename = models.CharField(max_length=255, blank=True)
    raw_text = models.TextField(blank=True)

    full_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    designation = models.CharField(max_length=255, blank=True)
    alternate_titles = models.JSONField(default=list, blank=True)
    skills = models.JSONField(default=list, blank=True)
    years_experience = models.FloatField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    summary = models.TextField(blank=True)

    extraction_method = models.CharField(max_length=20, blank=True)  # "llm" | "heuristic"
    extraction_note = models.TextField(blank=True)

    confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name="cvprofile_singleton")]

    def confirm(self):
        self.confirmed = True
        self.confirmed_at = timezone.now()
        self.save()

    @property
    def search_titles(self) -> list[str]:
        titles = [self.designation] + list(self.alternate_titles or [])
        seen, out = set(), []
        for t in titles:
            key = t.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(t.strip())
        return out
