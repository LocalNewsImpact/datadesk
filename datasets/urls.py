"""Dataset management URLs."""

from django.urls import path

from datasets import views

app_name = "datasets"

urlpatterns = [
    path("datasets/", views.dataset_list, name="list"),
    path("datasets/new/", views.dataset_create, name="create"),
    path("datasets/<slug:slug>/", views.dataset_detail, name="detail"),
    path("sources/new/", views.source_create, name="source_create"),
    path(
        "sources/propose-new/",
        views.source_propose_new,
        name="source_propose_new",
    ),
    path("sources/<str:source_id>/", views.source_edit, name="source_edit"),
    path(
        "sources/<str:source_id>/propose/",
        views.source_propose,
        name="source_propose",
    ),
]
