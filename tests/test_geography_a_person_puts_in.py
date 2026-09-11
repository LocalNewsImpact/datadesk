"""A person gives geography to stories the pipeline could not read."""

import pytest

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def test_the_queue_excludes_the_verdict_that_is_not_a_gap():
    """`no_codeable_geography` means the pipeline read the story and
    found no place in it -- a column, a devotional, a national piece with
    nothing local. That is an answer, not a gap.

    1,155 of them against about 1,300 real gaps: asking somebody to
    second-guess the correct ones to find the others is how a queue stops
    being worked. Spot-checking the verdict is a different question and
    belongs in its own queue.
    """
    from review.geography import ANSWERED_NOT_MISSING, REVIEWABLE_SKIPS

    assert ANSWERED_NOT_MISSING == "no_codeable_geography"
    assert ANSWERED_NOT_MISSING not in REVIEWABLE_SKIPS


def test_the_filters_are_the_ones_the_builder_already_uses():
    """343 articles for March across three counties, 273 of them Boone
    across ten newsrooms. Nobody works a 343-item queue; "Osage, March"
    is 23. The filters are not a convenience -- without them the queue is
    not a queue.

    And they are the four the visual builder's spec already uses, so the
    console keeps one vocabulary rather than growing a second.
    """
    import inspect

    from review.geography import needs_geography

    taken = set(inspect.signature(needs_geography).parameters)
    assert {"dataset", "since", "until", "county", "newsroom"} <= taken


def test_a_suggestion_prefers_the_publishers_state_without_forcing_it():
    """RANKED, NEVER FILTERED.

    A story about the sewer trustees of Freeburg -- a village in Osage
    County, Missouri -- was extracted as "Freeburg, IL". Illinois has a
    Freeburg, the lookup succeeded, and the story was filed three hundred
    miles away. Somebody typing it for a Missouri outlet is offered
    Missouri's first.

    Filtering would be wrong: Whiteman Air Force Base, Nashville, Wichita
    State and Seattle were all covered by Missouri outlets in one month.
    """
    from review.geography import suggest

    hits = suggest("Freeburg", publisher_state="MO")
    assert hits[0]["state"] == "MO"
    assert "IL" in {h["state"] for h in hits}, "other states are still offered"


def test_a_typed_name_that_is_not_a_place_is_refused():
    """A name that does not resolve cannot become a row. The reviewer is
    sent back to the suggestions rather than storing something the
    pipeline could never have produced."""
    from review.geography import SET_THE_PLACE, apply_geography

    class _Article:
        id = "a1"

    with pytest.raises(ValueError, match="not a place"):
        apply_geography(
            _Article(), SET_THE_PLACE, {"name": "Nowheresville", "state": "MO"}, None
        )


def test_the_console_may_create_this_row_and_only_this_one():
    """The write boundary is an allowlist. `ArticlePlaceManual` is the
    first row the console creates that is not dataset maintenance, and it
    stays narrow: the console never writes `article_geoids`, which is the
    pipeline's -- the crawler rebuilds that from this table."""
    from explorer.models import ArticlePlaceManual
    from review.services import CREATABLE

    assert ArticlePlaceManual in CREATABLE
    names = {model.__name__ for model in CREATABLE}
    assert "ArticleGeoid" not in names


def test_the_queue_is_registered_at_startup():
    """A registry that only holds what a request happened to touch is not
    a registry."""
    from review import kernel
    from review.geography import GEOGRAPHY_QUEUE_KEY

    queue = kernel.get(GEOGRAPHY_QUEUE_KEY)
    assert queue.subject_type == "article"
    assert {v.name for v in queue.verbs} == {
        "set_place",
        "also_mentions",
        "nothing_to_add",
    }
