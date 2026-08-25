"""Walking the builder, one step at a time.

The rule the whole design rests on: a step writes only the keys it owns, so
going back changes one choice and leaves the rest. These walk it rather than
asserting it in the abstract, because the failure it guards against is
silent -- a form that quietly empties when somebody looks at another chart
type teaches them not to explore.
"""

from unittest import mock

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from explorer.models import Dataset, DatasetSource, Source
from visuals.models import Visual
from visuals.types import BY_ID

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def author(client):
    user = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


@pytest.fixture
def dataset(crawler_schema):
    return Dataset.objects.create(
        id="d1", slug="mizzou", label="Missouri", meta={"default_state": "MO"}
    )


@pytest.fixture
def newsroom(dataset):
    source = Source.objects.create(
        id="s1",
        host="komu.example",
        host_norm="komu.example",
        canonical_name="KOMU",
        city="Columbia",
        county="Boone",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    return source


@pytest.fixture
def visual(author):
    return Visual.objects.create(
        slug="walk",
        title="Walk",
        template="builder",
        source_kind="corpus",
        created_by=author,
    )


def step(client, visual, name, **post):
    url = f"/visuals/builder/{visual.slug}/step/{name}/"
    return client.post(url, post) if post else client.get(url)


# --- each step writes its own keys and no others -----------------------------


def test_choosing_a_type_writes_only_the_type(client, author, visual):
    visual.config = {"theme": "mizzou", "title": "Mine"}
    visual.save()
    step(client, visual, "type", kind="chord")
    visual.refresh_from_db()
    assert visual.config["kind"] == "chord"
    assert visual.config["theme"] == "mizzou", "the colour survived"
    assert visual.config["title"] == "Mine"


def test_changing_the_type_keeps_the_fields_already_chosen(client, author, visual):
    """The one step that can invalidate an earlier choice. The rule is to
    keep it and mark it unusable, never to empty the form."""
    visual.config = {"kind": "bar"}
    visual.spec = {"roles": {"x": "cin_primary", "y": "articles"}}
    visual.save()
    step(client, visual, "type", kind="donut")
    visual.refresh_from_db()
    assert visual.config["kind"] == "donut"
    assert visual.spec["roles"]["x"] == "cin_primary"


def test_the_colour_step_does_not_touch_the_data(client, author, visual):
    visual.spec = {"dataset": "mizzou", "from": "2026-03-01"}
    visual.save()
    step(client, visual, "theme", theme="rji", taxonomy="1")
    visual.refresh_from_db()
    assert visual.config["theme"] == "rji"
    assert visual.config["taxonomy"] == "cin"
    assert visual.spec["dataset"] == "mizzou", "the slice survived"


def test_the_data_step_writes_datasets_and_dates(client, author, visual, dataset):
    step(
        client,
        visual,
        "data",
        datasets=["mizzou"],
        **{"from": "2026-03-01", "to": "2026-03-31"},
    )
    visual.refresh_from_db()
    assert visual.spec["datasets"] == ["mizzou"]
    assert visual.spec["from"] == "2026-03-01"


def test_a_dataset_outside_the_authors_grants_is_refused(
    client, author, visual, dataset
):
    """A slug typed into a form must not reach past what the author can
    read -- the same rule the single-dataset field follows."""
    Dataset.objects.create(id="d2", slug="secret", label="Not theirs")
    Grant.objects.filter(user=author).update(scope="mizzou")
    response = step(client, visual, "data", datasets=["secret"])
    visual.refresh_from_db()
    assert "secret" not in (visual.spec.get("datasets") or [])
    assert response.status_code == 200, "refused in the panel, not a crash"


# --- the newsrooms -----------------------------------------------------------


def test_no_newsrooms_chosen_means_all_of_them(client, author, visual, newsroom):
    """Storing exclusions would make "all" a list that goes stale the
    moment a publisher is added."""
    step(client, visual, "newsrooms")
    visual.refresh_from_db()
    assert visual.spec.get("publishers") in (None, [])


def test_the_tree_is_state_then_county_then_newsroom(client, author, visual, newsroom):
    body = step(client, visual, "newsrooms").content.decode()
    assert "Missouri" in body, "the code is stored and the name is shown"
    assert "Boone" in body
    assert "KOMU" in body


# --- the fields, which is where the pivot is decided -------------------------


def test_the_fields_step_asks_for_a_chart_type_first(client, author, visual):
    body = step(client, visual, "fields").content.decode()
    assert "Pick a chart type first" in body


def test_a_role_offers_only_variables_that_fit_it(client, author, visual):
    visual.config = {"kind": "chord"}
    visual.save()
    body = step(client, visual, "fields").content.decode()
    # `from` takes a category, so a measure is not offered for it.
    assert "CIN (primary)" in body
    assert "Cost (sum, USD)" in body, "the amount slot takes a measure"


def test_choosing_variables_writes_the_pivot(client, author, visual, dataset):
    """Picking variables *is* defining the pivot, which the prototype
    assumed had already happened."""
    visual.config = {"kind": "chord"}
    visual.save()
    step(
        client,
        visual,
        "fields",
        **{
            "role-from": "cin_primary",
            "role-to": "cin_alternate",
            "role-value": "articles",
        },
    )
    visual.refresh_from_db()
    assert visual.spec["dimensions"] == ["cin_primary", "cin_alternate"]
    assert visual.spec["measure"] == "articles"
    assert visual.spec["roles"]["from"] == "cin_primary"


def test_an_unknown_variable_is_refused(client, author, visual):
    visual.config = {"kind": "chord"}
    visual.save()
    response = step(client, visual, "fields", **{"role-from": "nonsense"})
    visual.refresh_from_db()
    assert not visual.spec.get("dimensions")
    assert response.status_code == 200


# --- the shell ---------------------------------------------------------------


def test_the_sentence_says_what_has_been_chosen(client, author, visual):
    visual.config = {"kind": "chord"}
    visual.spec = {"roles": {"from": "cin_primary", "to": "cin_alternate"}}
    visual.save()
    body = step(client, visual, "type").content.decode()
    assert "chord diagram" in body
    assert "CIN (primary)" in body


def test_the_sentence_marks_the_step_being_worked_on(client, author, visual):
    body = step(client, visual, "type").content.decode()
    assert 'class="gap here"' in body


def test_the_later_steps_wait_for_a_chart_type(client, author, visual):
    """Shown and refused rather than hidden: knowing the step exists is
    worth more than a rail that grows as you go."""
    body = step(client, visual, "type").content.decode()
    assert "not-yet" in body


def test_an_unknown_step_is_a_404(client, author, visual):
    assert (
        client.get(f"/visuals/builder/{visual.slug}/step/nonsense/").status_code == 404
    )


# --- the ladder's own behaviour ----------------------------------------------


def test_the_disclosure_needs_no_javascript(client, author, visual, newsroom):
    """Native <details>, so it opens with the keyboard and reads correctly
    to a screen reader without anything of ours running."""
    body = step(client, visual, "newsrooms").content.decode()
    assert "<details" in body
    assert "<summary" in body


def test_a_parent_checkbox_is_a_shortcut_not_a_value(client, author, visual, newsroom):
    """It sets the leaves and is never submitted. If it carried a value,
    checking a state would post a state where a list of newsrooms belongs."""
    body = step(client, visual, "newsrooms").content.decode()
    assert 'class="branch"' in body
    assert 'name="publishers"' in body
    branch_line = next(line for line in body.splitlines() if 'class="branch"' in line)
    assert "name=" not in branch_line, "a shortcut must not submit"


def test_every_parent_control_is_named_for_a_screen_reader(
    client, author, visual, newsroom
):
    body = step(client, visual, "newsrooms").content.decode()
    for line in body.splitlines():
        if 'class="branch"' in line:
            assert "aria-label=" in line


# --- the value facets --------------------------------------------------------


@pytest.fixture
def corpus(dataset, newsroom):
    """A handful of articles so a facet has something to count.

    Dated, paired and placed, because a pivot that returns nothing makes
    every assertion about what it returns vacuous: a walk that publishes
    an empty snapshot passes a test that only checks the feed answers.
    These are in March 2026, which is the range the walk asks for.
    """
    import datetime as dt

    from django.utils import timezone

    from explorer.models import Article, ArticleEnrichment, CandidateLink

    link = CandidateLink.objects.create(
        id="cl1", url="https://komu.example/1", source=newsroom
    )
    pairs = [
        ("Civic Life", "Sports", "29019", "Columbia"),
        ("Civic Life", "Civic Life", "29019", "Columbia"),
        ("Sports", "Civic Life", "29095", "Kansas City"),
    ]
    for i, (primary, alternate, geoid, place) in enumerate(pairs):
        article = Article.objects.create(
            id=f"a{i}",
            status="ok",
            candidate_link=link,
            primary_label=primary,
            alternate_label=alternate,
            # Aware, because the column is a datetime and a naive one is
            # read as UTC with a warning -- which puts an article an hour
            # either side of the range somebody actually asked for.
            publish_date=timezone.make_aware(dt.datetime(2026, 3, 10 + i, 12)),
        )
        ArticleEnrichment.objects.create(
            article=article,
            scope="local",
            point_place=place,
            point_geoid=geoid,
            point_geoid_level="county",
            point_lat=38.95 + i,
            point_lon=-92.33 - i,
            cost_usd="0.01",
        )
    return dataset


def test_a_chosen_dimension_offers_its_values_with_counts(
    client, author, visual, corpus
):
    """A facet without counts is a wall of checkboxes; the count is what
    says whether narrowing leaves anything to draw.

    Fetched when the facet is opened rather than while the step renders --
    it is one aggregate over the corpus per role, and running three of
    them before anything drew took the step to 65 seconds."""
    visual.config = {"kind": "bar"}
    visual.spec = {"roles": {"x": "cin_primary"}}
    visual.save()

    # The step itself draws without them...
    body = step(client, visual, "fields").content.decode()
    assert 'data-role="x"' in body, "no facet to open"
    assert "Civic Life" not in body, "the step should not be counting yet"

    # ...and opening the facet is what asks.
    values = client.get(f"/visuals/builder/{visual.slug}/values/x/").json()["values"]
    names = [v["value"] for v in values]
    assert "Civic Life" in names
    assert "Sports" in names
    assert all(isinstance(v["n"], int) for v in values), "counted, not just listed"


def test_a_measure_offers_no_values(client, author, visual, corpus):
    """A measure has a range, not a set of values to tick."""
    visual.config = {"kind": "bar"}
    visual.spec = {"roles": {"y": "articles"}}
    visual.save()
    body = step(client, visual, "fields").content.decode()
    assert 'name="only-articles"' not in body


def test_ticking_every_value_is_not_a_filter(client, author, visual, corpus):
    """Storing them all would go stale the moment one is added, which is
    why the newsroom step stores nothing for "all" either."""
    visual.config = {"kind": "bar"}
    visual.save()
    step(
        client,
        visual,
        "fields",
        **{
            "role-x": "cin_primary",
            "role-y": "articles",
            "only-cin_primary": ["Civic Life", "Sports"],
        },
    )
    visual.refresh_from_db()
    assert not (visual.spec.get("only") or {}).get("cin_primary")


def test_ticking_some_values_is_a_filter(client, author, visual, corpus):
    visual.config = {"kind": "bar"}
    visual.save()
    step(
        client,
        visual,
        "fields",
        **{
            "role-x": "cin_primary",
            "role-y": "articles",
            "only-cin_primary": ["Sports"],
        },
    )
    visual.refresh_from_db()
    assert visual.spec["only"]["cin_primary"] == ["Sports"]


def test_a_facet_that_cannot_be_counted_leaves_the_picker_working(
    client, author, visual
):
    """No crawler tables here at all. A facet that fails should not take
    the page down -- the variable can still be chosen."""
    visual.config = {"kind": "bar"}
    visual.spec = {"roles": {"x": "cin_primary"}}
    visual.save()
    assert step(client, visual, "fields").status_code == 200


def test_no_template_comment_leaks_into_the_page():
    """`{# #}` closes on its own line and nothing else does. One spanning
    five lines rendered as a card in the theme gallery, which no test of
    behaviour would catch -- it was visible only by looking."""
    from pathlib import Path

    steps = Path(__file__).resolve().parent.parent / "templates/visuals/steps"
    for template in steps.glob("*.html"):
        for number, line in enumerate(template.read_text().split("\n"), 1):
            if "{#" in line and "#}" not in line:
                raise AssertionError(
                    f"{template.name}:{number} opens {{# #}} and does not close "
                    "it on the same line; use {% comment %}"
                )


def test_every_class_the_panels_use_is_styled():
    """A template naming a class the stylesheet never carried renders as
    unstyled markup -- a list of checkboxes came out as one run-on line,
    which no test of behaviour would notice.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    css = (root / "static/css/datadesk.css").read_text()
    used = set()
    for template in (root / "templates/visuals/steps").glob("*.html"):
        for value in re.findall(r'class="([^"{}]+)"', template.read_text()):
            used |= {c for c in value.split() if c}
    # Anywhere in the stylesheet, not a standalone rule: several are hooks
    # that only ever appear in a compound selector -- `.node.state`,
    # `.row .label`. What this catches is a class nothing styles at all.
    missing = sorted(c for c in used if f".{c}" not in css)
    assert not missing, f"classes nothing styles: {missing}"


# --- the dataset picker at a hundred datasets --------------------------------


def test_the_dataset_picker_is_searchable_with_chips(client, author, visual, dataset):
    """A column of checkboxes is fine for four datasets and unusable for a
    hundred. The chips are what answers "what have I picked" without
    scrolling back through the list to find the ticks."""
    body = step(client, visual, "data").content.decode()
    assert 'id="dataset-find"' in body
    assert 'id="dataset-chips"' in body
    assert "of 1 chosen" in body


def test_the_chips_are_not_a_second_place_a_choice_lives(
    client, author, visual, dataset
):
    """The checkboxes are what submits. If a chip carried its own value,
    removing one would leave the box ticked and the two would disagree."""
    body = step(client, visual, "data").content.decode()
    start = body.index('id="dataset-chips"')
    chips = body[start : body.index("</div>", start)]
    assert "<input" not in chips, "a chip must not carry a value"
    assert 'name="datasets"' in body


def test_the_picker_opens_when_nothing_is_chosen(client, author, visual, dataset):
    """Closed on an empty picker hides the only thing to do on the step."""
    body = step(client, visual, "data").content.decode()
    assert '<details class="picker" open>' in body

    visual.spec = {"datasets": ["mizzou"]}
    visual.save()
    body = step(client, visual, "data").content.decode()
    assert '<details class="picker" open>' not in body


# --- what the page calls things ----------------------------------------------


def test_the_sentence_names_a_dataset_not_its_slug(visual, dataset):
    """The spec stores "mizzou" and the sentence should say "Missouri".
    Reading a key back to somebody is the schema talking."""
    from visuals.sentence import parts_for

    visual.config = {"kind": "chord"}
    visual.spec = {"datasets": ["mizzou"]}
    said = {text for _, text, kind in parts_for(visual) if kind == "said"}
    assert "Missouri" in said
    assert "mizzou" not in said


def test_a_dataset_that_no_longer_exists_falls_back_to_its_slug(visual, crawler_schema):
    """Better a key than an empty gap: a visual wired to something since
    removed should say so rather than read as though nothing was chosen."""
    from visuals.sentence import parts_for

    visual.config = {"kind": "chord"}
    visual.spec = {"datasets": ["gone"]}
    assert "gone" in {text for _, text, kind in parts_for(visual) if kind == "said"}


def test_a_slot_says_what_it_takes_in_plain_words(client, author, visual, dataset):
    """ "a category", not "text". The gallery already says it this way and
    the panel said it the other."""
    visual.config = {"kind": "chord"}
    visual.save()
    body = step(client, visual, "fields").content.decode()
    assert "a category" in body
    assert "&mdash; text" not in body and "— text" not in body


def test_the_pairing_note_names_the_slots_above_it(client, author, visual, dataset):
    """ "From and To", which is what they are called on screen. "from and
    to" reads as a fragment of the code."""
    visual.config = {"kind": "chord"}
    visual.save()
    body = step(client, visual, "fields").content.decode()
    assert "From and To must come from the same set of values" in body


def test_a_record_with_no_state_is_named_not_punctuated(
    client, author, visual, dataset
):
    """A missing state is not a place called "?" -- it is a record the scan
    already flags. And the label names the field: "NA State" over "NA
    County" says which one is short, where one word repeated says only that
    something is."""
    import uuid

    from explorer.models import DatasetSource, Source

    orphan = Source.objects.create(
        id=str(uuid.uuid4()),
        host="nowhere.example",
        host_norm="nowhere.example",
        canonical_name="Nowhere Gazette",
    )
    DatasetSource.objects.create(id=str(uuid.uuid4()), dataset=dataset, source=orphan)

    body = step(client, visual, "newsrooms").content.decode()
    assert "NA State" in body
    assert "NA County" in body
    assert ">?<" not in body


def test_the_missing_rows_sort_last(client, author, visual, newsroom):
    """The sentinel sorts before every real name, so without a key the row
    for what is absent would head the list. It is the exception, not the
    first thing to read."""
    import uuid

    from explorer.models import DatasetSource, Source

    orphan = Source.objects.create(
        id=str(uuid.uuid4()),
        host="none.example",
        host_norm="none.example",
        canonical_name="Nowhere Gazette",
    )
    DatasetSource.objects.create(
        id=str(uuid.uuid4()),
        dataset=newsroom.memberships.first().dataset,
        source=orphan,
    )
    body = step(client, visual, "newsrooms").content.decode()
    assert body.index("Missouri") < body.index("NA State")


def test_the_buttons_say_what_happens(client, author, visual, dataset):
    """ "Continue" and "Save and stay" describe the mechanism. "Next" moves
    on; "Update" stays -- and it is named for the reason to press it,
    which is that the preview redraws with what you just chose. "Save"
    read as filing something away and said nothing about the picture."""
    body = step(client, visual, "data").content.decode()
    assert ">Next<" in body
    assert ">Update</button>" in body
    assert "Continue" not in body and "Save and stay" not in body
    assert ">Save<" not in body


def test_the_two_numbers_on_a_row_are_explained(client, author, visual, newsroom):
    """ "3 · 0" says nothing on its own. What each number counts is said
    once above the tree and again on hover."""
    body = step(client, visual, "newsrooms").content.decode()
    assert "counties · newsrooms" in body
    assert "newsroom" in body and "article" in body


def test_one_county_is_not_one_counties(client, author, visual, newsroom):
    body = step(client, visual, "newsrooms").content.decode()
    assert "1 counties" not in body


# --- how somebody reaches the builder ----------------------------------------
#
# It was deployed and unreachable: live at its URL, linked from nowhere but
# a notice inside the old form. Code nobody can navigate to is not shipped.


def test_a_new_visual_lands_in_the_builder(client, author, crawler_schema):
    """Not in the form of ninety-one controls. That form still exists for
    what the steps do not cover, and every step links to it."""
    response = client.post(
        "/visuals/builder/new/",
        {"title": "Fresh", "slug": "fresh", "source_kind": "corpus"},
    )
    assert response.status_code == 302
    assert response.url == "/visuals/builder/fresh/step/type/"


def test_a_new_visual_has_no_chart_type_chosen(client, author, crawler_schema):
    """Seeding "table" answered step one's question before anybody was
    asked it, so the gallery opened with a choice nobody had made."""
    from visuals.models import Visual

    client.post(
        "/visuals/builder/new/",
        {"title": "Fresh2", "slug": "fresh2", "source_kind": "corpus"},
    )
    assert not (Visual.objects.get(slug="fresh2").config or {}).get("kind")


def test_the_index_links_to_the_builder(client, author, visual, crawler_schema):
    body = client.get("/visuals/").content.decode()
    assert f"/visuals/builder/{visual.slug}/step/type/" in body


def test_every_step_offers_the_way_back_to_the_old_form(
    client, author, visual, dataset
):
    """The steps do not cover everything yet -- annotations, publishing,
    the snapshot. Losing the form would lose those."""
    for name in ("type", "theme", "data", "newsrooms", "fields"):
        body = step(client, visual, name).content.decode()
        assert f"/visuals/builder/{visual.slug}/" in body


def test_a_corpus_visual_can_be_made_from_the_form(client, author, crawler_schema):
    """The builder's five steps are entirely about the corpus, and the
    creation form offered inline, BigQuery and a bucket -- so the thing the
    builder builds could not be created from the UI at all. Both visuals
    made so far were made from a shell."""
    response = client.post(
        "/visuals/builder/new/",
        {"title": "From the form", "slug": "from-the-form", "source_kind": "corpus"},
    )
    assert response.status_code == 302, "creation was refused"
    assert response.url.endswith("/step/type/")


def test_a_corpus_visual_is_not_snapshotted_at_creation(client, author, crawler_schema):
    """What it draws is decided by the steps. Asking for a snapshot now
    fails on a spec nobody has written."""
    from visuals.models import Visual

    client.post(
        "/visuals/builder/new/",
        {"title": "No snap", "slug": "no-snap", "source_kind": "corpus"},
    )
    assert not Visual.objects.get(slug="no-snap").snapshots.exists()


# --- which articles a visual may draw ----------------------------------------


def test_nothing_in_flight_is_ever_drawable(crawler_schema):
    """An article the pipeline has not finished with would change under the
    reader. That is a floor, not a filter somebody chooses -- so it holds
    whatever subset is asked for, including none."""
    from accounts.access import ALL_SCOPES
    from explorer.models import Article
    from visuals.corpus import _base_queryset

    Article.objects.create(id="a-paused", status="paused")
    Article.objects.create(id="a-done", status="labeled")

    for spec in ({}, {"subset": "complete"}, {"subset": "enriched"}):
        drawn = {a.id for a in _base_queryset(spec, ALL_SCOPES)}
        assert "a-paused" not in drawn, spec


def test_complete_keeps_what_the_export_drops(crawler_schema):
    """Obituaries, weather, opinion and paywall stubs are all terminal.
    They are the reason this reads Postgres rather than BigQuery, so the
    complete subset must not quietly drop them."""
    from accounts.access import ALL_SCOPES
    from explorer.models import Article
    from visuals.corpus import _base_queryset

    for status in ("obituary", "weather", "opinion", "paywall", "wire"):
        Article.objects.create(id=f"a-{status}", status=status)

    drawn = {a.status for a in _base_queryset({"subset": "complete"}, ALL_SCOPES)}
    assert {"obituary", "weather", "opinion", "paywall", "wire"} <= drawn


def test_enriched_is_what_reaches_enrichment(crawler_schema):
    """`enrichment_skipped` belongs with it: the skip is a decision at that
    stage rather than a diversion before it, and the two together are what
    the crawler exports."""
    from accounts.access import ALL_SCOPES
    from explorer.models import Article
    from visuals.corpus import _base_queryset

    for status in ("enriched", "enrichment_skipped", "obituary", "labeled"):
        Article.objects.create(id=f"e-{status}", status=status)

    drawn = {a.status for a in _base_queryset({"subset": "enriched"}, ALL_SCOPES)}
    assert drawn == {"enriched", "enrichment_skipped"}


def test_the_subset_is_a_filter_on_a_dataset_not_part_of_its_name():
    """The dataset says which newsrooms; the subset says how much of what
    they published. Two questions, so two controls."""
    from visuals.corpus import SUBSETS

    assert set(SUBSETS) == {"complete", "enriched"}
    for label, note in SUBSETS.values():
        assert "Missouri" not in label, "a subset is not named after a dataset"
        assert note.strip()


def test_the_sentence_says_which_subset(visual, dataset):
    """A chart of everything and a chart of the exported set look identical
    and mean different things."""
    from visuals.sentence import parts_for

    visual.config = {"kind": "bar"}
    visual.spec = {"datasets": ["mizzou"], "subset": "enriched"}
    assert "enriched" in {t for _, t, kind in parts_for(visual) if kind == "said"}


def test_the_data_step_offers_the_subset(client, author, visual, dataset):
    body = step(client, visual, "data").content.decode()
    assert 'name="subset"' in body
    assert "Complete" in body and "Enriched" in body


def test_an_unknown_subset_is_refused(client, author, visual, dataset):
    response = step(client, visual, "data", datasets=["mizzou"], subset="everything")
    visual.refresh_from_db()
    assert (visual.spec or {}).get("subset") != "everything"
    assert response.status_code == 200


# --- publishing, and the code somebody pastes --------------------------------


def test_the_embed_code_is_where_somebody_would_look_for_it(
    client, author, visual, dataset
):
    """It lived on the advanced-settings page. That page is where a
    visual's plumbing is changed; somebody looking for the code to paste
    was being sent to a page about data sources."""
    from visuals.models import Visual, VisualSnapshot

    VisualSnapshot.objects.create(
        visual=visual, version=1, data=[{"a": 1}], created_by=author
    )
    visual.status = Visual.PUBLISHED
    visual.save()

    body = step(client, visual, "publish").content.decode()
    assert "datadesk-visual" in body
    assert "data.localnewsimpact.org" in body


def test_an_empty_visual_cannot_be_published(client, author, visual, dataset):
    """An embed of it renders nothing on somebody else's page, which is
    worse than not existing."""
    body = step(client, visual, "publish").content.decode()
    assert "Nothing to publish yet" in body
    assert 'value="publish"' not in body


def test_the_publish_step_says_which_version_is_being_served(
    client, author, visual, dataset
):
    """A snapshot newer than the pinned one means the embed is serving
    something other than what the builder shows."""
    from visuals.models import Visual, VisualSnapshot

    old = VisualSnapshot.objects.create(
        visual=visual, version=1, data=[{"a": 1}], created_by=author
    )
    visual.pinned_snapshot = old
    visual.status = Visual.PUBLISHED
    visual.save()
    VisualSnapshot.objects.create(
        visual=visual, version=2, data=[{"a": 2}], created_by=author
    )

    body = step(client, visual, "publish").content.decode()
    assert "Pinned to version 1" in body
    assert "Version 2 exists and is not being served" in body


def test_the_settings_page_no_longer_publishes(client, author, visual, dataset):
    body = client.get(f"/visuals/builder/{visual.slug}/").content.decode()
    assert "Publish and get the embed code" in body
    assert 'name="form" value="publish"' not in body


# --- the preview is a renderer and needs what one needs ----------------------


def _complete_chord(visual):
    """A chord with no gaps left in its sentence, which is when the shell
    draws the preview rather than "Not enough yet"."""
    visual.config = {"kind": "chord", "theme": "datadesk"}
    visual.spec = {
        "dataset": "mizzou",
        "subset": "complete",
        "from": "2026-03-01",
        "to": "2026-03-31",
        # The field mapping lives on the spec, beside the slice it reads.
        "roles": {
            "from": "cin_primary",
            "to": "cin_alternate",
            "value": "articles",
        },
    }
    visual.save()
    return visual


def test_the_preview_fetches_a_real_url_and_loads_its_libraries(client, author, visual):
    """A renderer reads `{{ feed }}` and `{{ libs }}`, and Django resolves
    an undefined name to the empty string rather than raising. So a view
    that omits either renders a page that looks right and does nothing:

      * no `feed` -> `fetch("")` re-fetches the builder page itself, and
        the runtime parses HTML: "unexpected character at line 1 column 1"
      * no `libs` -> `"d3" in ""` is false, and no library loads at all

    The step shell was missed on both counts, because it is reached by
    `{% extends %}` rather than named in a view.
    """
    _complete_chord(visual)
    body = step(client, visual, "fields").content.decode()
    assert "Not enough yet" not in body, "the preview did not draw at all"

    assert 'fetch("")' not in body, "the preview would re-fetch its own page"
    assert f"/visuals/{visual.slug}/data.json" in body

    # A chord draws its own SVG, so d3 and nothing else.
    assert "d3.min" in body
    assert "plot.min" not in body


def test_the_preview_follows_the_data_rather_than_the_pin(client, author, visual):
    """It is a preview of what the chart will become."""
    _complete_chord(visual)
    body = step(client, visual, "fields").content.decode()
    assert "live=1" in body


# --- what the builder costs to walk through ----------------------------------


def test_the_newsroom_tree_is_not_rebuilt_on_every_visit(
    client, author, visual, newsroom
):
    """Building it counts every article in every dataset the visual is
    wired to. In production that step took 13 to 24 seconds, and the
    fields step after it 32 to 65, because both did their counting again
    on every arrival."""
    from django.core.cache import cache

    from visuals.views import _newsroom_tree

    cache.clear()
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["datasets"])

    first = _newsroom_tree(visual)
    with mock.patch("explorer.models.Article.objects") as never:
        again = _newsroom_tree(visual)
        assert not never.called, "the tree was rebuilt from the corpus"
    assert again == first


