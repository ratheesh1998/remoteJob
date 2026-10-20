"""SMTP helpers shared by the test-email button and the Celery sender."""
import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from django.conf import settings

from .crypto import DecryptionError
from .models import SMTPConfig


@dataclass
class SMTPFailure:
    message: str          # the real error, as reported
    hint: str             # what to try
    permanent: bool       # retrying will not help


def open_connection(config: SMTPConfig):
    """Return a logged-in smtplib connection. Caller must quit() it."""
    timeout = settings.SMTP_TIMEOUT
    context = ssl.create_default_context()
    if config.security == SMTPConfig.SSL:
        conn = smtplib.SMTP_SSL(config.host, config.port, timeout=timeout, context=context)
    else:
        conn = smtplib.SMTP(config.host, config.port, timeout=timeout)
        try:
            conn.ehlo()
            conn.starttls(context=context)
            conn.ehlo()
        except Exception:
            conn.close()
            raise
    try:
        conn.login(config.username, config.get_password())
    except Exception:
        conn.close()
        raise
    return conn


def build_message(config: SMTPConfig, to_addr: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((config.from_name, config.from_email)) if config.from_name else config.from_email
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send_test_email(config: SMTPConfig) -> None:
    msg = build_message(
        config,
        config.from_email,
        "RemoteApply: SMTP test",
        "This is a test email from RemoteApply. Your SMTP settings work.",
    )
    conn = open_connection(config)
    try:
        conn.send_message(msg)
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def classify_error(exc: Exception, config: SMTPConfig | None = None) -> SMTPFailure:
    """Turn an exception into the real message plus an actionable hint."""
    host = (config.host if config else "").lower()
    gmail = "gmail" in host or "google" in host
    outlook = any(h in host for h in ("outlook", "office365", "hotmail", "live."))

    if isinstance(exc, DecryptionError):
        return SMTPFailure(str(exc), "The encryption key changed. Re-enter your SMTP password and save.", True)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        hint = "Check the username and password."
        if gmail:
            hint = ("Gmail needs an App Password (not your normal password) and 2-Step Verification "
                    "turned on. See the Help page.")
        elif outlook:
            hint = ("Outlook/Microsoft 365 may require an app password or have SMTP AUTH disabled "
                    "for the mailbox. See the Help page.")
        return SMTPFailure(_text(exc), hint, True)
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return SMTPFailure(_text(exc), "The recipient address was rejected by the server.", True)
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return SMTPFailure(_text(exc), "The From address is not allowed for this account. "
                                       "Use the account's own email address.", True)
    if isinstance(exc, smtplib.SMTPDataError):
        permanent = 500 <= exc.smtp_code < 600
        return SMTPFailure(_text(exc), "The server rejected the message content or you hit a sending limit.",
                           permanent)
    if isinstance(exc, ssl.SSLError):
        return SMTPFailure(
            _text(exc),
            "TLS handshake failed. Port 465 uses SSL/TLS; port 587 uses STARTTLS. "
            "Make sure the security option matches the port.", True)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return SMTPFailure("Connection timed out.", "Check host/port and that your network allows outgoing SMTP.", False)
    if isinstance(exc, (socket.gaierror,)):
        return SMTPFailure(_text(exc), "The host name could not be resolved. Check for typos.", True)
    if isinstance(exc, (ConnectionRefusedError, smtplib.SMTPConnectError)):
        return SMTPFailure(_text(exc), "Connection refused. Check host and port.", False)
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return SMTPFailure(_text(exc), "The server closed the connection. Often a port/security mismatch.", False)
    if isinstance(exc, smtplib.SMTPException):
        return SMTPFailure(_text(exc), "The SMTP server reported an error.", False)
    if isinstance(exc, OSError):
        return SMTPFailure(_text(exc), "Network error. Check host, port and your connection.", False)
    return SMTPFailure(f"{type(exc).__name__}: {exc}", "Unexpected error.", True)


def _text(exc: Exception) -> str:
    code = getattr(exc, "smtp_code", None)
    err = getattr(exc, "smtp_error", None)
    if code is not None and err is not None:
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return f"{code} {err}"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "; ".join(f"{r}: {c} {m.decode('utf-8', 'replace') if isinstance(m, bytes) else m}"
                         for r, (c, m) in exc.recipients.items())
    return str(exc) or type(exc).__name__
