"""Dataset management (SCOPE.md §2.4): gazetteer-validated sources,
membership with audit, the profile contract, build requests."""

import json

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
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
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    client.force_login(user)
    return user


@pytest.fixture
def editor(client):
    user = User.objects.create_user("editor", email="editor@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
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


# --- county normalization ----------------------------------------------------


def test_county_canonicalization_keeps_city_and_county_apart():
    """Missouri's St. Louis city and St. Louis County are different
    places; folding away "city" would silently merge them."""
    from datasets.geo import canonical_county

    assert canonical_county("MO", "St Louis") == ("29189", "St. Louis")
    assert canonical_county("MO", "SAINT LOUIS COUNTY") == ("29189", "St. Louis")
    assert canonical_county("MO", "St. Louis city") == ("29510", "St. Louis city")
    assert canonical_county("MO", "SAINTE GENEVIEVE COUNTY") == (
        "29186",
        "Ste. Genevieve",
    )
    assert canonical_county("MO", "Nowhere") == (None, None)


def test_normalize_classifies_clean_rewrite_and_review():
    from datasets.management.commands.normalize_counties import classify

    assert classify("MO", "Boone")[0] == "clean"
    kind, canonical, _ = classify("MO", "st louis county")
    assert (kind, canonical) == ("rewrite", "St. Louis")
    kind, _, detail = classify("MO", "Jasper and Newton")
    assert kind == "review"
    assert "several counties" in detail
    kind, _, detail = classify("MO", "Grenfeld")
    assert kind == "review"
    assert "no gazetteer match" in detail
    assert classify("MO", "")[0] == "review"


def test_normalize_command_reports_then_applies(client, admin, crawler_schema):
    from io import StringIO

    from django.core.management import call_command

    from audit.models import AuditLogEntry

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    messy = Source.objects.create(
        id="s1", host="a.com", host_norm="a.com", county="st louis county"
    )
    fine = Source.objects.create(
        id="s2", host="b.com", host_norm="b.com", county="Boone"
    )
    both = Source.objects.create(
        id="s3", host="c.com", host_norm="c.com", county="Jasper and Newton"
    )
    for i, source in enumerate((messy, fine, both)):
        DatasetSource.objects.create(id=f"ds{i}", dataset=dataset, source=source)

    out = StringIO()
    call_command("normalize_counties", dataset="mo", stdout=out)
    report = out.getvalue()
    assert "1 clean · 1 to rewrite · 1 need review" in report
    assert "Jasper and Newton" in report
    messy.refresh_from_db()
    assert messy.county == "st louis county"  # reported only

    out = StringIO()
    call_command(
        "normalize_counties", dataset="mo", apply=True, actor=admin.email, stdout=out
    )
    messy.refresh_from_db()
    both.refresh_from_db()
    assert messy.county == "St. Louis"
    assert both.county == "Jasper and Newton"  # never touched
    entry = AuditLogEntry.objects.get(action="source:normalize_county")
    assert entry.before == {"s1": {"county": "st louis county"}}


def test_normalize_diagnoses_wrong_state_and_city_values():
    """A dead end is usually a county in the next state or a city."""
    from datasets.management.commands.normalize_counties import classify

    kind, _, detail = classify("MO", "Wyandotte")
    assert kind == "review"
    assert "county in KS" in detail
    kind, _, detail = classify("MO", "Kansas City")
    assert kind == "review"
    assert "is a city, not a county" in detail
    assert "Jackson" in detail


def test_place_to_county_uses_the_place_internal_point():
    """Kansas City spans four counties; lowest-FIPS would say Cass."""
    from datasets.geo import county_for_place

    assert county_for_place("2938000") == ("29095", 4)


# --- proposing a change without the right to make it -------------------------
#
# Editing a source is a dataset privilege: an admin or an editor changes the
# record. A viewer who knows something true about it -- an owner who sold, a
# paper that folded -- has no write, and until there was a form for it had
# nowhere to put what they knew.


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("looker", email="looker@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="missouri", role="viewer")
    client.force_login(user)
    return user


@pytest.fixture
def member_source(dataset):
    source = Source.objects.create(
        id="s-lex",
        host="lexingtonnews.example",
        host_norm="lexingtonnews.example",
        canonical_name="The Lexington News",
        owner="Independent",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    return source


def test_a_viewer_cannot_edit_the_record(client, viewer, member_source):
    """The write path is unchanged and still refuses them."""
    response = client.post(
        f"/manage/sources/{member_source.id}/",
        {"owner": "Lexington Area Chamber of Commerce", "state": "MO"},
    )
    assert response.status_code in (302, 403)
    member_source.refresh_from_db()
    assert member_source.owner == "Independent"


def test_a_viewer_proposes_and_the_corpus_is_untouched(client, viewer, member_source):
    """The whole point: what they know is recorded, and nothing is written."""
    from review.proposals import ChangeProposal

    response = client.post(
        f"/manage/sources/{member_source.id}/propose/",
        {
            "canonical_name": "The Lexington News",
            "city": "",
            "county": "",
            "owner": "Lexington Area Chamber of Commerce",
            "type": "",
            "state": "MO",
            "citation": "https://example.test/chamber-buys-papers",
            "detail": "Announced in the Advance in March.",
        },
    )
    assert response.status_code == 302

    member_source.refresh_from_db()
    assert member_source.owner == "Independent", "proposing must not write"

    proposals = list(ChangeProposal.objects.filter(record_id=member_source.id))
    assert [p.field for p in proposals] == ["owner"], "only what differs"
    (owner,) = proposals
    assert owner.current_value == "Independent"
    assert owner.proposed_value == "Lexington Area Chamber of Commerce"
    assert owner.state == ChangeProposal.PENDING
    assert owner.flag == "reported"


def test_a_proposal_names_the_person_and_the_evidence(client, viewer, member_source):
    """An edit offered to somebody else's dataset is worth what knowing who
    offered it is worth."""
    from review.proposals import ChangeProposal

    client.post(
        f"/manage/sources/{member_source.id}/propose/",
        {
            "owner": "Lexington Area Chamber of Commerce",
            "state": "MO",
            "citation": "https://example.test/chamber-buys-papers",
        },
    )
    proposal = ChangeProposal.objects.get(record_id=member_source.id)
    assert proposal.proposed_by == viewer
    assert proposal.citation == "https://example.test/chamber-buys-papers"


def test_a_proposal_without_evidence_is_refused(client, viewer, member_source):
    """A reviewer deciding on somebody's word needs to see the word."""
    from review.proposals import ChangeProposal

    response = client.post(
        f"/manage/sources/{member_source.id}/propose/",
        {"owner": "Lexington Area Chamber of Commerce", "state": "MO"},
    )
    assert response.status_code == 400
    assert not ChangeProposal.objects.filter(record_id=member_source.id).exists()


def test_a_proposal_that_changes_nothing_is_refused(client, viewer, member_source):
    """An empty proposal is a queue item somebody has to read and dismiss."""
    from review.proposals import ChangeProposal

    response = client.post(
        f"/manage/sources/{member_source.id}/propose/",
        {
            "canonical_name": "The Lexington News",
            "owner": "Independent",
            "state": "MO",
            "citation": "https://example.test/nothing-changed",
        },
    )
    assert response.status_code == 400
    assert not ChangeProposal.objects.filter(record_id=member_source.id).exists()


def test_the_proposal_lands_in_the_datasets_queue(client, viewer, member_source):
    """'In their dataset': the proposal carries the dataset the proposer
    reaches the source through, so it is reviewed by that dataset's people
    rather than by whoever opens the queue first."""
    from review.proposals import ChangeProposal

    client.post(
        f"/manage/sources/{member_source.id}/propose/",
        {
            "owner": "Lexington Area Chamber of Commerce",
            "state": "MO",
            "citation": "https://example.test/chamber-buys-papers",
        },
    )
    assert ChangeProposal.objects.get(record_id=member_source.id).dataset == "missouri"


def test_a_source_outside_the_readable_datasets_cannot_be_proposed_on(
    client, viewer, crawler_schema
):
    """A 404 rather than a 403: telling somebody a record exists but is not
    theirs says more than the guard is willing to."""
    other = Source.objects.create(
        id="s-other", host="elsewhere.example", host_norm="elsewhere.example"
    )
    response = client.get(f"/manage/sources/{other.id}/propose/")
    assert response.status_code == 404


def test_a_viewer_reaches_the_form_from_the_publisher_list(
    client, viewer, member_source
):
    """A form nobody can navigate to is not a feature. The dataset admin is
    an administration surface and stays one; this is the read surface a
    viewer already has."""
    page = client.get("/explorer/sources/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "The Lexington News" in body
    assert f"/manage/sources/{member_source.id}/propose/" in body


def test_the_publisher_list_filters_by_directory(client, viewer, member_source):
    """The list is capped at two hundred and the corpus holds 1,149 across
    four states, so a name somebody half remembers was findable only by
    typing enough of it -- and "every publisher in Vermont" could not be
    asked for at all."""
    from accounts.models import DATADESK, Grant

    other = Dataset.objects.create(id="d-vt", slug="vermont", label="Vermont")
    Grant.objects.get_or_create(
        user=viewer, app=DATADESK, scope="vermont", role="viewer"
    )
    green = Source.objects.create(
        id="s-green",
        host="greenmountain.example",
        host_norm="greenmountain.example",
        canonical_name="The Green Mountain Times",
        meta={"state": "VT"},
    )
    DatasetSource.objects.create(id="ds-vt", dataset=other, source=green)

    both = client.get("/explorer/sources/").content.decode()
    assert "The Lexington News" in both and "The Green Mountain Times" in both

    one = client.get("/explorer/sources/?dataset=vermont").content.decode()
    assert "The Green Mountain Times" in one
    assert "The Lexington News" not in one

    # The picker offers the directories, so nobody has to know a slug.
    assert 'value="vermont"' in both


def test_the_directory_picker_offers_only_what_may_be_read(
    client, viewer, member_source
):
    """A picker listing a dataset somebody cannot choose is an invitation
    to a 403, and the guard would then refuse them for picking what they
    were shown. Choosing one anyway filters to nothing of theirs rather
    than reaching past the grant."""
    Dataset.objects.create(id="d-hidden", slug="hidden", label="Not Yours")
    hidden = Source.objects.create(
        id="s-hidden2",
        host="hidden2.example",
        host_norm="hidden2.example",
        canonical_name="Nobody Sees This",
    )
    DatasetSource.objects.create(
        id="ds-hidden",
        dataset=Dataset.objects.get(slug="hidden"),
        source=hidden,
    )

    body = client.get("/explorer/sources/").content.decode()
    assert 'value="hidden"' not in body

    # And picking one anyway is refused by the guard every dataset-shaped
    # page already goes through -- told, rather than shown a page that
    # looks like the dataset is empty.
    assert client.get("/explorer/sources/?dataset=hidden").status_code == 403


def test_the_publisher_list_shows_only_readable_datasets(client, viewer, member_source):
    """The same scoping as everything else: no grant, no record."""
    Source.objects.create(
        id="s-hidden",
        host="hidden.example",
        host_norm="hidden.example",
        canonical_name="Not Yours",
    )
    body = client.get("/explorer/sources/").content.decode()
    assert "The Lexington News" in body
    assert "Not Yours" not in body


# --- paywalls ----------------------------------------------------------------


def test_the_record_carries_its_paywall(client, admin, member_source):
    """Is this behind a paywall, what does it cost, and where does a
    person sign in. One question about the record, so one panel."""
    page = client.get(f"/manage/sources/{member_source.id}/").content.decode()
    assert "Paywall and sign-in" in page
    assert 'name="has_paywall"' in page
    assert 'name="subscription_cost"' in page
    assert 'name="login_url"' in page
    # No credentials on the page, because none are in the table.
    assert 'name="username"' not in page and 'name="password"' not in page

    client.post(
        f"/manage/sources/{member_source.id}/",
        {
            "state": "MO",
            "has_paywall": "1",
            "subscription_cost": "$12.99",
            "subscription_period": "monthly",
            "login_url": "https://lexingtonnews.example/login",
            "reason": "read the site",
        },
    )
    member_source.refresh_from_db()
    assert member_source.has_paywall is True
    assert str(member_source.subscription_cost) == "12.99"
    assert member_source.subscription_period == "monthly"
    assert member_source.login_url == "https://lexingtonnews.example/login"


def test_an_amount_with_no_period_is_refused(client, admin, member_source):
    """$12 a month and $12 a year are different subscriptions, and a
    number with neither is one nobody can read."""
    response = client.post(
        f"/manage/sources/{member_source.id}/",
        {"state": "MO", "subscription_cost": "12", "reason": "x"},
    )
    assert response.status_code == 400
    assert "monthly or annual" in response.content.decode()
    member_source.refresh_from_db()
    assert member_source.subscription_cost is None


def test_what_is_not_an_amount_is_refused(client, admin, member_source):
    response = client.post(
        f"/manage/sources/{member_source.id}/",
        {"state": "MO", "subscription_cost": "twelve dollars", "reason": "x"},
    )
    assert response.status_code == 400
    assert "is not an amount" in response.content.decode()


def test_the_crawlers_sign_in_is_shown_and_not_edited(client, admin, member_source):
    """It is configured when somebody automates a publisher's login, and
    the secret it names is the one thing on this page that must not be
    settable from a form field."""
    member_source.requires_login = True
    member_source.auth_type = "form"
    member_source.auth_secret_name = "publisher-auth-lexingtonnews-example"
    member_source.save(
        update_fields=["requires_login", "auth_type", "auth_secret_name"]
    )

    page = client.get(f"/manage/sources/{member_source.id}/").content.decode()
    assert "publisher-auth-lexingtonnews-example" in page
    assert "form login" in page
    # Shown, never as an input.
    assert 'name="auth_secret_name"' not in page

    # And a post naming it does not write it.
    client.post(
        f"/manage/sources/{member_source.id}/",
        {
            "state": "MO",
            "auth_secret_name": "publisher-auth-somebody-else",
            "reason": "x",
        },
    )
    member_source.refresh_from_db()
    assert member_source.auth_secret_name == "publisher-auth-lexingtonnews-example"


def test_the_record_opens_without_the_console_around_it(client, admin, member_source):
    """`?bare=1` is the same form with the console taken off, for a dialog
    to hold. A whole document rather than a fragment, so the same URL
    works opened in a tab -- which is what the queue's link falls back to
    with no JavaScript."""
    page = client.get(f"/manage/sources/{member_source.id}/?bare=1").content.decode()
    assert "<!doctype html>" in page.lower()
    assert "Paywall and sign-in" in page
    # The console is not around it.
    assert "← Datasets" not in page
    assert 'class="sidebar"' not in page

    # And the ordinary page still has it.
    full = client.get(f"/manage/sources/{member_source.id}/").content.decode()
    assert "← Datasets" in full


def test_a_bare_save_answers_where_it_was_asked(client, admin, member_source):
    """Editing a publisher from the review queue is one question inside
    another. Redirecting to the datasets list afterwards loses the queue
    somebody was working."""
    response = client.post(
        f"/manage/sources/{member_source.id}/?bare=1",
        {"state": "MO", "owner": "CherryRoad Media", "reason": "sold"},
    )
    assert response.status_code == 200, "a bare save redirected away"
    member_source.refresh_from_db()
    assert member_source.owner == "CherryRoad Media"
    # Audited like any other write, and revertible with it.
    # `audited_update` records the changes flat and the previous values
    # per record, which is what revert reads back.
    entry = AuditLogEntry.objects.filter(action="source:edit").latest("id")
    assert entry.after["owner"] == "CherryRoad Media"
    assert member_source.id in entry.before
    revert(admin, entry)
    member_source.refresh_from_db()
    assert member_source.owner != "CherryRoad Media"