def test_a_cached_count_cannot_cross_between_what_two_people_may_read(crawler_schema):
    """The key decides who sees what. Scopes are the grant -- a key
    without them would hand one author counts over a dataset they hold no
    grant on, from a cache entry somebody else warmed."""
    from visuals.corpus import _cache_key

    # The key derives a corpus version, which reads the crawler tables --
    # hence the fixture. That version is the other half of this: it is
    # what lets the answers be kept for a week rather than minutes.
    spec = {"dataset": "mizzou", "from": "2026-03-01"}
    mine = _cache_key("corpus.values", "cin_primary", spec, ["mizzou"], 200)
    theirs = _cache_key("corpus.values", "cin_primary", spec, ["lehigh"], 200)
    everything = _cache_key("corpus.values", "cin_primary", spec, "*", 200)
    assert len({mine, theirs, everything}) == 3

    # And the spec itself: a different slice is a different answer.
    other = _cache_key(
        "corpus.values", "cin_primary", {**spec, "from": "2026-04-01"}, ["mizzou"], 200
    )
    assert other != mine


def test_a_counted_answer_is_kept_until_the_corpus_moves(crawler_schema):
    """These numbers change when the pipeline syncs -- at most every six
    hours, sometimes not for months. Expiring them on a timer pays for a
    recount every few minutes whether or not anything changed, and each
    recount is tens of seconds.

    So the key carries a version and the entry is kept for a week: a stale
    entry is not possible, only an unused one.
    """
    from django.core.cache import cache

    from visuals.corpus import CORPUS_CACHE_SECONDS, _cache_key, corpus_version

    assert CORPUS_CACHE_SECONDS >= 24 * 3600, "kept for a day at least"

    cache.delete("corpus.version")
    before = corpus_version()
    key_before = _cache_key("x", ["mizzou"])

    # Nothing changed: same version, same key, so the answer is reused.
    cache.delete("corpus.version")
    assert corpus_version() == before
    assert _cache_key("x", ["mizzou"]) == key_before

    # A newsroom joins a dataset. The counts move, so the key must.
    source = Source.objects.create(
        id="s-new", host="new.example", host_norm="new.example"
    )
    DatasetSource.objects.create(
        id="ds-new",
        dataset=Dataset.objects.create(id="d2", slug="d2", label="D2"),
        source=source,
    )
    cache.delete("corpus.version")
    assert corpus_version() != before
    assert _cache_key("x", ["mizzou"]) != key_before


