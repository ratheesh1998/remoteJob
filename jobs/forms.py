import re

from django import forms

from core.forms import StyledFormMixin

from . import post_emails
from .models import SOURCE_LABELS, SearchSettings
from .sources import ALL_SOURCES

SORT_CHOICES = [
    ("score", "Best match"), ("newest", "Newest"), ("company", "Company A-Z"), ("title", "Title A-Z"),
]
SORT_FIELDS = {
    "score": ("-match_score", "-posted_at", "id"),
    "newest": ("-posted_at", "-match_score", "id"),
    "company": ("company", "-match_score", "id"),
    "title": ("title", "-match_score", "id"),
}


class JobFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(required=False, max_length=100, label="Search")
    source = forms.ChoiceField(
        required=False, label="Source",
        choices=[("", "All sources")] + [(s.name, s.label) for s in ALL_SOURCES.values()] + [("linkedin_paste", SOURCE_LABELS["linkedin_paste"])])
    posted = forms.ChoiceField(
        required=False, label="Posted",
        choices=[("", "Any time"), ("7", "Last 7 days"), ("30", "Last 30 days")])
    min_score = forms.IntegerField(required=False, min_value=0, max_value=100, label="Min score")
    has_email = forms.BooleanField(required=False, label="Has email")
    sort = forms.ChoiceField(required=False, choices=SORT_CHOICES, label="Sort")


class PasteForm(StyledFormMixin, forms.Form):
    text = forms.CharField(
        label="Post text", widget=forms.Textarea(attrs={"rows": 12, "placeholder": "Paste the text of a LinkedIn hiring post here.\nSeparate several posts with a line containing only ---"}),
        max_length=60000)
    url = forms.URLField(required=False, label="Post URL (optional)", assume_scheme="https",
                         help_text="Used when you paste a single post.")


class ParsedPostForm(StyledFormMixin, forms.Form):
    keep = forms.BooleanField(required=False, initial=True, label="Add this job")
    email = forms.EmailField(label="Application email")
    title = forms.CharField(max_length=500, label="Job title")
    company = forms.CharField(max_length=300, required=False, label="Company", help_text="Leave blank if the post does not say.")
    skills = forms.CharField(required=False, max_length=500, label="Skills", help_text="Comma-separated. Used for the match score.")
    url = forms.URLField(required=False, label="Post URL", assume_scheme="https")
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}), max_length=8000, label="Post text")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if self.data.get(self.add_prefix("keep")) is None:       # unticked rows are not validated further
            return email
        reason = post_emails.rejection_reason(email)
        if reason:
            raise forms.ValidationError(f"This address cannot be used ({reason}).")
        if not post_emails.deliverable(email):
            raise forms.ValidationError("This domain has no mail server (no MX record), so mail would bounce.")
        return email

    def clean_title(self):
        title = " ".join(self.cleaned_data["title"].split())
        if not title:
            raise forms.ValidationError("Enter the job title.")
        return title

    def clean_skills(self):
        return [s.strip() for s in re.split(r"[,;\n]", self.cleaned_data["skills"]) if s.strip()][:15]


class SearchSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SearchSettings
        fields = ["provider", "max_queries_per_run"]
        labels = {"provider": "Search provider", "max_queries_per_run": "Max queries per search"}
        help_texts = {"max_queries_per_run": "Each query uses one request from your API quota. Results are cached for 24 hours."}

    def clean_max_queries_per_run(self):
        n = self.cleaned_data["max_queries_per_run"]
        if not 1 <= n <= 20:
            raise forms.ValidationError("Choose a number between 1 and 20.")
        return n
