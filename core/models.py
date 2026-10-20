from django.db import models
from django.utils import timezone

from . import crypto


class SingletonModel(models.Model):
    """Exactly one row, always pk=1. Enforced in code and by a CHECK constraint."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError(
            f"{type(self).__name__} is a singleton; use core.services.delete_all_data()."
        )

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def peek(cls):
        """The row if it exists, without creating it."""
        return cls.objects.filter(pk=1).first()


class SMTPConfig(SingletonModel):
    SSL = "ssl"
    STARTTLS = "starttls"
    SECURITY_CHOICES = [(SSL, "SSL/TLS"), (STARTTLS, "STARTTLS")]

    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=465)
    security = models.CharField(max_length=10, choices=SECURITY_CHOICES, default=SSL)
    username = models.CharField(max_length=255, blank=True)
    password_encrypted = models.TextField(blank=True)
    from_name = models.CharField(max_length=255, blank=True)
    from_email = models.EmailField(blank=True)
    daily_limit = models.PositiveIntegerField(default=40)
    verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name="smtpconfig_singleton")]

    # The password is write-only: nothing renders or logs it.
    def set_password(self, raw: str) -> None:
        self.password_encrypted = crypto.encrypt(raw) if raw else ""

    def get_password(self) -> str:
        return crypto.decrypt(self.password_encrypted) if self.password_encrypted else ""

    @property
    def has_password(self) -> bool:
        return bool(self.password_encrypted)

    @property
    def is_complete(self) -> bool:
        return bool(self.host and self.port and self.username and self.has_password and self.from_email)

    @property
    def is_ready(self) -> bool:
        return self.verified and self.is_complete

    def mark_verified(self) -> None:
        self.verified = True
        self.verified_at = timezone.now()
        self.last_error = ""
        self.save()

    def mark_unverified(self, error: str = "") -> None:
        self.verified = False
        self.verified_at = None
        self.last_error = error
        self.save()

    def __str__(self):  # never include the password
        return f"SMTPConfig({self.host}:{self.port}, verified={self.verified})"

    __repr__ = __str__