def test_choosing_newsrooms_actually_narrows_the_chart(client, author, visual, corpus):
    """The step wrote `publishers` and nothing read it. A chart built
    after narrowing to one county was a chart of every newsroom in the
    dataset, and it looked right -- the panel said "934 of 1143 kept" and
    the picture did not change, because the picture never depended on it.
    """
    from accounts.access import ALL_SCOPES
    from explorer.models import Article, CandidateLink
    from visuals.corpus import _base_queryset

    # A second newsroom, so there is something for the filter to exclude.
    other = Source.objects.create(
        id="s2", host="two.example", host_norm="two.example", canonical_name="Two"
    )
    DatasetSource.objects.create(id="ds2", dataset=corpus, source=other)
    link = CandidateLink.objects.create(
        id="cl2", url="https://two.example/1", source=other
    )
    Article.objects.create(
        id="a9", status="ok", candidate_link=link, primary_label="Health"
    )

    everything = _base_queryset({}, ALL_SCOPES).count()
    assert everything == 4

    assert _base_queryset({"publishers": ["s1"]}, ALL_SCOPES).count() == 3
    assert _base_queryset({"publishers": ["s2"]}, ALL_SCOPES).count() == 1

    # Empty is "all", which is what the step stores rather than listing
    # every publisher -- a stored list of everything goes stale the moment
    # a newsroom is added.
    assert _base_queryset({"publishers": []}, ALL_SCOPES).count() == everything


def test_a_chart_carries_its_own_title(client, author, visual):
    """Nothing in the stepped flow could set the title a reader sees; only
    the advanced settings page could, and that page is about plumbing."""
    step(
        client,
        visual,
        "theme",
        theme="datadesk",
        title="How CIN needs pair up",
        subtitle="Primary against alternate, March 2026",
    )
    visual.refresh_from_db()
    assert visual.config["title"] == "How CIN needs pair up"
    assert visual.config["subtitle"] == "Primary against alternate, March 2026"
    # One title: the record answers to the same name the chart carries.
    assert visual.title == "How CIN needs pair up"


def test_the_title_box_opens_on_the_record_name(client, author, visual):
    """An empty box on a new visual invites leaving it empty."""
    body = step(client, visual, "theme").content.decode()
    assert 'name="title"' in body
    assert f'value="{visual.title}"' in body


# --- where a map is centred, in the flow that builds it ----------------------


