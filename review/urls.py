"""Review URLs."""

from django.urls import path

from review import views

app_name = "review"

urlpatterns = [
    path(
        "articles/<str:article_id>/edit/<str:field>/",
        views.edit_field,
        name="edit_field",
    ),
    path("articles/disposition/", views.bulk_disposition, name="bulk_disposition"),
    path("audit/", views.audit_log, name="audit_log"),
    path("audit/<int:entry_id>/revert/", views.revert_entry, name="revert"),
]
