from django import forms

from .models import SMTPConfig


class SMTPConfigForm(forms.ModelForm):
    # Write-only: never rendered back (render_value=False) and never part of the model form fields.
    password = forms.CharField(
        label="Password / app password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = SMTPConfig
        fields = ["host", "port", "security", "username", "from_name", "from_email", "daily_limit"]
        labels = {
            "host": "SMTP host", "port": "Port", "security": "Security", "username": "Username",
            "from_name": "From name", "from_email": "From email", "daily_limit": "Daily send limit",
        }

    # Changing any of these invalidates a previous successful test.
    CONNECTION_FIELDS = ("host", "port", "security", "username", "from_email")

    def clean_host(self):
        host = self.cleaned_data["host"].strip()
        if not host or " " in host or "/" in host or "://" in host:
            raise forms.ValidationError("Enter a host name such as smtp.gmail.com (no http://, no spaces).")
        return host

    def clean_port(self):
        port = self.cleaned_data["port"]
        if not 1 <= port <= 65535:
            raise forms.ValidationError("Port must be between 1 and 65535.")
        return port

    def clean_daily_limit(self):
        limit = self.cleaned_data["daily_limit"]
        if not 1 <= limit <= 500:
            raise forms.ValidationError("Daily limit must be between 1 and 500.")
        return limit

    def clean_from_name(self):
        name = self.cleaned_data["from_name"].strip()
        if "\n" in name or "\r" in name:
            raise forms.ValidationError("Invalid name.")
        return name

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("password") and not self.instance.has_password:
            self.add_error("password", "Enter your password or app password.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        password = self.cleaned_data.get("password")
        changed = any(f in self.changed_data for f in self.CONNECTION_FIELDS) or bool(password)
        if password:
            obj.set_password(password)
        if changed:
            obj.verified = False
            obj.verified_at = None
        if commit:
            obj.save()
        return obj


class DeleteAllForm(forms.Form):
    confirm = forms.CharField(label='Type DELETE to confirm')

    def clean_confirm(self):
        if self.cleaned_data["confirm"].strip() != "DELETE":
            raise forms.ValidationError("Type DELETE exactly to confirm.")
        return "DELETE"
