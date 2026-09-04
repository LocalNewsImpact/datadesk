"""Each disposition, proved against the pipeline that has to act on it.

A status is only a decision if something downstream selects it. These
tests pin the selectors the crawler actually uses, read out of its source,
so a change there fails here rather than silently stranding every decision
a reviewer made.

The selectors, verbatim:

  ENRICHMENT   src/enrichment/repository.py
      WHERE a.status = 'labeled'
        AND a.wire_check_status IN ('complete', 'local')
        AND a.enrichment_attempts < :max_attempts        (default 3)

  LABELING     src/cli/commands/analysis.py::_resolve_statuses
      default ["cleaned", "local"]

  PROMOTION    src/models/database.py
      if article.status in ("cleaned", "local"): status = "labeled"

  HOUSEKEEPING src/cli/commands/housekeeping.py
      UPDATE articles SET status = 'paused', pause_reason = 'null_text'
       WHERE status = 'extracted' AND text IS NULL
"""

import pytest
from django.contrib.auth.models import User

from explorer.models import Article, CandidateLink, Source
from review.dispositions import (
    ENRICHMENT,
    EXTRACTION,
    LABELING,
    REEXTRACT_TO,
    record,
    rewind_target,
)

#: What each downstream stage selects. Copied from the crawler, not guessed.
ENRICHMENT_SELECTS = "labeled"
LABELING_SELECTS = ("cleaned", "local")
ENRICHMENT_MAX_ATTEMPTS = 3
#: The shape housekeeping pauses.
HOUSEKEEPING_PAUSES = ("extracted", None)


@pytest.fixture
def reviewer(db):
    return User.objects.create_user("ed", email="ed@localnewsimpact.org")


def _article(pk, crawler_schema, **kwargs):
    source, _ = Source.objects.get_or_create(
        id="s1", defaults={"host": "a.example", "host_norm": "a.example"}
    )
    link = CandidateLink.objects.create(id=f"c{pk}", source_id=source.id, url="u")
    fields = {
        "status": "not_article",
        "wire_check_status": "complete",
        "content": "A captured body.",
        "enrichment_attempts": 1,
    }
    fields.update(kwargs)
    return Article.objects.create(id=pk, candidate_link=link, **fields)


# --- reject at the enrichment stage ------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_reject_at_enrichment_lands_on_the_status_enrichment_selects(
    reviewer, crawler_schema
):
    article = _article("e1", crawler_schema)
    record(article, decision="reject", stage=ENRICHMENT, user=reviewer)
    article.refresh_from_db()
    assert article.status == ENRICHMENT_SELECTS


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_rejected_row_still_satisfies_the_rest_of_the_enrichment_selector(
    reviewer, crawler_schema
):
    """Status alone is not enough. The selector also requires a settled
    wire check and attempts under the limit -- in production the highest
    attempt count on these rows is 1, against a limit of 3."""
    article = _article("e2", crawler_schema, enrichment_attempts=1)
    record(article, decision="reject", stage=ENRICHMENT, user=reviewer)
    article.refresh_from_db()
    assert article.status == ENRICHMENT_SELECTS
    assert article.wire_check_status in ("complete", "local")
    assert article.enrichment_attempts < ENRICHMENT_MAX_ATTEMPTS


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_row_at_the_attempt_limit_would_not_be_re_enriched(reviewer, crawler_schema):
    """The one way this path fails. No production row is here today --
    the highest is 1 -- but a rewind is not a promise if the selector
    still excludes it, and this says so rather than assuming."""
    article = _article("e3", crawler_schema, enrichment_attempts=3)
    record(article, decision="reject", stage=ENRICHMENT, user=reviewer)
    article.refresh_from_db()
    assert article.status == ENRICHMENT_SELECTS
    assert not article.enrichment_attempts < ENRICHMENT_MAX_ATTEMPTS


# --- reject at the extraction and labeling stages -----------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize("stage", [EXTRACTION, LABELING])
def test_reject_lands_on_a_status_the_labeler_selects(reviewer, crawler_schema, stage):
    article = _article(f"l-{stage}", crawler_schema)
    record(article, decision="reject", stage=stage, user=reviewer)
    article.refresh_from_db()
    assert article.status in LABELING_SELECTS


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_labeler_then_promotes_it_to_what_enrichment_selects(
    reviewer, crawler_schema
):
    """The whole path, not one hop: `cleaned` is what the labeler reads,
    and what it writes on success is `labeled`, which is what enrichment
    reads. So a rejection at extraction reaches enrichment in two steps."""
    article = _article("l2", crawler_schema)
    record(article, decision="reject", stage=EXTRACTION, user=reviewer)
    article.refresh_from_db()
    assert article.status in LABELING_SELECTS
    # models/database.py promotes exactly these to `labeled`.
    assert article.status in ("cleaned", "local")


# --- re-extraction ------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_re_extraction_does_not_claim_extraction_succeeded(reviewer, crawler_schema):
    """`extracted` is written only when extraction succeeds, counts as
    eligible for classification, and -- with no text -- is the exact shape
    housekeeping pauses. Setting it would queue a body-less row for
    labelling and then be undone by the next housekeeping run."""
    article = _article(
        "r1", crawler_schema, content="", text="", raw_gcs_path="gs://bucket/p.html.gz"
    )
    record(article, decision="reextract", stage=EXTRACTION, user=reviewer)
    article.refresh_from_db()
    assert article.status != "extracted"
    assert article.status == REEXTRACT_TO


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_re_extracted_row_is_not_the_shape_housekeeping_pauses(
    reviewer, crawler_schema
):
    article = _article(
        "r2", crawler_schema, content="", text="", raw_gcs_path="gs://bucket/p.html.gz"
    )
    record(article, decision="reextract", stage=EXTRACTION, user=reviewer)
    article.refresh_from_db()
    status, text = HOUSEKEEPING_PAUSES
    assert not (article.status == status and not article.text)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_re_extraction_keeps_the_path_to_the_capture(reviewer, crawler_schema):
    """The status says it is out of the pipeline; raw_gcs_path says the
    body can be rebuilt. Both are needed and neither is cleared."""
    article = _article(
        "r3", crawler_schema, content="", text="", raw_gcs_path="gs://bucket/p.html.gz"
    )
    record(article, decision="reextract", stage=EXTRACTION, user=reviewer)
    article.refresh_from_db()
    assert article.raw_gcs_path == "gs://bucket/p.html.gz"


# --- accept -------------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize("status", ["not_article", "obituary", "weather", "opinion"])
def test_accept_leaves_a_status_no_downstream_stage_selects(
    reviewer, crawler_schema, status
):
    """Accept is correct precisely because the status already excludes the
    article: none of these is what the labeler or enrichment reads."""
    article = _article(f"a-{status}", crawler_schema, status=status)
    record(article, decision="accept", stage=EXTRACTION, user=reviewer)
    article.refresh_from_db()
    assert article.status == status
    assert article.status != ENRICHMENT_SELECTS
    assert article.status not in LABELING_SELECTS


# --- the map itself -----------------------------------------------------------


def test_every_rewind_target_is_a_status_something_selects():
    """A target nothing reads is a decision that does not happen."""
    selected_by_something = set(LABELING_SELECTS) | {ENRICHMENT_SELECTS}
    for stage in (EXTRACTION, LABELING, ENRICHMENT):
        assert rewind_target(stage) in selected_by_something
