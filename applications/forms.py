from django import forms

from core.forms import StyledFormMixin

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