@pytest.fixture
def two_newsrooms(dataset, newsroom):
    """A second newsroom, so selecting one is a subset rather than all."""
    other = Source.objects.create(
        id="s-kc2",
        host="kc2.example",
        host_norm="kc2.example",
        canonical_name="KC Star",
        county="Jackson",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds-kc2", dataset=dataset, source=other)
    return newsroom, other


def test_the_newsrooms_frame_the_map(client, author, visual, two_newsrooms):
    """Choosing whose coverage this is answers where the map is about.
    Asking again in another step was a second way to say the same thing,
    and the two could disagree -- which is how a copy of the Boone map
    retargeted at Jackson ended up framed on Adair, drawing nothing."""
    visual.config = {"kind": "storymap"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["config", "datasets"])

    boone, _ = two_newsrooms
    step(client, visual, "newsrooms", publishers=[boone.id])
    visual.refresh_from_db()

    # KOMU is in Boone County, so that is what the map paints.
    assert visual.config["frame"] == ["29019"]
    assert visual.config["focus"] == "", "no place was typed"


def test_an_override_survives_a_newsroom_change(client, author, visual, newsroom):
    """An override the next newsroom change silently undid would be worse
    than no override."""
    visual.config = {"kind": "storymap"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["config", "datasets"])

    step(client, visual, "newsrooms", publishers=[newsroom.id], focus="Jackson")
    visual.refresh_from_db()
    assert visual.config["focus_name"] == "Jackson"

    # Change the newsrooms; the typed place stays.
    step(client, visual, "newsrooms", publishers=[newsroom.id], focus="Jackson")
    visual.refresh_from_db()
    assert visual.config["focus_name"] == "Jackson"


def test_clearing_the_override_hands_the_map_back_to_the_newsrooms(
    client, author, visual, two_newsrooms
):
    visual.config = {"kind": "storymap", "focus": "29095", "focus_name": "Jackson"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["config", "datasets"])

    boone, _ = two_newsrooms
    step(client, visual, "newsrooms", publishers=[boone.id], focus="")
    visual.refresh_from_db()
    assert visual.config["focus"] == ""
    assert visual.config["frame"] == ["29019"], "back to the newsrooms' own county"


def test_a_chart_that_is_not_a_map_is_not_asked(client, author, visual, newsroom):
    """A bar chart has nowhere to be centred, and a control that does
    nothing is worse than no control."""
    visual.config = {"kind": "bar"}
    visual.save(update_fields=["config"])
    assert 'name="focus"' not in step(client, visual, "newsrooms").content.decode()


def test_a_place_name_is_resolved_to_the_code_the_map_needs(
    client, author, visual, newsroom
):
    """Nobody knows the FIPS code for their own county, and the boundary
    file is keyed by nothing else."""
    visual.config = {"kind": "storymap"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["config", "datasets"])
    step(client, visual, "newsrooms", publishers=[newsroom.id], focus="Boone")
    visual.refresh_from_db()

    assert visual.config["focus"] == "29019"
    assert visual.config["focus_level"] == "county"
    # The name is kept beside the code: without it the box came back
    # reading "29019" to somebody who typed "Boone".
    assert visual.config["focus_name"] == "Boone"


def test_an_empty_map_says_which_kind_of_empty_it_is():
    """ "No mapped stories" is true and useless. It does not distinguish a
    slice with no articles, newsrooms that published none in the window,
    and a map centred where none of the chosen newsrooms write -- which is
    what a duplicated map becomes when it is retargeted at one county and
    left filtered to another's newsrooms."""
    from pathlib import Path

    corpus = (Path(__file__).resolve().parent.parent / "visuals/corpus.py").read_text()
    js = (
        Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    ).read_text()

    assert "def _why_nothing_mapped(" in corpus
    # Counts, not a guess: "the newsrooms published nothing" is not
    # something a reader should take on trust when they know the county
    # has newspapers. Each filter is relaxed in turn and reported.
    assert "None of the {chosen:,} newsrooms chosen published" in corpus
    assert "though {ignoring_newsrooms:,} articles in this data did" in corpus
    assert "articles sit " in corpus
    # And the runtime prefers it to its own generic line.
    assert "empty_because" in js


def test_naming_a_chart_renames_it_everywhere(client, author, visual):
    """The record carried its own title, set when the visual was created
    and never again, so renaming a chart left the listing, the browser tab
    and the preview's heading showing what it had been called on the day
    it was made."""
    step(client, visual, "theme", theme="datadesk", title="How CIN needs pair up")
    visual.refresh_from_db()

    assert visual.config["title"] == "How CIN needs pair up"
    assert visual.title == "How CIN needs pair up"

    # The listing shows it...
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    assert "How CIN needs pair up" in client.get("/visuals/").content.decode()
    # ...and so does the heading above the preview.
    assert "How CIN needs pair up" in step(client, visual, "theme").content.decode()


def test_an_empty_title_leaves_the_record_named(client, author, visual):
    """A chart drawn without a heading is a choice; a record with no name
    is a row nobody can find."""
    was = visual.title
    step(client, visual, "theme", theme="datadesk", title="")
    visual.refresh_from_db()

    assert visual.config["title"] == ""
    assert visual.title == was


def test_the_empty_map_names_the_filter_that_empties_it(client, author, corpus):
    """A reader who knows the county has newspapers should not have to
    take "they published nothing" on trust. Whichever filter takes the
    total from something to nothing is the one reported."""
    from accounts.access import ALL_SCOPES
    from visuals.corpus import _why_nothing_mapped

    # A newsroom that exists but is not the one with articles.
    other = Source.objects.create(
        id="s-empty",
        host="quiet.example",
        host_norm="quiet.example",
        canonical_name="Quiet Weekly",
        county="Jackson",
    )
    DatasetSource.objects.create(id="ds-empty", dataset=corpus, source=other)

    said = _why_nothing_mapped({"publishers": ["s-empty"]}, ALL_SCOPES)
    assert "None of the 1 newsrooms chosen published" in said
    assert "3 articles in this data did" in said, "it should count the rest"

    # And a date range with nothing in it names the dates instead.
    said = _why_nothing_mapped({"from": "1999-01-01", "to": "1999-12-31"}, ALL_SCOPES)
    assert "Nothing published between 1999-01-01 and 1999-12-31" in said
    assert "3 articles sit" in said


def test_choosing_every_newsroom_stores_no_filter(client, author, visual, newsroom):
    """Everything ticked is not a filter. Stored in full it becomes a
    snapshot that goes stale the moment a newsroom is added -- one such
    spec holds 942 publishers across four states, which is every newsroom
    that existed on the day the box was ticked."""
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["datasets"])

    step(client, visual, "newsrooms", publishers=[newsroom.id])
    visual.refresh_from_db()
    assert visual.spec["publishers"] == [], "all of them means no filter"


def test_the_newsroom_step_clears_the_older_county_filter(
    client, author, visual, newsroom
):
    """`publisher_county` names the same thing from before this step
    existed, and nothing in the flow shows it. A map filtered to Jackson's
    newsrooms while still carrying `publisher_county: Boone` matches
    nothing, and the two are ANDed with no way to see the second."""
    visual.spec = {"publisher_county": "Boone", "publisher_city": "Columbia"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["spec", "datasets"])

    step(client, visual, "newsrooms", publishers=[newsroom.id])
    visual.refresh_from_db()
    assert visual.spec["publisher_county"] == ""
    assert visual.spec["publisher_city"] == ""


def test_two_newsroom_filters_cannot_contradict(client, author, corpus):
    """The failure they produced: articles exist for each filter alone and
    none for both, so the map drew nothing and blamed the newsrooms."""
    from accounts.access import ALL_SCOPES
    from visuals.corpus import _base_queryset

    other = Source.objects.create(
        id="s-kc",
        host="kc.example",
        host_norm="kc.example",
        canonical_name="KC Star",
        county="Jackson",
    )
    DatasetSource.objects.create(id="ds-kc", dataset=corpus, source=other)

    # Boone's newsrooms have articles; Jackson's newsroom is real.
    assert _base_queryset({"publisher_county": "Boone"}, ALL_SCOPES).count() == 3
    # Both together: the contradiction.
    both = {"publisher_county": "Boone", "publishers": ["s-kc"]}
    assert _base_queryset(both, ALL_SCOPES).count() == 0


def test_every_step_writes_every_key_it_owns(client, author, visual, two_newsrooms):
    """A step's save rebuilds its part of the query; it does not add to
    it. Keys are merged onto what is stored, so one a step owns and does
    not write on every save survives forever -- invisible, because no
    control shows it, and still filtering.

    That is `publisher_county`: set by a hand-authored visual, owned by
    nobody, ANDed with the newsroom selection. A map filtered to Jackson's
    newsrooms kept `publisher_county: Boone` and matched nothing.
    """
    from visuals.steps import STEPS

    boone, _ = two_newsrooms
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["datasets"])

    posts = {
        "type": {"kind": "storymap"},
        "theme": {"theme": "datadesk", "title": "T", "subtitle": "S"},
        "data": {
            "datasets": ["mizzou"],
            "subset": "complete",
            "from": "2026-03-01",
            "to": "2026-03-31",
        },
        "newsrooms": {"publishers": [boone.id]},
        # Not empty: `step()` treats an empty dict as a GET, which saves
        # nothing and would pass this test without exercising it.
        "fields": {"role-from": "cin_primary"},
    }
    for spec_step in (s for s in STEPS if s.slug in posts):
        step(client, visual, spec_step.slug, **posts[spec_step.slug])
        visual.refresh_from_db()
        held = {
            "config": visual.config or {},
            "spec": visual.spec or {},
            "visual": {"status": visual.status},
        }
        missing = [
            k for k in spec_step.owns if k.split(":")[1] not in held[k.split(":")[0]]
        ]
        assert not missing, (
            f"the {spec_step.slug} step owns {missing} and left them unwritten, "
            "so whatever was there before survives its save"
        )


