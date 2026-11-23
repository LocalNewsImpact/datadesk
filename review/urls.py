"""Review URLs."""

from django.urls import path

from review import views

app_name = "review"

urlpatterns = [
    path("queue/", views.queue, name="queue"),
    path("discovery/", views.discovery_queue, name="discovery"),
    path("classification/", views.classification_queue, name="classification"),
    path(
        "classification/geography/",
        views.geography_labeling_queue,
        name="geography_labeling",
    ),
    path(
        "classification/where/",
        views.geography_coding_resolve,
        name="geography_coding_resolve",
    ),
    path(
        "classification/find/",
        views.geography_coding_find,
        name="geography_coding_find",
    ),
    # Geography a person puts in, for articles the pipeline could not place.
    path("geography/", views.geography_queue, name="geography"),
    path("geography/suggest/", views.geography_suggest, name="geography_suggest"),
    # The CIN admin: the work about the queue, not the queue.
    path("cin/", views.cin_reporting, name="cin_reporting"),
    path("cin/coders/", views.cin_coders, name="cin_coders"),
    path("cin/codebook/", views.cin_codebook, name="cin_codebook"),
    path("cin/rounds/", views.cin_rounds, name="cin_rounds"),
    path("cin/rounds/<int:pk>/", views.cin_round, name="cin_round"),
    path("cin/export/", views.cin_export, name="cin_export"),
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
    # Byline review: the strings, then the two reports they feed.
    path("bylines/", views.byline_queue, name="bylines"),
    path("bylines/report/", views.byline_report, name="byline_report"),
    path("paywalls/", views.paywalls, name="paywalls"),
    path("schema/", views.schema, name="schema"),
    path("audit/", views.audit_log, name="audit_log"),
    path("audit/<int:entry_id>/", views.audit_entry, name="audit_entry"),
    path("audit/<int:entry_id>/revert/", views.revert_entry, name="revert"),
]
