from django.contrib import messages
from django.shortcuts import redirect

# Until SMTP is saved and verified only these are reachable.
GATE_EXEMPT_PREFIXES = ("/smtp/", "/help/", "/delete-all/", "/static/")


class SMTPGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(GATE_EXEMPT_PREFIXES):
            from .models import SMTPConfig

            smtp = SMTPConfig.peek()
            if not (smtp and smtp.is_ready):
                messages.info(request, "Set up and verify your email (SMTP) first. Nothing else works until then.")
                return redirect("smtp_setup")
        return self.get_response(request)
