from django import forms

from core.forms import StyledFormMixin

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
        choices=[("", "All sources")] + [(s.name, s.label) for s in ALL_SOURCES.values()])
    posted = forms.ChoiceField(
        required=False, label="Posted",
        choices=[("", "Any time"), ("7", "Last 7 days"), ("30", "Last 30 days")])
    min_score = forms.IntegerField(required=False, min_value=0, max_value=100, label="Min score")
    has_email = forms.BooleanField(required=False, label="Has email")
    sort = forms.ChoiceField(required=False, choices=SORT_CHOICES, label="Sort")