def test_a_draft_page_can_draw_for_whoever_may_change_it(client, author, visual):
    """A draft has no pinned snapshot, so its own page -- the only page
    that shows a draft at all -- asked for one and got a 404 from the
    feed. Whoever may change the visual may see what it currently draws,
    which is the rule the builder's preview already uses."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    assert visual.status == Visual.DRAFT
    assert visual.snapshots.count() == 0

    body = client.get(f"/visuals/{visual.slug}/").content.decode()
    assert "data.json?live=1" in body, "the draft's page must ask for live data"


def test_a_chart_with_no_fields_says_so(client, author, visual):
    """A story map declares no roles: its geography comes from the
    enrichment rather than from columns somebody picks. The page rendered
    as a heading and a lone Update button, which reads as a step that
    failed to load."""
    step(client, visual, "type", kind="storymap")
    body = step(client, visual, "fields").content.decode()

    # Normalised: the sentence wraps in the template, so a literal match
    # looks for text no single line contains.
    flat = " ".join(body.split())
    assert "no fields to choose here" in flat
    assert "Pick a chart type first" not in flat


def test_the_newsroom_tree_follows_the_datasets_chosen(client, author, visual, dataset):
    """Reading only the singular `dataset` key left a visual wired to
    every dataset its author could read, so the tree offered newsrooms
    from four states to somebody who had chosen one."""
    from visuals.views import _wired_datasets

    assert _wired_datasets(author, {"datasets": ["mizzou"]}) == ["mizzou"]
    # A slug the author cannot read is not smuggled in by the plural.
    assert _wired_datasets(author, {"datasets": ["mizzou", "nope"]}) == ["mizzou"]
    # The older singular still works where it is all a visual has.
    assert _wired_datasets(author, {"dataset": "mizzou"}) == ["mizzou"]


# --- every surface a visual can be seen through ------------------------------
#
# The failures this exists to catch all had one shape: a view renders a
# chart, the chart fetches a feed, and the URL it was handed was wrong or
# absent. Each was found by a person opening a page.
#
#   fetch("")                   the builder preview, when no feed reached it
#   404 from the feed           a draft's own page, which never asks for live
#   no fields, just a button    a kind that declares no roles
#
# One matrix, not a test per instance: every kind against every view.

RENDERING_VIEWS = ("page", "type", "theme", "data", "newsrooms", "fields", "publish")


@pytest.mark.parametrize("kind", ["storymap", "chord", "bar", "table"])
def test_every_view_that_draws_a_chart_can_load_its_feed(
    client, author, visual, corpus, two_newsrooms, kind
):
    """A draft with no snapshot, which is what every visual is until it is
    published, and what a fresh copy is for as long as it takes to change
    something."""
    import re

    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)

    # A corpus visual, which is what the builder makes: live data runs the
    # pivot rather than reaching for BigQuery.
    visual.config = {"kind": kind, "theme": "datadesk"}
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {
        "datasets": ["mizzou"],
        "subset": "complete",
        "roles": {"x": "cin_primary"},
        "measure": "articles",
        "dimensions": ["cin_primary"],
    }
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])
    assert visual.snapshots.count() == 0, "the case that kept breaking"

    for where in RENDERING_VIEWS:
        url = (
            f"/visuals/{visual.slug}/"
            if where == "page"
            else f"/visuals/builder/{visual.slug}/step/{where}/"
        )
        page = client.get(url)
        assert page.status_code == 200, f"{kind} at {where}: {page.status_code}"
        body = page.content.decode()

        # Whatever it fetches must be a real URL, not the empty string --
        # `fetch("")` re-requests the page and the runtime parses HTML.
        for fetched in re.findall(r'fetch\("([^"]*)"\)', body):
            assert fetched, f"{kind} at {where} fetches nothing"
            feed = client.get(fetched)
            assert (
                feed.status_code == 200
            ), f"{kind} at {where} fetches {fetched} -> {feed.status_code}"


@pytest.mark.parametrize("kind", ["storymap", "chord", "bar", "table"])
def test_no_view_leaves_a_panel_empty(
    client, author, visual, corpus, two_newsrooms, kind
):
    """A step that renders a heading and a lone button reads as one that
    failed to load. Every step says something, whether it has controls to
    offer or not."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    visual.config = {"kind": kind, "theme": "datadesk"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["config", "datasets"])

    for where in ("type", "theme", "data", "newsrooms", "fields", "publish"):
        body = client.get(
            f"/visuals/builder/{visual.slug}/step/{where}/"
        ).content.decode()
        panel = body[body.index('class="build-rail"') :]
        panel = panel[: panel.index('class="build-stage"')]
        # Something to read or something to operate, in the panel itself.
        assert (
            "<p" in panel or "<label" in panel or "<select" in panel
        ), f"{kind} at {where}: the panel offers nothing"


def test_changing_an_option_changes_what_the_preview_draws(
    client, author, visual, corpus, two_newsrooms
):
    """The question the steps exist to answer: does pressing Update
    rebuild the query. Asserted on the rows the preview fetches, not on
    the page rendering."""
    import json

    boone, jackson = two_newsrooms
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)

    # An article from the second newsroom, so the two selections differ.
    from explorer.models import Article, CandidateLink

    link = CandidateLink.objects.create(
        id="cl-j", url="https://kc2.example/1", source=jackson
    )
    Article.objects.create(
        id="a-j", status="ok", candidate_link=link, primary_label="Health"
    )

    visual.config = {"kind": "bar", "theme": "datadesk"}
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {
        "datasets": ["mizzou"],
        "subset": "complete",
        "roles": {"x": "cin_primary"},
        "measure": "articles",
        "dimensions": ["cin_primary"],
    }
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])

    def drawn():
        feed = client.get(f"/visuals/{visual.slug}/data.json?live=1")
        assert feed.status_code == 200, feed.status_code
        return json.loads(feed.content)["data"]

    everything = drawn()
    labels = {row.get("CIN (primary)") for row in everything}
    assert {"Civic Life", "Sports", "Health"} <= labels, labels

    # Narrow to the second newsroom alone.
    step(client, visual, "newsrooms", publishers=[jackson.id])
    only_jackson = drawn()
    assert {row.get("CIN (primary)") for row in only_jackson} == {"Health"}

    # ...and back to the first.
    step(client, visual, "newsrooms", publishers=[boone.id])
    only_boone = drawn()
    assert {row.get("CIN (primary)") for row in only_boone} == {"Civic Life", "Sports"}

    # A date range with nothing in it empties it.
    step(
        client,
        visual,
        "data",
        datasets=["mizzou"],
        subset="complete",
        **{"from": "1999-01-01", "to": "1999-12-31"},
    )
    assert drawn() == []


def test_output_is_cached_by_publishing_it_and_not_otherwise(
    client, author, visual, corpus
):
    """A snapshot is the cached copy of an output: it carries a version,
    and `?v=` serves that version for as long as anybody asks. A second
    copy kept under the visual's slug is a cache with no version on it --
    nothing names which question it answers, and for five minutes after
    any change it answered the previous one."""
    from unittest import mock

    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {
        "datasets": ["mizzou"],
        "subset": "complete",
        "roles": {"x": "cin_primary"},
        "measure": "articles",
        "dimensions": ["cin_primary"],
    }
    visual.save(update_fields=["source_kind", "datasets", "spec"])

    url = f"/visuals/{visual.slug}/data.json?live=1"
    with mock.patch("visuals.views.fetch_source_data", return_value=[]) as ran:
        client.get(url)
        client.get(url)
    assert ran.call_count == 2, "the second read came from a cache"


def test_a_published_visual_is_served_from_its_snapshot(client, visual, author):
    """What caching an output looks like when publishing does it: the rows
    are stored once, under a version, and reading them re-runs nothing."""
    from visuals.models import VisualSnapshot
    from visuals.services import publish

    snapshot = VisualSnapshot.objects.create(
        visual=visual,
        version=1,
        data=[{"county": "Boone", "stories": 41}],
        created_by=author,
    )
    visual.pinned_snapshot = snapshot
    visual.save(update_fields=["pinned_snapshot"])
    publish(visual, author)

    with mock.patch("visuals.views.fetch_source_data") as never:
        feed = client.get(f"/visuals/{visual.slug}/data.json").json()
    assert not never.called, "a published feed must not re-run the query"
    assert feed["version"] == 1
    assert feed["data"] == [{"county": "Boone", "stories": 41}]


# --- one question, asked once ------------------------------------------------
#
# The preview asks the corpus a question and draws its answer. Between
# panels the question does not change, so the answer cannot either --
# but every panel asked it again, and four abandoned asks plus the one
# from Update ran together inside a single container until each was
# waiting on the other four. The first took 128 seconds. The same feed,
# with nothing piled on it, takes between one and three.


def _stamp_on(client, visual, step):
    """The name the page gives the question its preview is asking."""
    import re

    body = client.get(f"/visuals/builder/{visual.slug}/step/{step}/").content.decode()
    found = re.search(r'const stamp = "([^"]*)"', body)
    assert found, f"the {step} panel names no question"
    return found.group(1)


def _a_corpus_map(visual):
    visual.config = {"kind": "storymap", "theme": "datadesk"}
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {
        "datasets": ["mizzou"],
        "subset": "complete",
        "shape": "story_map",
        "roles": {},
        "measure": "articles",
        "dimensions": [],
        "publishers": [],
    }
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])
    return visual


def test_walking_the_panels_asks_the_corpus_one_question(
    client, author, visual, corpus, two_newsrooms
):
    """Chart, Look, Data, Newsrooms, Fields: the same question throughout.

    This is the whole defect. Four panels, four identical asks, none of
    them stopped by moving to the next panel.
    """
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)

    panels = ("type", "theme", "data", "fields")
    named = {s: _stamp_on(client, visual, s) for s in panels}
    assert all(named.values()), "a live preview must name its question"
    assert len(set(named.values())) == 1, f"the panels disagree: {named}"


def test_changing_the_newsrooms_changes_the_question(
    client, author, visual, corpus, two_newsrooms
):
    """...and changing one is what makes it ask again.

    A name that never changed would be a cache with no version on it --
    the thing that answered the previous question for five minutes.
    """
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    before = _stamp_on(client, visual, "newsrooms")

    one = two_newsrooms[0]
    client.post(
        f"/visuals/builder/{visual.slug}/step/newsrooms/",
        {"publishers": [str(one.id)], "stay": "1"},
    )
    visual.refresh_from_db()
    assert (visual.spec or {}).get("publishers") == [str(one.id)]
    assert _stamp_on(client, visual, "newsrooms") != before


def test_a_reader_is_never_handed_a_held_answer(
    client, author, visual, corpus, two_newsrooms
):
    """The embed draws a snapshot -- one answer, at a URL that says which
    version it is. Nothing is held for it, so nothing can be held
    wrongly, and a republish reaches whoever loads it next."""
    _a_corpus_map(visual)
    visual.status = Visual.PUBLISHED
    visual.save(update_fields=["status"])
    from visuals.services import record_snapshot

    snapshot = record_snapshot(visual, author, [{"county": "Boone", "stories": 41}])
    visual.pinned_snapshot = snapshot
    visual.save(update_fields=["pinned_snapshot"])

    from django.test import Client

    body = Client().get(f"/embed/{visual.slug}/").content.decode()
    assert "DatadeskChart.mount" in body, "the reader's embed draws nothing"
    assert 'const stamp = ""' in body, "a reader's embed must name no question"


