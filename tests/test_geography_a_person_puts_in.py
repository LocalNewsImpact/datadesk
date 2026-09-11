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


# --- the filter bar -----------------------------------------------------------


def test_the_dataset_filter_resolves_a_slug_to_the_id_the_column_holds():
    """`articles.dataset_id` holds the dataset's UUID and the filter bar
    offers slugs, so `dataset_id=<slug>` matched nothing at all: picking
    a dataset emptied the queue AND every dropdown built from it, which
    reads as a dataset with no work in it rather than a filter that
    cannot match.

    Asserted against the one definition of this question --
    `review.queue._in_dataset` -- whose own docstring says why it is one:
    "a dropdown built over a different population than the list offers
    values that return nothing."
    """
    import inspect

    from review import geography

    source = inspect.getsource(geography.needs_geography)
    assert "_in_dataset" in source
    assert "dataset_id=dataset" not in source


def test_the_filters_cascade(monkeypatch):
    """Each filter is offered from what the one before it leaves.

    Offering every newsroom regardless means picking Osage and then a
    newsroom in Boone, which returns nothing and reads as a queue that
    lost rows rather than as two filters that cannot both be true.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    body = (
        (root / "review/views.py")
        .read_text()
        .split("def geography_queue(")[1]
        .split("\ndef ")[0]
    )
    # Counties come from the dataset's population, newsrooms from the
    # county's -- two different querysets, not one reused.
    assert "in_dataset = geography.needs_geography(" in body
    assert "in_county = geography.needs_geography(" in body
    assert "county=county or None" in body
    # And a selection the new scope cannot offer is dropped rather than
    # left to filter everything away.
    assert 'params.pop("county", None)' in body
    assert 'params.pop("newsroom", None)' in body


def test_the_rows_are_queried_after_the_selections_are_validated():
    """Building the queryset first and validating afterwards meant a
    dropped county still filtered the rows: the select said "all" and the
    queue showed one county's worth."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    body = (
        (root / "review/views.py")
        .read_text()
        .split("def geography_queue(")[1]
        .split("\ndef ")[0]
    )
    assert body.index('params.pop("newsroom", None)') < body.index(
        "candidates = geography.needs_geography("
    )


# --- what a reviewer is told before they open the link ------------------------


class _Enrichment:
    def __init__(self, skip_reason="", geo_skip_reason=""):
        self.skip_reason = skip_reason
        self.geo_skip_reason = geo_skip_reason


class _Placed:
    def __init__(self, enrichment=None, **kw):
        if enrichment is not None:
            self.enrichment = enrichment
        for key, value in kw.items():
            setattr(self, key, value)


def test_a_paywall_stub_reads_differently_from_a_story_nobody_scoped():
    """A reviewer has to know whether to open the link. A paywall stub
    has only a headline; a story nobody scoped may have the place in the
    text already."""
    from review.geography import why_no_geography

    label, detail = why_no_geography(_Placed(_Enrichment(skip_reason="paywall_stub")))
    assert label == "behind a paywall"
    assert "only way to read it" in detail

    label, detail = why_no_geography(_Placed(_Enrichment(geo_skip_reason="not_scoped")))
    assert label == "never scoped"
    assert detail


def test_a_regional_story_with_an_empty_place_set_says_so():
    """Its geography IS the places it names, so naming none is the whole
    explanation."""
    from review.geography import why_no_geography

    label, detail = why_no_geography(
        _Placed(_Enrichment(geo_skip_reason="regional_uses_place_set"))
    )
    assert "regional" in label
    assert "named none" in detail


def test_a_gazetteer_miss_explains_that_nothing_could_be_placed_relative_to_it():
    from review.geography import why_no_geography

    label, detail = why_no_geography(
        _Placed(_Enrichment(geo_skip_reason="publication_city_not_in_census_gazetteer"))
    )
    assert "gazetteer does not have" in label
    assert "publication" in detail.lower()


def test_an_article_nothing_ran_on_is_named_as_such():
    """Distinct from every other reason: there is no enrichment row at
    all, so there is no skip reason to report."""
    from review.geography import why_no_geography

    assert why_no_geography(_Placed())[0] == "never enriched"


def test_a_reason_nobody_wrote_a_sentence_for_is_still_shown():
    """An unrecognised reason is passed through rather than swallowed --
    a blank cell would read as a row with nothing wrong with it."""
    from review.geography import why_no_geography

    assert why_no_geography(_Placed(_Enrichment(geo_skip_reason="odd")))[0] == "odd"
    assert why_no_geography(_Placed(_Enrichment()))[0] == "no reason recorded"


# --- which state the suggestions rank by --------------------------------------


