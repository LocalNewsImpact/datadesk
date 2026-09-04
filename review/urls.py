"""Review URLs."""

from django.urls import path

from review import views

app_name = "review"

urlpatterns = [
    path("queue/", views.queue, name="queue"),
    path(
        "extraction-problems/",
        views.extraction_problems,
        name="extraction_problems",
    ),
    path(
        "articles/<str:article_id>/edit/<str:field>/",
        views.edit_field,
        name="edit_field",
    ),
    path("articles/disposition/", views.bulk_disposition, name="bulk_disposition"),
    path("import/", views.import_batches, name="import_batches"),
    path("import/<int:batch_id>/map/", views.import_map, name="import_map"),
    path("import/<int:batch_id>/", views.import_diff, name="import_diff"),
    path("import/<int:batch_id>/revert/", views.import_revert, name="import_revert"),
    path("export/", views.export, name="export"),
    path("export/<int:definition_id>/run/", views.export_run, name="export_run"),
    path("proposals/", views.proposals, name="proposals"),
    path("proposals/rescan/", views.rescan_sources, name="rescan"),
    path("paywalls/", views.paywalls, name="paywalls"),
    path("schema/", views.schema, name="schema"),
    path("audit/", views.audit_log, name="audit_log"),
    path("audit/<int:entry_id>/revert/", views.revert_entry, name="revert"),
]