def test_the_preview_script_is_javascript(
    client, author, visual, corpus, two_newsrooms
):
    """Rendered, then parsed. Everything the preview does lives in one
    inline script, so a syntax error in it is a blank chart on every
    panel -- and a template renders happily either way."""
    import re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to parse with")
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    page = client.get(f"/visuals/builder/{visual.slug}/step/newsrooms/")
    body = page.content.decode()
    scripts = re.findall(r"<script>(.*?)</script>", body, re.S)
    ours = [s for s in scripts if "DatadeskChart.mount" in s]
    assert ours, "the preview renders no script"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(ours[0])
        where = fh.name
    done = subprocess.run([node, "--check", where], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_live_preview_is_credited(client, author, visual, corpus, two_newsrooms):
    """The table view reads the owner and contact off the payload. Without
    them the preview was the one place a dataset's own attribution could
    not be checked before it was published."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    feed = client.get(f"/visuals/{visual.slug}/data.json?live=1").json()
    assert "attribution" in feed, "a live preview carries no credit"


# --- the map follows the newsrooms -------------------------------------------
#
# The focus box is rendered holding whatever focus is stored, so every
# save posted one back and every save read as somebody typing a place.
# A map that had ever been focused could never be re-framed by choosing
# different newsrooms again -- and the box doing it sits inside a fold
# that only opens when it is already set.


def _a_focused_map(visual, focus="jackson", geoid="29095"):
    visual.config = {
        "kind": "storymap",
        "theme": "datadesk",
        "focus": geoid,
        "focus_name": focus,
        "focus_level": "county",
    }
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {"datasets": ["mizzou"], "shape": "story_map", "publishers": []}
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])
    return visual


def test_choosing_newsrooms_reframes_a_map_that_was_already_focused(
    client, author, visual, corpus, two_newsrooms
):
    """The form posts the focus back because the form was given it. That
    is not somebody asking for it again."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_focused_map(visual)

    one = two_newsrooms[0]
    client.post(
        f"/visuals/builder/{visual.slug}/step/newsrooms/",
        # Exactly what the rendered page posts: the box holds the stored
        # focus, untouched.
        {
            "publishers": [str(one.id)],
            "focus": "jackson",
            "focus_level": "county",
            "stay": "1",
        },
    )
    visual.refresh_from_db()
    assert (
        visual.config.get("focus_name") != "jackson"
    ), "the newsroom choice could not reach the frame"
    assert visual.spec.get("publishers") == [str(one.id)]


def test_a_place_actually_typed_still_wins(
    client, author, visual, corpus, two_newsrooms
):
    """Saying something the box did not already say is the override, and
    a newsroom change must not silently undo it."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_focused_map(visual)

    one = two_newsrooms[0]
    client.post(
        f"/visuals/builder/{visual.slug}/step/newsrooms/",
        {
            "publishers": [str(one.id)],
            "focus": "Boone",
            "focus_level": "county",
            "stay": "1",
        },
    )
    visual.refresh_from_db()
    assert (visual.config.get("focus_name") or "").lower() == "boone"


def test_a_typed_place_can_be_kept_across_a_newsroom_change(
    client, author, visual, corpus, two_newsrooms
):
    """The newsroom change clears it, and typing it again keeps it --
    because by then it differs from what is stored. An override that
    cannot be re-stated after a newsroom change would not be one."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_focused_map(visual)
    where = f"/visuals/builder/{visual.slug}/step/newsrooms/"

    one, two = two_newsrooms[0], two_newsrooms[1]
    client.post(
        where,
        {
            "publishers": [str(one.id)],
            "focus": "jackson",
            "focus_level": "county",
            "stay": "1",
        },
    )
    visual.refresh_from_db()
    assert visual.config.get("focus_name") != "jackson", "the change did not land"

    client.post(
        where,
        {
            "publishers": [str(one.id)],
            "focus": "Jackson",
            "focus_level": "county",
            "stay": "1",
        },
    )
    visual.refresh_from_db()
    assert visual.config.get("focus_name") == "Jackson"

    # ...and it now survives, because the next change is to the focus box
    # or to nothing at all.
    client.post(
        where,
        {
            "publishers": [str(one.id), str(two.id)],
            "focus": "Jackson",
            "focus_level": "county",
            "stay": "1",
        },
    )
    visual.refresh_from_db()
    assert (
        visual.config.get("focus_name") != "Jackson"
    ), "moving the newsrooms again must re-frame it again"


# --- publishing something that has never been published ----------------------
#
# The step offered publishing only where a snapshot already existed --
# which is what publishing makes. Nothing built in the builder could get
# past it; the visuals in production that did were snapshotted through
# the refresh on the settings page, from before publishing lived here.


def test_a_visual_that_has_never_been_published_can_be(
    client, author, visual, corpus, two_newsrooms
):
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    # A date range, which is the one gap the sentence has left: the
    # question is whether a drawable visual can be published, not whether
    # an unfinished one can.
    visual.spec = dict(visual.spec, **{"from": "2026-03-01", "to": "2026-03-31"})
    visual.save(update_fields=["spec"])
    assert visual.snapshots.count() == 0, "the case that could not publish"

    where = f"/visuals/builder/{visual.slug}/step/publish/"
    body = client.get(where).content.decode()
    assert "Nothing to publish yet" not in body, "a drawable visual was called empty"

    client.post(where, {"do": "publish", "stay": "1"})
    visual.refresh_from_db()
    assert visual.status == Visual.PUBLISHED
    assert visual.pinned_snapshot is not None
    assert visual.pinned_snapshot.version == 1


def test_a_visual_with_gaps_left_still_cannot_be_published(
    client, author, visual, corpus, two_newsrooms
):
    """A chart whose fields are unmapped draws nothing, and an embed of
    nothing is worse on somebody's page than one that does not exist."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    visual.config = {"kind": "bar", "theme": "datadesk"}
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {"datasets": ["mizzou"], "roles": {}, "dimensions": []}
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])

    body = client.get(f"/visuals/builder/{visual.slug}/step/publish/").content.decode()
    assert "Nothing to publish yet" in body


# --- the walk, end to end ----------------------------------------------------
#
# Every defect found tonight was a step that worked on a visual carrying
# state from before it: publishing needed a snapshot only publishing
# makes, the focus box only yielded to newsrooms while it was empty, and
# the preview re-asked its question once per panel. None of them showed
# up in a test that set the spec directly and rendered one step, because
# that is a visual arriving already half-built.
#
# This starts at "New visual" with nothing and presses through to an
# embed a reader can load.


#: The kinds the corpus pivot cannot draw, and why. A pivot emits one
#: measure column per query, and both of these need two numbers in the
#: same row: a scatter plots one against the other, and a point map wants
#: a latitude and a longitude.
#:
#: A dot map used to be here and no longer is. Every county and place
#: GEOID has a centroid in the gazetteers this repo vendors, so a geo
#: dimension now carries its own coordinates and a dot map takes a place
#: rather than two numbers.
#:
#: Named here rather than left out, so that "not walked" is a statement
#: with a reason attached and the list cannot quietly grow.
NEEDS_TWO_MEASURES = {
    "scatter": "plots one measure against another; the pivot emits one",
}


#: What each kind needs at the fields step, in the words the form posts.
#: A story map and a table declare no roles: their shape comes out of the
#: data whole rather than from columns somebody picks.
FIELDS_FOR = {
    "storymap": {},
    "table": {"columns": ["cin_primary", "month"], "measure": "articles"},
    "bar": {"role-x": "cin_primary", "role-y": "articles"},
    "donut": {"role-x": "cin_primary", "role-y": "articles"},
    "chord": {
        "role-from": "cin_primary",
        "role-to": "cin_alternate",
        "role-value": "articles",
    },
    "arc": {
        "role-from": "cin_primary",
        "role-to": "cin_alternate",
        "role-value": "articles",
    },
    "line": {"role-x": "month", "role-y": "articles"},
    "area": {"role-x": "month", "role-y": "articles"},
    # Both axes are numbers, so both are measures: cost against articles
    # is the pair a corpus can actually plot against itself.
    "scatter": {
        "role-x": "cost_sum",
        "role-y": "articles",
        "role-series": "publisher",
    },
    "choropleth": {"role-geo_join": "geo_county", "role-geo_value": "articles"},
    # A point map wants a latitude and a longitude. The corpus offers no
    # such variable -- the only numbers it has are counts -- so what
    # fills those slots here is what the fields step actually offers,
    # which is a real gap in what a point map can be built out of and a
    # separate question from whether the walk works.
    # A place, and how big to draw each dot. Where the places are comes
    # from the pivot, which writes the centroid of a geo dimension beside
    # it.
    "points": {
        "role-place": "geo_county",
        "role-size": "articles",
        "role-label": "point_place",
    },
}


@pytest.mark.parametrize("kind", sorted(set(FIELDS_FOR) - set(NEEDS_TWO_MEASURES)))
def test_a_visual_walked_from_nothing_reaches_a_working_embed(
    client, author, corpus, two_newsrooms, dataset, kind
):
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    one, two = two_newsrooms

    made = client.post(
        "/visuals/builder/new/",
        {"title": f"Walked {kind}", "source_kind": "corpus"},
    )
    assert made.status_code in (302, 303), made.status_code
    fresh = Visual.objects.get(slug=f"walked-{kind}")
    assert fresh.snapshots.count() == 0
    assert not fresh.config.get("kind"), "step one has not been asked yet"

    def press(name, **fields):
        got = client.post(
            f"/visuals/builder/{fresh.slug}/step/{name}/", dict(fields, stay="1")
        )
        assert got.status_code in (200, 302), f"{name}: {got.status_code}"
        fresh.refresh_from_db()
        return got

    press("type", kind=kind)
    assert fresh.config["kind"] == kind

    press("theme", theme="datadesk", theme_mode="light")
    assert fresh.config["theme"] == "datadesk"

    press(
        "data",
        datasets=[dataset.slug],
        subset="complete",
        **{"from": "2026-03-01", "to": "2026-03-31"},
    )
    assert fresh.spec["datasets"] == [dataset.slug]

    # One of the two, so this is a choice rather than everything.
    press("newsrooms", publishers=[str(one.id)], focus="", focus_level="")
    assert fresh.spec["publishers"] == [str(one.id)]
    # ...and where it is a map, choosing it framed the map, with nobody
    # typing a place.
    if kind == "storymap":
        assert fresh.config.get("frame"), "the newsroom choice did not frame the map"

    # What this kind needs drawn, which for a story map or a table is
    # nothing -- their shape comes out of the data whole.
    press("fields", **FIELDS_FOR[kind])

    # The chart has to name columns the rows actually carry. The fields
    # step writes `spec["roles"]`, which names variables by id; the
    # renderer is handed `config` and draws column names, and the pivot
    # emits its columns under their display labels. Writing only the
    # roles left every chart built here with its fields chosen and no
    # idea which columns they were -- a feed that loads and a chart that
    # draws nothing, which is exactly what a test asserting 200 misses.
    feed = client.get(f"/visuals/{fresh.slug}/data.json?live=1").json()
    rows = feed["data"]
    if isinstance(rows, list) and rows:
        named = [
            fresh.config.get(role.id)
            for role in BY_ID[kind].roles
            if role.needs and fresh.config.get(role.id)
        ]
        assert len(named) == len(
            [r for r in BY_ID[kind].roles if r.needs]
        ), f"{kind} chose its fields and named no columns: {fresh.config}"
        for column in named:
            assert column in rows[0], (
                f"{kind} draws {column!r}, which the rows do not have: "
                f"{sorted(rows[0])}"
            )

    body = client.get(f"/visuals/builder/{fresh.slug}/step/publish/").content.decode()
    assert "Nothing to publish yet" not in body, "walked to publish, called empty"
    press("publish", do="publish")
    assert fresh.status == Visual.PUBLISHED
    assert fresh.pinned_snapshot is not None

    # What a reader loads, from a different client with no session at all.
    from django.test import Client

    reader = Client()
    page = reader.get(f"/embed/{fresh.slug}/")
    assert page.status_code == 200
    import re

    for url in re.findall(r'fetch\("([^"]*)"\)', page.content.decode()):
        assert url, "the embed fetches nothing"
        feed = reader.get(url)
        assert feed.status_code == 200, f"{url}: {feed.status_code}"
        assert feed.json()["version"] == 1


def test_a_table_with_no_columns_is_not_publishable(
    client, author, visual, corpus, two_newsrooms
):
    """A table groups by whatever is ticked, so a table with nothing
    ticked draws nothing -- and the sentence at the top of the page has
    no gap for it, because columns are not one of the things it says."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    visual.config = {"kind": "table", "theme": "datadesk"}
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {
        "datasets": ["mizzou"],
        "subset": "complete",
        "from": "2026-03-01",
        "to": "2026-03-31",
        "dimensions": [],
        "roles": {},
    }
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])

    body = client.get(f"/visuals/builder/{visual.slug}/step/publish/").content.decode()
    assert (
        "Nothing to publish yet" in body
    ), "an empty table offered itself for publishing"


