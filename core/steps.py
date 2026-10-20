"""The 5-step progress stepper. A step is done, current, or locked."""
from dataclasses import dataclass

from django.urls import reverse


@dataclass
class Step:
    number: int
    label: str
    url_name: str
    done: bool = False
    current: bool = False   # the next step to do
    active: bool = False    # the page being viewed

    @property
    def locked(self) -> bool:
        return not self.done and not self.current

    @property
    def url(self) -> str:
        return reverse(self.url_name)


def compute_state():
    """Return completion flags. Imports are local to avoid app-loading cycles."""
    from applications.models import Application
    from cv.models import CVProfile
    from jobs.models import JobSearch

    from .models import SMTPConfig

    smtp = SMTPConfig.peek()
    cv = CVProfile.peek()
    smtp_done = bool(smtp and smtp.is_ready)
    cv_done = bool(cv and cv.raw_text)
    confirm_done = bool(cv and cv.confirmed)
    search_done = JobSearch.objects.filter(status=JobSearch.DONE).exists()
    send_done = Application.objects.exclude(status=Application.DRAFT).exists()
    return {
        "smtp": smtp_done, "cv": cv_done, "confirm": confirm_done,
        "search": search_done, "send": send_done,
    }


def build_steps(current_key: str | None = None):
    state = compute_state()
    spec = [
        ("smtp", "Email", "smtp_setup"),
        ("cv", "CV", "cv_upload"),
        ("confirm", "Confirm", "cv_confirm"),
        ("search", "Find jobs", "job_search"),
        ("send", "Review and send", "applications_review"),
    ]
    steps, unlocked = [], True
    first_open_assigned = False
    for i, (key, label, url_name) in enumerate(spec, start=1):
        done = state[key]
        step = Step(i, label, url_name, done=done, active=(url_name == current_key))
        # A step is reachable only if every earlier step is done.
        if not unlocked:
            step.done = False
        elif not done and not first_open_assigned:
            step.current = True
            first_open_assigned = True
        steps.append(step)
        unlocked = unlocked and done
    return steps, state
