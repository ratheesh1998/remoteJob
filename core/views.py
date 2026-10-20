import json
import logging

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import mailer
from .forms import DeleteAllForm, SMTPConfigForm
from .models import SMTPConfig
from .services import delete_all_data
from .steps import build_steps

log = logging.getLogger(__name__)

PRESETS = {
    "gmail": {"host": "smtp.gmail.com", "port": 465, "security": "ssl"},
    "outlook": {"host": "smtp-mail.outlook.com", "port": 587, "security": "starttls"},
}


def index(request):
    steps, _ = build_steps()
    for step in steps:
        if step.current:
            return redirect(step.url_name)
    return redirect("job_search")


@require_http_methods(["GET", "POST"])
def smtp_setup(request):
    config = SMTPConfig.load()
    test_result = None
    if request.method == "POST":
        form = SMTPConfigForm(request.POST, instance=config)
        if form.is_valid():
            config = form.save()
            if request.POST.get("action") == "test":
                test_result = _run_test(config)
                config.refresh_from_db()
                form = SMTPConfigForm(instance=config)
            else:
                messages.success(request, "Settings saved. Send a test email to verify them.")
                return redirect("smtp_setup")
    else:
        form = SMTPConfigForm(instance=config)
    return render(request, "core/smtp.html", {
        "form": form, "config": config, "test_result": test_result, "presets": {k: json.dumps(v) for k, v in PRESETS.items()},
        "page_hint": "Enter your SMTP details, then send a test email. Everything else unlocks once the test succeeds.",
    })


def _run_test(config: SMTPConfig) -> dict:
    try:
        mailer.send_test_email(config)
    except Exception as exc:  # report the real error, never the password
        failure = mailer.classify_error(exc, config)
        config.mark_unverified(failure.message)
        log.warning("SMTP test failed: %s", failure.message)
        return {"ok": False, "message": failure.message, "hint": failure.hint}
    config.mark_verified()
    return {"ok": True, "message": f"Test email sent to {config.from_email}. Check your inbox."}


def help_page(request):
    return render(request, "core/help.html", {
        "page_hint": "Answers to the common questions. Read the responsible-use note before sending.",
    })


@require_http_methods(["GET", "POST"])
def delete_all(request):
    form = DeleteAllForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        delete_all_data()
        messages.success(request, "All your data was deleted.")
        return redirect("smtp_setup")
    return render(request, "core/delete_all.html", {
        "form": form,
        "page_hint": "This permanently deletes your CV, jobs, applications and email settings.",
    })
