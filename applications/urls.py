from django.urls import path

from . import views

urlpatterns = [
    path("", views.history, name="applications_history"),
    path("prepare/", views.prepare, name="applications_prepare"),
    path("review/", views.review, name="applications_review"),
    path("review/drafts/", views.review_drafts, name="applications_review_drafts"),
    path("confirm/", views.confirm, name="applications_confirm"),
    path("<int:pk>/remove/", views.remove_draft, name="applications_remove"),
    path("do-not-contact/", views.do_not_contact, name="applications_dnc"),
    path("do-not-contact/<int:pk>/remove/", views.dnc_remove, name="applications_dnc_remove"),
    path("<int:pk>/do-not-contact/", views.dnc_from_application, name="applications_dnc_add"),
    path("<int:pk>/retry/", views.retry, name="applications_retry"),
    path("progress/<uuid:batch>/", views.progress, name="applications_progress"),
    path("progress/<uuid:batch>/status/", views.progress_partial, name="applications_progress_partial"),
    path("export.csv", views.export_csv, name="applications_export"),
]
