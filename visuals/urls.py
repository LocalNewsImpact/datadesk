"""Visuals URLs. The embed lives at the root (/embed/<slug>/), matching
SCOPE.md §2.7; everything else under /visuals/."""

from django.urls import path

from visuals import views

app_name = "visuals"

urlpatterns = [
    path("visuals/", views.index, name="index"),
    path("visuals/builder/new/", views.builder_new, name="builder_new"),
    path(
        "visuals/builder/<slug:slug>/type/",
        views.builder_type,
        name="builder_type",
    ),
    path(
        "visuals/builder/<slug:slug>/step/<str:step>/",
        views.builder_step,
        name="builder_step",
    ),
    path("visuals/builder/<slug:slug>/", views.builder_edit, name="builder_edit"),
    path(
        "visuals/builder/<slug:slug>/duplicate/",
        views.builder_duplicate,
        name="builder_duplicate",
    ),
    # The two counts that used to run while a step rendered. Fetched once
    # the step has drawn, so the page appears at once and fills in.
    path(
        "visuals/builder/<slug:slug>/newsroom-counts/",
        views.newsroom_counts,
        name="newsroom_counts",
    ),
    path(
        "visuals/builder/<slug:slug>/values/<slug:role>/",
        views.role_values,
        name="role_values",
    ),
    path("visuals/<slug:slug>/", views.page, name="page"),
    path("visuals/<slug:slug>/data.json", views.data_json, name="data"),
    path("visuals/<slug:slug>/data.csv", views.data_csv, name="data_csv"),
    path("embed/<slug:slug>/", views.embed, name="embed"),
]