def test_the_publishers_own_state_wins_over_the_datasets_default():
    """`sources.metadata` first, exactly as enrichment resolves it. A
    Missouri dataset carrying a Kansas outlet must rank Kansas for that
    outlet's stories."""
    from review.geography import publisher_state

    article = _Placed(
        candidate_link=_Placed(source=_Placed(meta={"state": "KS"})),
        dataset_id="d1",
    )
    assert publisher_state(article) == "KS"


def test_an_article_with_no_dataset_asks_the_database_nothing():
    """This is called once per row. An unguarded lookup here is a round
    trip per article to read a value a page of 25 shares -- and an
    article with no dataset asked for `id=None`, a query for nothing."""
    from review.geography import publisher_state

    article = _Placed(candidate_link=None, dataset_id=None)
    assert publisher_state(article) is None


def test_a_source_without_metadata_falls_through_rather_than_raising():
    from review.geography import publisher_state

    article = _Placed(
        candidate_link=_Placed(source=_Placed(meta=None)), dataset_id=None
    )
    assert publisher_state(article) is None


# --- the write ----------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_decision_writes_the_rows_and_an_audit_entry(crawler_schema):
    """The write path end to end: what a reviewer picked becomes rows,
    resolved by the contract rather than by anything they typed, and the
    creation is audited."""
    from django.contrib.auth.models import User

    from explorer.models import Article, ArticlePlaceManual
    from review.geography import ALSO_MENTIONS, apply_geography

    user = User.objects.create_user("r", email="r@example.org")
    article = Article.objects.create(id="w1", status="enrichment_skipped", title="A")

    out = apply_geography(article, ALSO_MENTIONS, "Linn, MO; Westphalia, MO", user)

    rows = ArticlePlaceManual.objects.filter(article_id="w1").order_by("geoid")
    assert [(r.geoid, r.city, r.is_point, r.added_by) for r in rows] == [
        ("2943238", "Linn", False, "r@example.org"),
        ("2978910", "Westphalia", False, "r@example.org"),
    ]
    # The name as typed is kept beside the code it resolved to, so a
    # wrong resolution can be told from a wrong entry.
    assert {r.full_name for r in rows} == {"Linn", "Westphalia"}
    assert out["after"] == "Linn, Westphalia"
    assert out["wrote"]["audit"]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_county_rung_is_written_as_a_county(crawler_schema):
    """ "Osage" and "Osage County" are different codes. Resolving the
    suffixed name as a place would fail -- no gazetteer has a place
    called "Osage County"."""
    from django.contrib.auth.models import User

    from explorer.models import Article, ArticlePlaceManual
    from review.geography import ALSO_MENTIONS, apply_geography

    user = User.objects.create_user("r2", email="r2@example.org")
    article = Article.objects.create(id="w2", status="enrichment_skipped", title="B")

    apply_geography(article, ALSO_MENTIONS, "Osage County, MO", user)

    row = ArticlePlaceManual.objects.get(article_id="w2")
    assert (row.geoid, row.geoid_level) == ("29151", "county")
    # The county name lands in `county`, not `city`.
    assert (row.county, row.city) == ("Osage", None)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_centre_is_written_as_the_point(crawler_schema):
    from django.contrib.auth.models import User

    from explorer.models import Article, ArticlePlaceManual
    from review.geography import SET_THE_PLACE, apply_geography

    user = User.objects.create_user("r3", email="r3@example.org")
    article = Article.objects.create(id="w3", status="enrichment_skipped", title="C")

    apply_geography(article, SET_THE_PLACE, "Linn, MO", user)
    assert ArticlePlaceManual.objects.get(article_id="w3").is_point is True


@pytest.mark.django_db(databases=["default", "crawler"])
def test_saying_there_is_no_place_writes_nothing(crawler_schema):
    """The article has no geography and a person has confirmed none can
    be given, which is what stops the queue asking again."""
    from django.contrib.auth.models import User

    from explorer.models import Article, ArticlePlaceManual
    from review.geography import NOTHING_TO_ADD, apply_geography

    user = User.objects.create_user("r4", email="r4@example.org")
    article = Article.objects.create(id="w4", status="enrichment_skipped", title="D")

    out = apply_geography(article, NOTHING_TO_ADD, "", user)
    assert out["wrote"] == "nothing"
    assert not ArticlePlaceManual.objects.filter(article_id="w4").exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_refusal_writes_no_part_of_the_answer(crawler_schema):
    """A partial write records some of what a reviewer said and silently
    drops the rest, and they have no way to tell which."""
    from django.contrib.auth.models import User

    from explorer.models import Article, ArticlePlaceManual
    from review.geography import ALSO_MENTIONS, apply_geography

    user = User.objects.create_user("r5", email="r5@example.org")
    article = Article.objects.create(id="w5", status="enrichment_skipped", title="E")

    with pytest.raises(ValueError, match="Fatima"):
        apply_geography(article, ALSO_MENTIONS, "Linn, MO; Fatima, MO", user)

    assert not ArticlePlaceManual.objects.filter(article_id="w5").exists()
