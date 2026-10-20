from django import forms

from core.forms import StyledFormMixin

from django.core.exceptions import ValidationError

from . import contacts
from .models import Application


class DraftForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Application
        fields = ["subject", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 15})}

    def clean_subject(self):
        subject = self.cleaned_data["subject"].strip()
        if not subject:
            raise forms.ValidationError("Subject cannot be empty.")
        if "\n" in subject or "\r" in subject:
            raise forms.ValidationError("Subject must be a single line.")
        return subject

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError("The email body cannot be empty.")
        if len(body) > 5000:
            raise forms.ValidationError("The email body is too long (max 5000 characters).")
        return body


class DoNotContactForm(StyledFormMixin, forms.Form):
    value = forms.CharField(max_length=254, label="Email address or domain", help_text="For example jane@acme.com, or acme.com to block everyone at that company.")
    note = forms.CharField(max_length=200, required=False, label="Note (optional)")

    def clean_value(self):
        return contacts.normalize_entry(self.cleaned_data["value"])
