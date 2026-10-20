import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import extraction, parsing
from .forms import CVConfirmForm, CVUploadForm
from .models import CVProfile

log = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def cv_upload(request):
    profile = CVProfile.peek()
    form = CVUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["file"]
        try:
            text = parsing.extract_text(uploaded, form.kind)
        except ValidationError as exc:
            form.add_error("file", exc)
        else:
            result = extraction.extract_profile(text)
            _save_profile(uploaded, form.kind, text, result)
            messages.success(request, "CV uploaded. Check the extracted details below.")
            return redirect("cv_confirm")
    return render(request, "cv/upload.html", {
        "form": form, "profile": profile,
        "page_hint": "Upload your CV (PDF or DOCX). We read it to find your designation and skills.",
    })


def _save_profile(uploaded, kind: str, text: str, result: extraction.Extraction) -> CVProfile:
    profile = CVProfile.load()
    old_path = Path(profile.file.path) if profile.file else None
    d = result.data
    profile.original_filename = Path(uploaded.name).name[:255]
    profile.raw_text = text
    profile.full_name = d["full_name"] or ""
    profile.email = d["email"] or ""
    profile.phone = d["phone"] or ""
    profile.designation = d["designation"] or ""
    profile.alternate_titles = d["alternate_titles"]
    profile.skills = d["skills"]
    profile.years_experience = d["years_experience"]
    profile.location = d["location"] or ""
    profile.summary = d["summary"] or ""
    profile.extraction_method = result.method
    profile.extraction_note = result.note
    profile.confirmed = False          # re-uploading always resets confirmation
    profile.confirmed_at = None
    profile.uploaded_at = timezone.now()
    uploaded.seek(0)
    # Fixed server-side name: the client file name is never used as a path.
    profile.file.save(f"cv.{kind}", ContentFile(uploaded.read()), save=False)
    profile.save()
    if old_path and old_path.exists() and old_path != Path(profile.file.path):
        old_path.unlink(missing_ok=True)
    return profile


@require_http_methods(["GET", "POST"])
def cv_confirm(request):
    profile = CVProfile.peek()
    if not profile or not profile.raw_text:
        messages.info(request, "Upload your CV first.")
        return redirect("cv_upload")

    if request.method == "POST":
        form = CVConfirmForm(request.POST)
        if form.is_valid():
            c = form.cleaned_data
            profile.designation = c["designation"]
            profile.alternate_titles = c["alternate_titles"]
            profile.skills = c["skills"]
            profile.location = c["location"]
            profile.full_name = c["full_name"]
            profile.email = c["email"]
            profile.phone = c["phone"]
            profile.save()
            profile.confirm()
            messages.success(request, "Details confirmed.")
            return redirect("job_search")
    else:
        form = CVConfirmForm(initial={
            "designation": profile.designation,
            "alternate_titles": ", ".join(profile.alternate_titles or []),
            "skills": ", ".join(profile.skills or []),
            "location": profile.location,
            "full_name": profile.full_name,
            "email": profile.email,
            "phone": profile.phone,
        })
    return render(request, "cv/confirm.html", {
        "form": form, "profile": profile,
        "page_hint": "Check what we read from your CV and fix anything wrong. Job search starts only after you confirm.",
    })