def test_every_chart_kind_is_walked():
    """The matrix has to stay complete on its own.

    Three of the kinds walked here could not be built at all, and each
    was found only because something walked it. A kind added later and
    left out of the table above would be exactly as broken and exactly as
    quiet, so being left out is itself a failure.
    """
    from visuals.types import CHART_TYPES

    missing = sorted({c.id for c in CHART_TYPES} - set(FIELDS_FOR))
    assert not missing, f"these kinds are never walked: {missing}"
    # And a kind excused the walk says why, so the excused list is a
    # statement about the pivot rather than a place to put failures.
    assert set(NEEDS_TWO_MEASURES) <= set(FIELDS_FOR)
    assert all(NEEDS_TWO_MEASURES.values()), "an excused kind with no reason"


@pytest.mark.parametrize("kind", sorted(NEEDS_TWO_MEASURES))
def test_the_kinds_the_pivot_cannot_draw_say_so(
    client, author, visual, corpus, two_newsrooms, kind
):
    """Both need two numbers in one row and the pivot emits one, so the
    columns they name cannot both be there. Asserting the shortfall keeps
    it visible: the day the pivot emits two, this test fails and the kind
    rejoins the walk."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    visual.config = {"kind": kind, "theme": "datadesk"}
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {
        "datasets": ["mizzou"],
        "subset": "complete",
        "from": "2026-03-01",
        "to": "2026-03-31",
    }
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])
    client.post(
        f"/visuals/builder/{visual.slug}/step/fields/",
        dict(FIELDS_FOR[kind], stay="1"),
    )
    visual.refresh_from_db()

    rows = client.get(f"/visuals/{visual.slug}/data.json?live=1").json()["data"]
    assert rows, "nothing to measure the shortfall against"
    named = [
        visual.config.get(role.id)
        for role in BY_ID[kind].roles
        if role.needs and visual.config.get(role.id)
    ]
    absent = [c for c in named if c not in rows[0]]
    assert absent, (
        f"{kind} now has every column it names -- the pivot emits more than "
        f"one measure, so put it back in the walk: {sorted(rows[0])}"
    )


def test_a_duplicate_walks_on_its_own_and_leaves_the_original_serving(
    client, author, corpus, two_newsrooms, dataset
):
    """Duplicating is for iterating on something that is live. The copy
    is a draft that has never been published, which is the state that
    could not publish at all -- and the original has to keep serving the
    version it was pinned at while somebody works on the copy."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    one, two = two_newsrooms

    client.post(
        "/visuals/builder/new/", {"title": "The Original", "source_kind": "corpus"}
    )
    first = Visual.objects.get(slug="the-original")

    def press(visual, name, **fields):
        got = client.post(
            f"/visuals/builder/{visual.slug}/step/{name}/", dict(fields, stay="1")
        )
        assert got.status_code in (200, 302), f"{name}: {got.status_code}"
        visual.refresh_from_db()

    def walk(visual, publishers):
        press(visual, "type", kind="bar")
        press(visual, "theme", theme="datadesk", theme_mode="light")
        press(
            visual,
            "data",
            datasets=[dataset.slug],
            subset="complete",
            **{"from": "2026-03-01", "to": "2026-03-31"},
        )
        press(visual, "newsrooms", publishers=publishers, focus="", focus_level="")
        press(visual, "fields", **FIELDS_FOR["bar"])

    walk(first, [str(one.id)])
    press(first, "publish", do="publish")
    assert first.status == Visual.PUBLISHED
    pinned = first.pinned_snapshot.version

    copy_of = client.post(f"/visuals/builder/{first.slug}/duplicate/")
    assert copy_of.status_code in (302, 303)
    copy = Visual.objects.exclude(pk=first.pk).get(slug__startswith=first.slug)
    assert copy.status == Visual.DRAFT
    assert copy.snapshots.count() == 0, "a copy starts with nothing published"

    # Point the copy somewhere else and publish it on its own.
    press(copy, "newsrooms", publishers=[str(two.id)], focus="", focus_level="")
    assert copy.spec["publishers"] == [str(two.id)]
    body = client.get(f"/visuals/builder/{copy.slug}/step/publish/").content.decode()
    assert "Nothing to publish yet" not in body
    press(copy, "publish", do="publish")
    assert copy.pinned_snapshot.version == 1

    # ...and none of that touched what the original is serving.
    first.refresh_from_db()
    assert first.pinned_snapshot.version == pinned
    assert first.spec["publishers"] == [str(one.id)]


def test_republishing_makes_a_new_version_and_serves_it(
    client, author, corpus, two_newsrooms, dataset
):
    """The version is what an embed pins to, so a change that never
    reaches a new one is a change nobody reading it can see."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    one, two = two_newsrooms

    client.post(
        "/visuals/builder/new/", {"title": "Twice Over", "source_kind": "corpus"}
    )
    v = Visual.objects.get(slug="twice-over")

    def press(name, **fields):
        got = client.post(
            f"/visuals/builder/{v.slug}/step/{name}/", dict(fields, stay="1")
        )
        assert got.status_code in (200, 302), f"{name}: {got.status_code}"
        v.refresh_from_db()

    press("type", kind="bar")
    press("theme", theme="datadesk", theme_mode="light")
    press(
        "data",
        datasets=[dataset.slug],
        subset="complete",
        **{"from": "2026-03-01", "to": "2026-03-31"},
    )
    press("newsrooms", publishers=[str(one.id)], focus="", focus_level="")
    press("fields", **FIELDS_FOR["bar"])
    press("publish", do="publish")
    assert v.pinned_snapshot.version == 1

    press("newsrooms", publishers=[str(one.id), str(two.id)], focus="", focus_level="")
    press("publish", do="publish")
    assert v.pinned_snapshot.version == 2, "republishing served the old rows again"

    from django.test import Client

    reader = Client()
    feed = reader.get(f"/visuals/{v.slug}/data.json")
    assert feed.status_code == 200
    assert feed.json()["version"] == 2


# --- which version an address asks for ---------------------------------------


def test_an_embed_without_a_version_follows_what_is_published(
    client, author, visual, corpus, two_newsrooms
):
    """Two promises off one visual: a chart that stays current, and one
    cited in a piece that has to keep saying what it said."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    visual.spec = dict(visual.spec, **{"from": "2026-03-01", "to": "2026-03-31"})
    visual.save(update_fields=["spec"])
    client.post(
        f"/visuals/builder/{visual.slug}/step/publish/", {"do": "publish", "stay": "1"}
    )
    visual.refresh_from_db()

    body = client.get(f"/visuals/builder/{visual.slug}/step/publish/").content.decode()
    import json
    import re

    found = re.search(r'id="embed-snippets"[^>]*>(.*?)</script>', body, re.S)
    assert found, "the publish step hands over no snippet"
    snippets = json.loads(found.group(1))

    for theme in ("auto", "light", "dark"):
        assert "v=" not in snippets[f"{theme}|latest"], f"{theme}: latest is pinned"
        assert "v=1" in snippets[f"{theme}|pinned"], f"{theme}: pinned has no version"

    # ...and the choice is on the page, not just in the payload.
    assert 'name="embed-version"' in body


def test_asking_for_a_version_that_exists_serves_that_one_forever(
    client, author, visual, corpus, two_newsrooms
):
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    visual.spec = dict(visual.spec, **{"from": "2026-03-01", "to": "2026-03-31"})
    visual.save(update_fields=["spec"])
    where = f"/visuals/builder/{visual.slug}/step/publish/"
    client.post(where, {"do": "publish", "stay": "1"})
    client.post(
        f"/visuals/builder/{visual.slug}/step/newsrooms/",
        {"publishers": [str(two_newsrooms[0].id)], "stay": "1"},
    )
    client.post(where, {"do": "publish", "stay": "1"})
    visual.refresh_from_db()
    assert visual.pinned_snapshot.version == 2

    from django.test import Client

    reader = Client()
    # No version: whatever is published.
    assert reader.get(f"/visuals/{visual.slug}/data.json").json()["version"] == 2
    # A version: that one, and cacheable for a year because it cannot change.
    one = reader.get(f"/visuals/{visual.slug}/data.json?v=1")
    assert one.json()["version"] == 1
    assert "immutable" in one["Cache-Control"]
    # One that does not exist is refused rather than quietly redirected.
    assert reader.get(f"/visuals/{visual.slug}/data.json?v=9").status_code == 404


def test_a_place_carries_its_own_coordinates(client, author, visual, corpus):
    """A pivot grouped by a county or a place writes where that place is,
    from the Census gazetteer's internal point. This is what lets a dot
    map take a place: latitude and longitude as two separate measures
    could never both be filled, a pivot having one measure to give."""
    from visuals.corpus import LAT_LABEL, LON_LABEL, run_spec

    rows, _meta = run_spec(
        {
            "datasets": ["mizzou"],
            "subset": "complete",
            "dimensions": ["geo_county"],
            "measure": "articles",
        },
        frozenset(["mizzou"]),
    )
    assert rows, "nothing grouped by county"
    for row in rows:
        assert LAT_LABEL in row and LON_LABEL in row, f"no coordinates: {row}"
        # Missouri, not the Gulf of Guinea: a lookup that misses must
        # leave the columns out rather than store a pair of zeroes.
        assert 35 < row[LAT_LABEL] < 41, row
        assert -96 < row[LON_LABEL] < -89, row


def test_a_pivot_with_no_geography_carries_no_coordinates(client, author, corpus):
    """Columns that mean "where this is" have no meaning on a row that is
    not anywhere -- a CIN need is not a place."""
    from visuals.corpus import LAT_LABEL, run_spec

    rows, _meta = run_spec(
        {
            "datasets": ["mizzou"],
            "subset": "complete",
            "dimensions": ["cin_primary"],
            "measure": "articles",
        },
        frozenset(["mizzou"]),
    )
    assert rows
    assert all(LAT_LABEL not in row for row in rows)


# --- whose name is on it, and which answer it is -----------------------------


def test_the_look_step_offers_the_owner_by_name(
    client, author, visual, corpus, two_newsrooms, dataset
):
    """Not "credit the dataset owner", which asks somebody to choose a
    name they cannot see."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    dataset.owner_name = "Missouri School of Journalism"
    dataset.owner_email = "data@example.org"
    dataset.save(update_fields=["owner_name", "owner_email"])
    _a_corpus_map(visual)

    body = client.get(f"/visuals/builder/{visual.slug}/step/theme/").content.decode()
    assert "Local News Impact Consortium" in body
    assert "Missouri School of Journalism" in body

    client.post(
        f"/visuals/builder/{visual.slug}/step/theme/",
        {"theme": "datadesk", "title": "T", "credit": "dataset", "stay": "1"},
    )
    visual.refresh_from_db()
    assert visual.config["credit"] == "dataset"

    page = client.get(f"/visuals/{visual.slug}/").content.decode()
    assert "Missouri School of Journalism" in page
    assert "mailto:data@example.org" in page


def test_a_chart_with_no_recorded_owner_is_not_asked_who_to_credit(
    client, author, visual, corpus, two_newsrooms
):
    """There is nobody to credit, and a chart crediting a blank is worse
    than one crediting the consortium."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    body = client.get(f"/visuals/builder/{visual.slug}/step/theme/").content.decode()
    assert 'name="credit"' not in body


