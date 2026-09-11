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


class _Article:
    """The two attributes `apply_geography` reads."""

    id = "a1"
    title = "A story"
    dataset_id = None
    candidate_link = None


def test_a_typed_name_that_is_not_a_place_is_refused():
    """A name that does not resolve cannot become a row. The reviewer is
    sent back to the suggestions rather than storing something the
    pipeline could never have produced."""
    from review.geography import SET_THE_PLACE, apply_geography

    with pytest.raises(ValueError, match="did not resolve"):
        apply_geography(_Article(), SET_THE_PLACE, "Nowheresville, MO", None)


def test_the_value_is_the_string_the_submit_path_actually_passes():
    """`review/submit.py` reads the value off a form field and stores it
    on `ReviewDecision.value` verbatim -- it is a string, and nothing
    upstream builds a structure. An `apply` expecting a dict raised
    AttributeError on the first real submit while every unit test that
    handed it a dict passed."""
    import inspect

    from review import submit

    source = inspect.getsource(submit)
    assert "queue.apply(subject, verb, value, user)" in source
    # And the value it passes comes from the POST, not from a parsed
    # structure.
    assert "def posted(" in inspect.getsource(submit)


def test_several_mentions_are_one_decision_and_several_rows():
    """A story mentions several places and enrichment records several, so
    a person has to be able to say the same thing.

    It must be ONE decision: `ReviewDecision` is keyed on (subject,
    field, question) and `update_or_create` overwrites, so a second
    mention submitted separately would replace the first decision while
    both rows stayed in the table -- an audit trail disagreeing with the
    data it exists to explain.
    """
    from review.geography import parse_places

    assert parse_places("Linn, MO; Westphalia, MO") == [
        ("Linn", "MO"),
        ("Westphalia", "MO"),
    ]
    # A place that does not name its own state takes the publisher's.
    assert parse_places("Linn; Westphalia", default_state="MO") == [
        ("Linn", "MO"),
        ("Westphalia", "MO"),
    ]


def test_a_story_may_have_mentions_and_no_centre():
    """A game between two towns has two mentions and no central location.
    A queue that requires a centre gets an invented one."""
    from review.geography import ALSO_MENTIONS, GEOGRAPHY_QUEUE, SET_THE_PLACE

    verbs = {v.name: v for v in GEOGRAPHY_QUEUE.verbs}
    # Each verb stands alone: one decision per article, and mentions is
    # a complete one.
    assert verbs[ALSO_MENTIONS].takes_value
    assert verbs[SET_THE_PLACE].name != verbs[ALSO_MENTIONS].name
    # And a centre is singular where a mention is not.
    assert "one per article" in verbs[SET_THE_PLACE].sublabel


def test_a_centre_is_one_place():
    """The table's partial unique index says so; refusing here names the
    reason instead of raising IntegrityError on the second row."""
    from review.geography import SET_THE_PLACE, apply_geography

    with pytest.raises(ValueError, match="one central location"):
        apply_geography(_Article(), SET_THE_PLACE, "Linn, MO; Westphalia, MO", None)


def test_nothing_is_written_when_any_part_did_not_resolve():
    """A partial write records some of what a reviewer said and silently
    drops the rest, and they have no way to tell which."""
    from review.geography import ALSO_MENTIONS, apply_geography

    with pytest.raises(ValueError, match="Nowheresville"):
        apply_geography(_Article(), ALSO_MENTIONS, "Linn, MO; Nowheresville, MO", None)


def test_the_county_rung_is_offered_where_the_place_is_not_a_census_place():
    """Frankenstein is a real village in Osage County, Missouri, and is
    in no gazetteer -- unincorporated, so neither a place nor a CDP.
    Offering only places leaves the story unenterable and the reviewer
    invents a nearby town.

    Slots are RESERVED rather than competed for: appending counties after
    places and truncating gave every slot to places, and "Osage" for a
    Missouri outlet returned Osage IA, MN, OK, WV, WY and Sage CA --
    there being no Missouri place called Osage at all."""
    from lnic_contracts.geography import canonical_place

    from review.geography import suggest

    assert canonical_place("MO", "Frankenstein") == (None, None)

    hits = suggest("Osage", publisher_state="MO")
    assert hits[0]["kind"] == "county"
    assert hits[0]["state"] == "MO"
    assert hits[0]["name"] == "Osage"
    # The other rung is still there, and other states still are too.
    assert "place" in {h["kind"] for h in hits}
    assert {h["state"] for h in hits} - {"MO"}


def test_a_county_is_written_on_the_county_rung():
    """ "Osage" and "Osage County" are different codes, and a reviewer
    picking one must not get the other."""
    from review.geography import parse_places

    assert parse_places("Osage County, MO") == [("Osage County", "MO")]


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
