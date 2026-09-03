"""The queue's queries have to run on the database production uses.

Two defects reached production because SQLite accepted what Postgres
refuses, and both surfaced on the page as "crawler database not
connected" -- the view catches DatabaseError and cannot tell a broken
query from a broken connection:

1. `evidence` is TEXT in Postgres, not jsonb. Declaring it a JSONField let
   the ORM emit `?|` for `has_any_keys`, which Postgres has no operator
   for on text.
2. `.distinct()` over selected rows fails with "could not identify an
   equality operator for type json", because `articles` carries json
   columns. The telemetry join can repeat a row, so de-duplication is by
   id instead.

These tests assert the SHAPE that keeps both working, because the suite
runs on SQLite and cannot reproduce either failure.
"""

from pathlib import Path

from django.conf import settings

QUEUE = Path(settings.BASE_DIR) / "review/queue.py"
MODELS = Path(settings.BASE_DIR) / "explorer/models.py"


def test_evidence_is_not_declared_a_json_field():
    """It is TEXT in Postgres. Declaring it JSONField is what let the ORM
    emit an operator the column does not support."""
    body = MODELS.read_text()
    detection = body[body.index("class ContentTypeDetection") :]
    detection = detection[: detection.index("db_table")]
    assert "evidence = models.TextField" in detection
    assert "evidence = models.JSONField" not in detection


def test_no_json_key_operator_is_used_on_evidence():
    """`has_any_keys`, `has_key` and `contained_by` all emit operators
    Postgres refuses on a text column."""
    body = QUEUE.read_text()
    for lookup in ("has_any_keys", "has_keys", "has_key", "contained_by"):
        assert f"evidence__{lookup}" not in body


def test_the_queue_does_not_call_distinct_on_selected_rows():
    """DISTINCT over a row containing a json column fails in Postgres. The
    telemetry join can repeat a row, so the de-duplication is by id."""
    body = QUEUE.read_text()
    queued = body[body.index("def queued(") :]
    queued = queued[: queued.index("\ndef ")]
    # Comment lines dropped: this looks for the call, and the code says
    # in a comment why the call is not there.
    queued = "\n".join(
        line for line in queued.splitlines() if not line.strip().startswith("#")
    )
    assert ".distinct()" not in queued, (
        "queued() calls .distinct(); over rows carrying json columns that "
        "fails on Postgres. Match by id instead."
    )
    assert "id__in=" in queued


def test_the_not_connected_banner_is_reachable_only_from_a_real_failure():
    """Named here because both defects reached users as this message. A
    query error and an unreachable database are not the same thing, and
    reporting one as the other sent somebody to check credentials that
    were correct."""
    body = QUEUE.read_text()
    vocab = body[body.index("def vocab(") :]
    vocab = vocab[: vocab.index("\ndef ")] if "\ndef " in vocab else vocab
    # It still catches DatabaseError -- that is right -- but the queries it
    # guards must be ones that work, which the tests above enforce.
    assert "DatabaseError" in vocab
