import shutil

from django.conf import settings
from django.db import transaction


@transaction.atomic
def delete_all_data() -> None:
    """Wipe the CV (and its file), jobs, applications and SMTP config."""
    from applications.models import Application
    from cv.models import CVProfile
    from jobs.models import Job, JobSearch, SourceResult

    from .models import SMTPConfig

    Application.objects.all().delete()
    Job.objects.all().delete()
    SourceResult.objects.all().delete()
    JobSearch.objects.all().delete()
    # Queryset deletes bypass the singleton guard on instance.delete().
    CVProfile.objects.all().delete()
    SMTPConfig.objects.all().delete()
    cv_dir = settings.MEDIA_ROOT / "cv"
    if cv_dir.exists():
        shutil.rmtree(cv_dir, ignore_errors=True)
