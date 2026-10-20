"""Fernet encryption for secrets stored at rest (the SMTP password)."""
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class DecryptionError(Exception):
    """The stored secret cannot be decrypted (the Fernet key changed or data is corrupt)."""


def _load_key() -> bytes:
    env_key = os.environ.get("FERNET_KEY", "").strip()
    if env_key:
        return env_key.encode()
    path = settings.DATA_DIR / ".fernet_key"
    if path.exists():
        return path.read_text().strip().encode()
    key = Fernet.generate_key()
    # Create with 0600 from the start so the key is never world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(_load_key())


def reset_cache() -> None:
    """For tests that swap the key."""
    _fernet.cache_clear()


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise DecryptionError("Stored value cannot be decrypted with the current key.") from exc
