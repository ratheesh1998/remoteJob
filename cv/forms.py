import re

from django import forms
from django.core.exceptions import ValidationError

from core.forms import StyledFormMixin

from . import parsing


class CVUploadForm(StyledFormMixin, forms.Form):
    file = forms.FileField(label="CV (PDF or DOCX, max 5 MB)")

    def clean_file(self):
        f = self.cleaned_data["file"]
        self.kind = parsing.validate_upload(f)
        return f


def split_list(value: str, limit: int, maxlen: int = 80) -> list[str]:
    out, seen = [], set()
    for part in re.split(r"[,\n;]", value or ""):
        part = " ".join(part.split())
        if part and part.lower() not in seen:
            if len(part) > maxlen:
                raise ValidationError(f"'{part[:20]}...' is too long (max {maxlen} characters).")
            seen.add(part.lower())
            out.append(part)
    if len(out) > limit:
        raise ValidationError(f"At most {limit} items allowed.")
    return out


class CVConfirmForm(StyledFormMixin, forms.Form):
    designation = forms.CharField(max_length=255, label="Designation (job title)")
    alternate_titles = forms.CharField(
        required=False, label="Alternate titles", widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Comma-separated. Other titles to search for.")
    skills = forms.CharField(
        required=False, label="Top skills", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Comma-separated. Used for matching and for personalising emails.")
    location = forms.CharField(max_length=255, required=False, label="Location")
    full_name = forms.CharField(max_length=255, label="Full name", help_text="Used in the subject and signature.")
    email = forms.EmailField(required=False, label="Email", help_text="Shown in the signature.")
    phone = forms.CharField(max_length=50, required=False, label="Phone", help_text="Shown in the signature.")

    def clean_designation(self):
        value = " ".join(self.cleaned_data["designation"].split())
        if not value:
            raise ValidationError("Enter your designation.")
        return value

    def clean_full_name(self):
        value = " ".join(self.cleaned_data["full_name"].split())
        if not value:
            raise ValidationError("Enter your name.")
        return value

    def clean_alternate_titles(self):
        return split_list(self.cleaned_data["alternate_titles"], 6)

    def clean_skills(self):
        return split_list(self.cleaned_data["skills"], 20)

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if phone and not re.fullmatch(r"[\d\s+().\-]{6,50}", phone):
            raise ValidationError("Enter a valid phone number.")
        return phone
