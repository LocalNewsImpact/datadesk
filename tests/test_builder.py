"""The form-driven builder (SCOPE.md §2.6 v2): config validation, CSV
typing, the create/edit flow, and publish-from-builder."""

import io
import json

import pytest
from django.contrib.auth.models import Group, User

from visuals.builder import BuilderError, config_from_form, parse_upload
from visuals.models import Visual

pytestmark = pytest.mark.django_db

CSV = "county,fips,stories,share\nBoone,29019,41,0.31\nAdair,29001,7,0.05\n"


@pytest.fixture
def editor(client):
    user = User.objects.create_user("editor", email="editor@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="editor"))
    client.force_login(user)
    return user


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    return user


def _create(client, title="Story counties"):
    return client.post(
        "/visuals/builder/new/",
        {
            "title": title,
            "source_kind": "inline",
            "file": io.BytesIO(CSV.encode()),
        },
    )


# --- the pieces --------------------------------------------------------------


def test_csv_columns_are_typed():
    rows = parse_upload(io.BytesIO(CSV.encode()))
    assert rows[0] == {
        "county": "Boone",
        "fips": 29019,
        "stories": 41,
        "share": 0.31,
    }


def test_config_whitelists_and_validates():
    config = config_from_form(
        {
            "kind": "choropleth",
            "geo_level": "counties",
            "geo_join": "fips",
            "geo_value": "stories",
            "geo_fit": "1",
            "subtitle": "March",
            "bogus": "dropped",
            "x": "",
        }
    )
    assert config == {
        "kind": "choropleth",
        "geo_level": "counties",
        "geo_join": "fips",
        "geo_value": "stories",
        "geo_fit": True,
        "subtitle": "March",
    }
    with pytest.raises(BuilderError):
        config_from_form({"kind": "pie"})


# --- the flow ----------------------------------------------------------------


def test_create_makes_a_draft_with_a_snapshot(client, editor):
    response = _create(client)
    assert response.status_code == 302
    visual = Visual.objects.get()
    assert visual.slug == "story-counties"
    assert visual.template == "builder"
    assert visual.status == Visual.DRAFT
    snapshot = visual.snapshots.get()
    assert snapshot.version == 1
    assert snapshot.data[0]["fips"] == 29019


def test_slugs_do_not_collide(client, editor):
    _create(client)
    _create(client)
    slugs = set(Visual.objects.values_list("slug", flat=True))
    assert slugs == {"story-counties", "story-counties-2"}


def test_config_saves_and_renders_into_the_feed_page(client, editor):
    _create(client)
    client.post(
        "/visuals/builder/story-counties/",
        {
            "form": "config",
            "kind": "choropleth",
            "geo_level": "counties",
            "geo_join": "fips",
            "geo_value": "stories",
        },
    )
    visual = Visual.objects.get()
    assert visual.config["geo_join"] == "fips"
    page = client.get("/visuals/story-counties/")
    assert "dd-config" in page.content.decode()


def test_publish_from_builder_pins_and_serves_the_embed(client, editor):
    _create(client)
    client.post("/visuals/builder/story-counties/", {"form": "publish"})
    visual = Visual.objects.get()
    assert visual.status == Visual.PUBLISHED
    assert visual.pinned_snapshot.version == 1

    client.logout()
    embed = client.get("/embed/story-counties/")
    assert embed.status_code == 200
    content = embed.content.decode()
    assert "dd-config" in content
    assert "View data" in content  # the relief/table view is always there
    feed = client.get("/visuals/story-counties/data.json").json()
    assert feed["version"] == 1
    assert feed["data"][0]["county"] == "Boone"


def test_replace_data_versions_up(client, editor):
    _create(client)
    client.post(
        "/visuals/builder/story-counties/",
        {
            "form": "upload",
            "file": io.BytesIO(b"county,fips,stories\nBoone,29019,60\n"),
        },
    )
    visual = Visual.objects.get()
    assert visual.snapshots.count() == 2


def test_bad_csv_leaves_no_orphan_visual(client, editor):
    response = client.post(
        "/visuals/builder/new/",
        {
            "title": "Broken",
            "source_kind": "inline",
            "file": io.BytesIO("hé".encode("latin-1")),
        },
    )
    assert response.status_code == 200
    assert "Not UTF-8" in response.content.decode()
    assert Visual.objects.count() == 0


def test_viewers_cannot_build(client, viewer):
    assert client.get("/visuals/builder/new/").status_code == 403


def test_builder_config_json_is_escaped_into_page(client, editor):
    _create(client)
    client.post(
        "/visuals/builder/story-counties/",
        {
            "form": "config",
            "kind": "bar",
            "x": "county",
            "y": "stories",
            "subtitle": "</script><script>alert(1)</script>",
        },
    )
    page = client.get("/visuals/story-counties/")
    content = page.content.decode()
    assert "<script>alert(1)</script>" not in content
    config = Visual.objects.get().config
    assert json.loads(json.dumps(config))["kind"] == "bar"
