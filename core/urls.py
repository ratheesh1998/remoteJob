from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("smtp/", views.smtp_setup, name="smtp_setup"),
    path("help/", views.help_page, name="help"),
    path("delete-all/", views.delete_all, name="delete_all"),
]
