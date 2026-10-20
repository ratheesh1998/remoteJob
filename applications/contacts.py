"""Who must not be emailed: the do-not-contact list and the one-email-per-address-per-30-days cap."""
import re
from datetime import timedelta

from django.conf import settings
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import Application, DoNotContact

_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def normalize_entry(raw: str) -> str:
    """An address (a@b.com) or a domain (b.com) in canonical lowercase form, else ValidationError."""
    value = (raw or "").strip().lower()
    if "@" in value and not value.startswith("@"):
        validate_email(value)
        return value
    value = value.lstrip("@").removeprefix("www.")
    if not _DOMAIN_RE.match(value):
        raise ValidationError("Enter an email address (jane@acme.com) or a domain (acme.com).")
    return value


class ContactGuard:
    """Loads the do-not-contact list and recent contacts once, so a whole page or batch costs two queries."""

    def __init__(self):
        self.emails, self.domains = set(), set()
        for value in DoNotContact.objects.values_list("value", flat=True):
            (self.emails if "@" in value else self.domains).add(value)
        cutoff = timezone.now() - timedelta(days=settings.CONTACT_CAP_DAYS)
        self.recent = set(
            Application.objects.filter(
                Q(status=Application.SENT, sent_at__gte=cutoff) | Q(status__in=Application.ACTIVE)
            ).values_list("recipient", flat=True))

    def on_list(self, email: str) -> bool:
        email = (email or "").lower()
        domain = email.rpartition("@")[2]
        return email in self.emails or any(domain == d or domain.endswith("." + d) for d in self.domains)

    def block_reason(self, email: str) -> str | None:
        email = (email or "").strip().lower()
        if not email:
            return None
        if self.on_list(email):
            return "On your do-not-contact list"
        if email in self.recent:
            return f"Already emailed in the last {settings.CONTACT_CAP_DAYS} days"
        return None

    def claim(self, email: str) -> None:
        """Remember an address queued in this batch so a second job for it is held back."""
        self.recent.add((email or "").strip().lower())


def send_block_reason(app: Application) -> str | None:
    """Checked again at send time: the list or the history may have changed since the email was queued."""
    guard = ContactGuard()
    if guard.on_list(app.recipient):
        return "On your do-not-contact list"
    cutoff = timezone.now() - timedelta(days=settings.CONTACT_CAP_DAYS)
    if Application.objects.filter(recipient=app.recipient, status=Application.SENT, sent_at__gte=cutoff).exclude(pk=app.pk).exists():
        return f"Already emailed in the last {settings.CONTACT_CAP_DAYS} days"
    return None


def add_to_list(value: str, note: str = "") -> DoNotContact:
    entry, _ = DoNotContact.objects.get_or_create(value=normalize_entry(value), defaults={"note": note[:200]})
    cancel_queued_for(entry.value)
    return entry


def cancel_queued_for(value: str) -> int:
    """Stop anything still waiting to go out to a newly blocked address or domain."""
    n = 0
    for app in Application.objects.filter(status__in=(Application.QUEUED, Application.DRAFT)):
        guard = ContactGuard.__new__(ContactGuard)
        guard.emails, guard.domains, guard.recent = ({value} if "@" in value else set()), (set() if "@" in value else {value}), set()
        if guard.on_list(app.recipient):
            if app.status == Application.DRAFT:
                app.delete()
            else:
                app.status, app.error = Application.CANCELLED, "On your do-not-contact list"
                app.save(update_fields=["status", "error"])
            n += 1
    return n
