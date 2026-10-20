import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from core import crypto
from core.models import SMTPConfig


class AppTestCase(TestCase):
    """Isolated media dir, a throwaway Fernet key, eager Celery."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls._override = override_settings(MEDIA_ROOT=Path(cls._tmp.name), ANTHROPIC_API_KEY="")
        cls._override.enable()
        cls._key = Fernet.generate_key()
        cls._orig_load = crypto._load_key
        crypto._load_key = lambda: cls._key
        crypto.reset_cache()

    @classmethod
    def tearDownClass(cls):
        crypto._load_key = cls._orig_load
        crypto.reset_cache()
        cls._override.disable()
        cls._tmp.cleanup()
        super().tearDownClass()

    def make_smtp(self, verified=True, **kw):
        cfg = SMTPConfig.load()
        cfg.host = kw.get("host", "smtp.example.test")
        cfg.port = kw.get("port", 465)
        cfg.security = kw.get("security", "ssl")
        cfg.username = "me@example.test"
        cfg.from_email = "me@example.test"
        cfg.from_name = "Me"
        cfg.daily_limit = kw.get("daily_limit", 40)
        cfg.set_password("secret-app-password")
        cfg.verified = verified
        cfg.save()
        return cfg
