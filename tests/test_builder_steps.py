"""Walking the builder, one step at a time.

The rule the whole design rests on: a step writes only the keys it owns, so
going back changes one choice and leaves the rest. These walk it rather than
asserting it in the abstract, because the failure it guards against is
silent -- a form that quietly empties when somebody looks at another chart
type teaches them not to explore.
"""

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
