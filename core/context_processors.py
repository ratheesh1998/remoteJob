from django.conf import settings
from django.http.request import split_domain_port

from .steps import build_steps


def app_context(request):
    domain, _ = split_domain_port(request.get_host())
    match = getattr(request, "resolver_match", None)
    current = match.url_name if match else None
    steps, _state = build_steps(current)
    return {
        "steps": steps,
        "non_local_host": domain.lower() not in settings.LOCAL_HOSTNAMES,
    }
