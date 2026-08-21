"""Dataset management (SCOPE.md §2.4): gazetteer-validated sources,
membership with audit, the profile contract, build requests."""

import json

import pytest
from django.contrib.auth.models import Group, User

from audit.models import AuditLogEntry
from datasets.models import GazetteerBuildRequest
from datasets.places import validate_city
from datasets.profiles import ProfileError, requires_version_bump, validate_profile
from explorer.models import Dataset, DatasetSource, Source
from review.services import revert

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def admin(client):
    user = User.objects.create_user("boss", email="boss@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="admin"))
    client.force_login(user)
    return user


@pytest.fixture
def editor(client):
    user = User.objects.create_user("editor", email="editor@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="editor"))
    client.force_login(user)
    return user


@pytest.fixture
def dataset(crawler_schema):
    return Dataset.objects.create(
        id="d1", slug="missouri", label="Missouri", meta={"default_state": "MO"}
    )


# --- the Census place gazetteer ---------------------------------------------


def test_gazetteer_knows_real_places():
    assert validate_city("MO", "Kirksville") == (True, [])
    assert validate_city("MO", "Columbia") == (True, [])


def test_gazetteer_catches_the_march_typos():
    known, suggestions = validate_city("MO", "Kirskville")
    assert not known
    assert "Kirksville" in suggestions
    known, suggestions = validate_city("MO", "Grenfield")
    assert not known
    assert "Greenfield" in suggestions


# --- the exit test: a typo'd city is refused at entry ------------------------


def test_source_with_typo_city_is_refused_with_suggestion(client, admin, dataset):
    response = client.post(
        "/manage/sources/new/",
        {"host": "kirksvilledailyexpress.com", "city": "Kirskville", "state": "MO"},
    )
    assert response.status_code == 400
    assert "Did you mean: Kirksville" in response.content.decode()
    assert Source.objects.count() == 0


def test_valid_source_is_created_audited_and_joined(client, admin, dataset):
    response = client.post(
        "/manage/sources/new/",
        {
            "host": "Kirksvilledailyexpress.com",
            "canonical_name": "Kirksville Daily Express",
            "city": "Kirksville",
            "state": "MO",
            "dataset": "missouri",
        },
    )
    assert response.status_code == 302
    source = Source.objects.get()
    assert source.host_norm == "kirksvilledailyexpress.com"
    assert source.meta == {"state": "MO"}
    assert DatasetSource.objects.filter(
        dataset_id=dataset.id, source_id=source.id
    ).exists()
    actions = set(AuditLogEntry.objects.values_list("action", flat=True))
    assert actions == {"source:create", "dataset:add_source"}


def test_source_edit_validates_too(client, admin, crawler_schema):
    source = Source.objects.create(
        id="s1", host="x.com", host_norm="x.com", meta={"state": "MO"}
    )
    response = client.post(
        f"/manage/sources/{source.id}/", {"city": "Grenfield", "state": "MO"}
    )
    assert response.status_code == 400
    client.post(
        f"/manage/sources/{source.id}/",
        {"city": "Greenfield", "state": "MO", "reason": "typo fix"},
    )
    source.refresh_from_db()
    assert source.city == "Greenfield"


# --- datasets ----------------------------------------------------------------


def test_dataset_create_starts_cron_off(client, admin, crawler_schema):
    client.post("/manage/datasets/new/", {"slug": "lehigh", "label": "Lehigh Valley"})
    dataset = Dataset.objects.get(slug="lehigh")
    assert dataset.cron_enabled is False
    assert AuditLogEntry.objects.filter(action="dataset:create").exists()


def test_membership_remove_is_audited_and_revertible(client, admin, dataset):
    source = Source.objects.create(id="s1", host="x.com", host_norm="x.com")
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    client.post(
        f"/manage/datasets/{dataset.slug}/",
        {"form": "remove_source", "source_id": "s1", "reason": "left the study"},
    )
    assert DatasetSource.objects.count() == 0
    entry = AuditLogEntry.objects.get(action="dataset:remove_source")
    revert(admin, entry)
    assert DatasetSource.objects.filter(id="ds1").exists()


def test_default_state_lands_in_metadata(client, admin, dataset):
    client.post(
        f"/manage/datasets/{dataset.slug}/",
        {"form": "fields", "name": "Missouri", "default_state": "mo"},
    )
    dataset.refresh_from_db()
    assert dataset.meta["default_state"] == "MO"


# --- the profile contract ----------------------------------------------------


def test_profile_schema_is_enforced():
    validate_profile({"version": 2, "scope": True, "metadata_presets": ["topic"]})
    with pytest.raises(ProfileError):
        validate_profile({"version": 0})
    with pytest.raises(ProfileError):
        validate_profile({"version": 1, "scopes": True})  # unknown key
    with pytest.raises(ProfileError):
        validate_profile({"version": 1, "metadata_presets": ["vibes"]})
    with pytest.raises(ProfileError):
        validate_profile({"version": 1, "steady_state_since": "March 1"})


def test_version_bump_contract():
    old = {"version": 3, "scope": True}
    assert requires_version_bump(old, {"version": 3, "scope": False})
    assert not requires_version_bump(old, {"version": 4, "scope": False})
    assert not requires_version_bump(old, {"version": 3, "scope": True})


def test_profile_editor_refuses_content_change_without_bump(client, admin, dataset):
    Dataset.objects.filter(pk=dataset.pk).update(
        meta={"enrichment_profile": {"version": 1, "scope": True}}
    )
    client.post(
        f"/manage/datasets/{dataset.slug}/",
        {"form": "profile", "profile": json.dumps({"version": 1, "scope": False})},
    )
    dataset.refresh_from_db()
    assert dataset.meta["enrichment_profile"]["scope"] is True  # unchanged
    response = client.get(f"/manage/datasets/{dataset.slug}/")
    assert "version did not" in response.content.decode()


def test_profile_editor_saves_valid_bumped_profile(client, admin, dataset):
    client.post(
        f"/manage/datasets/{dataset.slug}/",
        {"form": "profile", "profile": json.dumps({"version": 1, "scope": True})},
    )
    dataset.refresh_from_db()
    assert dataset.meta["enrichment_profile"] == {"version": 1, "scope": True}
    # default_state survived the profile write.
    assert dataset.meta["default_state"] == "MO"


# --- gazetteer build requests ------------------------------------------------


def test_build_request_records_and_flags_new_state(client, admin, dataset):
    response = client.get(f"/manage/datasets/{dataset.slug}/")
    assert "Geofabrik state extract" in response.content.decode()
    client.post(f"/manage/datasets/{dataset.slug}/", {"form": "gazetteer_build"})
    build = GazetteerBuildRequest.objects.get()
    assert build.dataset_slug == "missouri"
    assert build.state == "MO"
    assert "populate-gazetteer --dataset missouri" in build.command


# --- access ------------------------------------------------------------------


def test_editors_cannot_manage_datasets(client, editor, dataset):
    assert client.get("/manage/datasets/").status_code == 403


def test_anonymous_is_redirected(client):
    assert client.get("/manage/datasets/").status_code == 302