def test_a_published_feed_says_which_answer_it_is_and_when(
    client, author, visual, corpus, two_newsrooms
):
    """The version is a property of the rows, not of the page framing
    them: an embed pinned to v3 says v3 wherever it is pasted."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _a_corpus_map(visual)
    visual.spec = dict(visual.spec, **{"from": "2026-03-01", "to": "2026-03-31"})
    visual.save(update_fields=["spec"])
    client.post(
        f"/visuals/builder/{visual.slug}/step/publish/", {"do": "publish", "stay": "1"}
    )

    import re

    from django.test import Client

    feed = Client().get(f"/visuals/{visual.slug}/data.json").json()
    assert feed["version"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", feed["taken"]), feed.get("taken")


# --- a chart built from a file somebody uploaded -----------------------------
#
# Not article data. A survey, a census table, anything related to the
# work and not produced by the pipeline. The walk was corpus-shaped --
# its Data step asks which articles and its Newsrooms step asks whose --
# so a file could be uploaded and never built into anything.


SURVEY_CSV = (
    "Source,None,Very little,Some,Quite a bit,A lot\n"
    "Local TV news station,8,14,27,28,23\n"
    "Local daily newspaper,31,24,23,14,9\n"
    "Local radio station,13,23,32,19,13\n"
)


def _upload(client, title, csv_text=SURVEY_CSV):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        "/visuals/builder/new/",
        {
            "title": title,
            "source_kind": "inline",
            "file": SimpleUploadedFile(
                "survey.csv", csv_text.encode(), content_type="text/csv"
            ),
        },
    )


def test_a_chart_is_built_from_an_uploaded_file(client, author):
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    assert _upload(client, "Where People Get News").status_code in (302, 303)
    v = Visual.objects.get(slug="where-people-get-news")
    assert v.snapshots.count() == 1, "the file is the first version"

    def press(name, **fields):
        got = client.post(
            f"/visuals/builder/{v.slug}/step/{name}/", dict(fields, stay="1")
        )
        assert got.status_code in (200, 302), f"{name}: {got.status_code}"
        v.refresh_from_db()

    # The walk it has: no newsroom step, because a file has no newsrooms
    # and whoever made it decided that already.
    rail = client.get(f"/visuals/builder/{v.slug}/step/type/").content.decode()
    assert "Newsrooms" not in rail
    assert client.get(f"/visuals/builder/{v.slug}/step/newsrooms/").status_code == 404

    # The data step is the file, and says how it read it.
    import re as _re

    data = client.get(f"/visuals/builder/{v.slug}/step/data/").content.decode()
    flat = _re.sub(r"\s+", " ", data)
    assert "3 rows, 6 columns" in flat, "the file step does not say what is in it"
    assert "Quite a bit" in flat, "the columns are not listed"

    press("type", kind="bar")
    press("theme", theme="datadesk", title="Where people get news", theme_mode="light")
    # The file's own columns, not the corpus's dimensions.
    press("fields", **{"role-x": "Source", "role-y": "A lot"})
    assert v.spec["roles"] == {"x": "Source", "y": "A lot"}
    assert v.config["x"] == "Source" and v.config["y"] == "A lot"

    # ...and it draws them.
    feed = client.get(f"/visuals/{v.slug}/data.json?live=1").json()
    assert feed["data"][0]["Source"] == "Local TV news station"
    assert feed["data"][0]["A lot"] == 23

    body = client.get(f"/visuals/builder/{v.slug}/step/publish/").content.decode()
    assert "Nothing to publish yet" not in body
    press("publish", do="publish")
    assert v.status == Visual.PUBLISHED

    from django.test import Client

    reader = Client()
    page = reader.get(f"/embed/{v.slug}/")
    assert page.status_code == 200
    import re

    for url in re.findall(r'fetch\("([^"]*)"\)', page.content.decode()):
        assert reader.get(url).status_code == 200


def test_an_uploaded_file_types_its_own_columns(client, author):
    """The type is what decides which charts a file can draw, so it is
    read from the values and shown at the data step -- somebody who meant
    a column of FIPS codes as geography should see that it was read that
    way, rather than find out at the fields step that it is not offered."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _upload(
        client,
        "Census Table",
        "County,FIPS,Month,Households\n"
        "Boone,29019,2026-03,48210\n"
        "Jackson,29095,2026-03,283900\n",
    )
    v = Visual.objects.get(slug="census-table")
    from visuals.panels import variables

    kinds = {c["id"]: c["kind"] for c in variables(v)}
    assert kinds == {
        "County": "text",
        "FIPS": "geo",
        "Month": "date",
        "Households": "number",
    }

    # ...and a choropleth can therefore be built from it, which is the
    # point: a census table joins on FIPS.
    fields = client.get(f"/visuals/builder/{v.slug}/step/fields/")
    client.post(
        f"/visuals/builder/{v.slug}/step/type/", {"kind": "choropleth", "stay": "1"}
    )
    body = client.get(f"/visuals/builder/{v.slug}/step/fields/").content.decode()
    assert fields.status_code == 200
    assert "FIPS" in body and "Households" in body


def test_replacing_the_file_is_a_new_version(client, author):
    """The rows are the snapshot, so a corrected file is the next answer
    to the same question -- and `?v=1` keeps serving the one somebody has
    already cited."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _upload(client, "Twice Uploaded")
    v = Visual.objects.get(slug="twice-uploaded")

    corrected = SURVEY_CSV.replace("8,14,27,28,23", "9,14,27,28,22")
    client.post(
        f"/visuals/builder/{v.slug}/step/data/",
        {
            "file": SimpleUploadedFile(
                "survey.csv", corrected.encode(), content_type="text/csv"
            ),
            "stay": "1",
        },
    )
    v.refresh_from_db()
    assert v.snapshots.count() == 2
    assert v.snapshots.order_by("-version").first().data[0]["None"] == 9
    assert v.snapshots.order_by("version").first().data[0]["None"] == 8


# --- the picker offers what the file can actually fill -----------------------


def test_the_picker_and_the_fields_step_agree_about_every_column(client, author):
    """Two inferences would be two answers to "what is this column", and
    the picker would grey out a chart the fields step could fill. A
    census table hit exactly that: its FIPS column parses as an integer,
    so one called it a number and the other geography."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _upload(
        client,
        "Agreeing",
        "County,FIPS,Month,Households,Note\n"
        "Boone,29019,2026-03,48210,n/a\n"
        "Jackson,29095,2026-04,283900,fine\n",
    )
    v = Visual.objects.get(slug="agreeing")

    from visuals.panels import _rows, variables
    from visuals.types import column_types

    picker = column_types(_rows(v))
    fields = {c["id"]: c["kind"] for c in variables(v)}
    assert picker == fields, "the picker and the fields step disagree"
    assert fields["FIPS"] == "geo"
    assert fields["Households"] == "number"
    assert fields["Month"] == "date"
    assert fields["Note"] == "text"


def test_a_file_with_no_geography_is_told_how_a_map_will_find_its_places(
    client, author
):
    """Not refused: a choropleth joins on county names as well as codes,
    so a file without codes can still draw one. But a name has to match
    the gazetteer's spelling and a code cannot be spelt wrong, and the
    difference between them is a map that comes out blank."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _upload(client, "No Places")  # the survey: a label and five counts
    v = Visual.objects.get(slug="no-places")

    from visuals.panels import _rows
    from visuals.types import column_types, gallery

    entries = {e["id"]: e for e in gallery(column_types(_rows(v)))}
    assert not entries["bar"]["why_not"], "a bar needs a category and a number"
    assert "FIPS" in entries["choropleth"]["caution"], entries["choropleth"]
    assert not entries["bar"]["caution"], "a bar has no places to find"

    body = client.get(f"/visuals/builder/{v.slug}/step/type/").content.decode()
    assert "joins on names" in body, "the caution is not on the page"


def test_a_census_table_is_offered_a_choropleth(client, author):
    """The point of reading FIPS as geography: a table keyed by it joins
    to the boundaries."""
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    _upload(
        client,
        "Census Places",
        "County,FIPS,Households\nBoone,29019,48210\nJackson,29095,283900\n",
    )
    v = Visual.objects.get(slug="census-places")

    from visuals.panels import _rows
    from visuals.types import column_types, gallery

    entries = {e["id"]: e for e in gallery(column_types(_rows(v)))}
    assert not entries["choropleth"]["why_not"], entries["choropleth"]["why_not"]


# --- an ordered series is a scale --------------------------------------------


def test_the_look_step_offers_a_scale_only_where_there_is_a_series(
    client, author, visual, corpus, two_newsrooms
):
    Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    visual.config = {"kind": "bar", "theme": "datadesk"}
    visual.source_kind = "corpus"
    visual.datasets = ["mizzou"]
    visual.spec = {"datasets": ["mizzou"], "roles": {"x": "month", "y": "articles"}}
    visual.save(update_fields=["config", "source_kind", "datasets", "spec"])
    where = f"/visuals/builder/{visual.slug}/step/theme/"
    assert 'name="series_scale"' not in client.get(where).content.decode()

    visual.spec = dict(visual.spec, roles={**visual.spec["roles"], "series": "wire"})
    visual.save(update_fields=["spec"])
    assert 'name="series_scale"' in client.get(where).content.decode()

    client.post(
        where,
        {"theme": "datadesk", "title": "T", "series_scale": "sequential", "stay": "1"},
    )
    visual.refresh_from_db()
    assert visual.config["series_scale"] == "sequential"


def test_an_ordered_series_is_drawn_as_one_hue_light_to_dark():
    """Read out of the runtime, not asserted about the source. Five
    unrelated hues say five unrelated things; a scale is one hue getting
    darker, in the order the values appear."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to run the runtime in")

    harness = """
    global.window = global;
    global.document = {
      addEventListener() {}, querySelectorAll: () => [],
      documentElement: { dataset: { theme: "light" } },
    };
    global.matchMedia = () => ({ matches: false });
    RUNTIME
    const levels = ["None", "Very little", "Some", "Quite a bit", "A lot"];
    const t = DatadeskChart.__test.theme("datadesk");
    const asScale = DatadeskChart.__test.scaleColors(levels, t);
    const asSet = DatadeskChart.__test.colorScale(levels, t, false);
    console.log(JSON.stringify({
      scale: asScale.range, set: asSet.range,
      seqLow: t.seqLow, seqHigh: t.seqHigh,
      tooMany: DatadeskChart.__test.scaleColors(
        Array.from({length: 12}, (_, i) => "v" + i), t),
      tooFew: DatadeskChart.__test.scaleColors(["only"], t),
    }));
    """.replace("RUNTIME", Path("static/js/datadesk-chart.js").read_text())

    done = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    got = json.loads(done.stdout)

    # Five steps for five levels, each distinct, running low to high.
    assert len(got["scale"]) == 5
    assert len(set(got["scale"])) == 5, got["scale"]
    assert got["scale"][0].lower() == got["seqLow"].lower()
    assert got["scale"][-1].lower() == got["seqHigh"].lower()

    # ...and monotonic: every step darker than the one before it, which
    # is what makes it readable as an order rather than as a set.
    def luminance(hex_colour):
        r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    lums = [luminance(c) for c in got["scale"]]
    assert lums == sorted(lums, reverse=True), lums

    # The categorical range it replaces is not a progression at all.
    assert got["set"] != got["scale"]

    # A scale nobody can follow is not a scale, and one value is not one
    # either: both fall back to the categorical hues.
    assert got["tooMany"] is None
    assert got["tooFew"] is None
