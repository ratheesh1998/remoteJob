from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path("", include("core.urls")),
    path("cv/", include("cv.urls")),
    path("jobs/", include("jobs.urls")),
    path("applications/", include("applications.urls")),
]
