from cryptography.fernet import Fernet
from django.db import connection

from core import crypto
from core.models import SMTPConfig

from .base import AppTestCase


class EncryptionTests(AppTestCase):
    def test_roundtrip(self):
        token = crypto.encrypt("hunter2-app-pass")
        self.assertNotIn("hunter2", token)
        self.assertEqual(crypto.decrypt(token), "hunter2-app-pass")

    def test_password_not_stored_in_plaintext(self):
        cfg = self.make_smtp()
        with connection.cursor() as cur:
            cur.execute("SELECT password_encrypted FROM core_smtpconfig")
            stored = cur.fetchone()[0]
        self.assertNotIn("secret-app-password", stored)
        self.assertEqual(SMTPConfig.load().get_password(), "secret-app-password")

    def test_wrong_key_raises_clear_error(self):
        token = crypto.encrypt("x")
        original = crypto._load_key
        crypto._load_key = lambda: Fernet.generate_key()
        crypto.reset_cache()
        try:
            with self.assertRaises(crypto.DecryptionError):
                crypto.decrypt(token)
        finally:
            crypto._load_key = original
            crypto.reset_cache()

    def test_password_never_in_repr_or_form_page(self):
        cfg = self.make_smtp()
        self.assertNotIn("secret-app-password", repr(cfg))
        self.assertNotIn(cfg.password_encrypted, repr(cfg))
        html = self.client.get("/smtp/").content.decode()
        self.assertNotIn("secret-app-password", html)
        self.assertNotIn(cfg.password_encrypted, html)

    def test_singleton(self):
        a = SMTPConfig.load()
        b = SMTPConfig(host="other")
        b.save()
        self.assertEqual(SMTPConfig.objects.count(), 1)
        self.assertEqual(b.pk, 1)
        with self.assertRaises(RuntimeError):
            a.delete()
