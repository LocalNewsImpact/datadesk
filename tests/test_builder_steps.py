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
    """A handful of articles so a facet has something to count."""
    from explorer.models import Article, CandidateLink

    link = CandidateLink.objects.create(
        id="cl1", url="https://komu.example/1", source=newsroom
    )
    for i, label in enumerate(["Civic Life", "Civic Life", "Sports"]):
        Article.objects.create(
            id=f"a{i}", status="ok", candidate_link=link, primary_label=label
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


def test_the_newsrooms_frame_the_map(client, author, visual, newsroom):
    """Choosing whose coverage this is answers where the map is about.
    Asking again in another step was a second way to say the same thing,
    and the two could disagree -- which is how a copy of the Boone map
    retargeted at Jackson ended up framed on Adair, drawing nothing."""
    visual.config = {"kind": "storymap"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["config", "datasets"])

    step(client, visual, "newsrooms", publishers=[newsroom.id])
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
    client, author, visual, newsroom
):
    visual.config = {"kind": "storymap", "focus": "29095", "focus_name": "Jackson"}
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["config", "datasets"])

    step(client, visual, "newsrooms", publishers=[newsroom.id], focus="")
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
