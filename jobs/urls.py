from django.urls import path

from . import views

urlpatterns = [
    path("", views.job_search, name="job_search"),
    path("start/", views.job_start, name="job_start"),
    path("paste/", views.job_paste, name="job_paste"),
    path("search-settings/", views.search_settings, name="search_settings"),
    path("progress/", views.job_progress, name="job_progress"),
    path("results/", views.job_results, name="job_results"),
]
