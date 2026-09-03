"""An article that cannot be released says so, rather than disappearing.

The crawler writes the hold note and the console reads it. Nothing
enforces the shape jointly across the two repositories, so a key renamed
on one side is invisible until an article is held and cannot be let out:
the status it was held from is gone, ACCEPT has nothing to restore it to,
and it stays out of the pipeline for ever.

Treating such an article as "never held" is how the data would go missing
without anybody seeing it. It is reported on the row instead.
"""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review.dispositions import (
    IN_REVIEW,
    REQUIRED_NOTE_KEYS,
    REVIEW_META_KEY,
    note_is_readable,
    unreadable_note_reason,
)

COMPLETE = {
    "status_before": "labeled",
    "claim": "byline_not_a_name",
    "stage": "extraction",
    "held_at": "2026-09-03T00:00:00+00:00",
}


class _Row:
    def __init__(self, status, metadata=None):
        self.status = status
        self.metadata = metadata


# --- what counts as readable --------------------------------------------------


def test_a_complete_note_is_readable():
    assert note_is_readable(_Row(IN_REVIEW, {REVIEW_META_KEY: COMPLETE}))


def test_a_held_article_with_no_note_is_not():
    assert not note_is_readable(_Row(IN_REVIEW, {}))


@pytest.mark.parametrize("missing", REQUIRED_NOTE_KEYS)
def test_every_required_key_is_actually_required(missing):
    """One test per key, so a key quietly dropped from the contract fails
    here rather than at runtime on a held article."""
    note = {k: v for k, v in COMPLETE.items() if k != missing}
    assert not note_is_readable(_Row(IN_REVIEW, {REVIEW_META_KEY: note}))


def test_a_renamed_key_is_caught():
    """The exact cross-repository failure: the crawler renames
    status_before and the console can no longer put anything back."""
    renamed = dict(COMPLETE)
    renamed["prior_status"] = renamed.pop("status_before")
    assert not note_is_readable(_Row(IN_REVIEW, {REVIEW_META_KEY: renamed}))


def test_an_empty_value_is_as_bad_as_a_missing_key():
    note = dict(COMPLETE, status_before="   ")
    assert not note_is_readable(_Row(IN_REVIEW, {REVIEW_META_KEY: note}))


def test_an_article_that_was_never_held_is_not_stranded():
    """Only IN_REVIEW needs a note. Everything else is where it belongs."""
    assert note_is_readable(_Row("labeled", None))
    assert note_is_readable(_Row("obituary", {}))


def test_a_note_that_arrived_as_a_json_string_is_read():
    """metadata is JSON in the crawler and can reach here as text."""
    import json

    row = _Row(IN_REVIEW, json.dumps({REVIEW_META_KEY: COMPLETE}))
    assert note_is_readable(row)


# --- what it says -------------------------------------------------------------


def test_the_reason_names_the_missing_keys():
    note = {k: v for k, v in COMPLETE.items() if k != "status_before"}
    reason = unreadable_note_reason(_Row(IN_REVIEW, {REVIEW_META_KEY: note}))
    assert "status_before" in reason


def test_a_readable_note_has_nothing_to_say():
    assert unreadable_note_reason(_Row(IN_REVIEW, {REVIEW_META_KEY: COMPLETE})) == ""


# --- and it reaches the page ---------------------------------------------------


@pytest.fixture
def reviewer(client, db):
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_queue_shows_a_stranded_article(client, reviewer, crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset=dataset, source_id=source.id)
    link = CandidateLink.objects.create(id="c1", source_id=source.id, url="u")
    Article.objects.create(
        id="a1",
        candidate_link=link,
        status="not_article",
        wire_check_status="complete",
        content="A body.",
        title="Held with a broken note",
        metadata={REVIEW_META_KEY: {"claim": "x", "stage": "y", "held_at": "z"}},
    )
    # Held, with the status_before missing.
    Article.objects.filter(id="a1").update(status=IN_REVIEW)

    body = client.get("/review/queue/", {"all": "1"}).content.decode()
    assert "incomplete note" in body or "no note recorded" in body


# --- the hold must not be a black hole ----------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_held_article_is_in_the_queue_at_all(client, reviewer, crawler_schema):
    """The gap this found. `in_review` is not one of the statuses the other
    cases select, so holding an article took it OUT of the queue that is
    supposed to review it -- every field defect the crawler holds would
    have been invisible here.
    """
    from review.queue import HELD_FOR_REVIEW, queued

    dataset = Dataset.objects.create(id="d2", slug="mo2", label="Missouri")
    source = Source.objects.create(id="s2", host="b.example", host_norm="b.example")
    DatasetSource.objects.create(id="ds2", dataset=dataset, source_id=source.id)
    link = CandidateLink.objects.create(id="c2", source_id=source.id, url="u")
    Article.objects.create(
        id="held",
        candidate_link=link,
        status=IN_REVIEW,
        wire_check_status="complete",
        content="A body.",
        title="Held",
        metadata={REVIEW_META_KEY: COMPLETE},
    )

    # On the landing view, unnarrowed: a held article is stopped and
    # waiting on a person.
    assert "held" in {a.id for a in queued({}, reviewer)}
    # And selectable as its own case.
    assert "held" in {a.id for a in queued({"case": HELD_FOR_REVIEW}, reviewer)}
