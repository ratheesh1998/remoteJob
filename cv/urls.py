from django.urls import path

from . import views

urlpatterns = [
    path("", views.cv_upload, name="cv_upload"),
    path("confirm/", views.cv_confirm, name="cv_confirm"),
]
