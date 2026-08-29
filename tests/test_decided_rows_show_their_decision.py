"""A question already answered must not come back looking unanswered.

"Including decided" rendered every proposal with the same live controls,
so 316 settled Missouri questions returned to the page showing Accept /
Keep / Fix and an empty `d-<pk>` field, exactly like a pending one. It
read as though the decisions had been reset.

They had not been, in either direction. The rows were untouched in the
database, and `_submit_proposals` skips anything whose state is not
pending, so pressing those buttons would have done nothing. The record
was never at risk; the page simply could not say what it knew.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from accounts.models import DATADESK, Grant
from explorer.models import Dataset, Source
from review.proposals import ChangeProposal


@pytest.fixture
def reviewer():
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


@pytest.fixture
def page(crawler_schema, reviewer):
    Dataset.objects.create(id="d-mo", slug="Mizzou-Missouri-State", label="Missouri")
    Source.objects.create(
        id="s-mo",
        host="tribune.example",
        host_norm="tribune.example",
        canonical_name="The Tribune",
    )
    ChangeProposal.objects.create(
        target="sources",
        record_id="s-mo",
        record_label="The Tribune",
        dataset="Mizzou-Missouri-State",
        field="city",
        current_value="",
        proposed_value="Columbia",
        final_value="Columbia",
        state=ChangeProposal.ACCEPTED,
        flag="city_missing",
        decided_by=reviewer,
    )
    ChangeProposal.objects.create(
        target="sources",
        record_id="s-mo",
        record_label="The Tribune",
        dataset="Mizzou-Missouri-State",
        field="county",
        current_value="Boone",
        proposed_value="Greene",
        state=ChangeProposal.REJECTED,
        flag="county_disputed",
        decided_by=reviewer,
    )
    ChangeProposal.objects.create(
        target="sources",
        record_id="s-mo",
        record_label="The Tribune",
        dataset="Mizzou-Missouri-State",
        field="owner",
        current_value="",
        proposed_value="Someone",
        state=ChangeProposal.PENDING,
        flag="owner_missing",
    )
    client = Client()
    client.force_login(reviewer)
    return client


def _including_decided(client):
    return client.get(reverse("review:proposals"), {"state": "all"}).content.decode()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_decided_row_shows_what_was_decided(page):
    body = _including_decided(page)
    assert "Accepted" in body
    assert "Kept" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_decided_row_offers_no_decision_controls(page):
    """The buttons were inert. Showing them said otherwise."""
    body = _including_decided(page)
    decided = ChangeProposal.objects.exclude(state=ChangeProposal.PENDING)
    for proposal in decided:
        assert f'name="d-{proposal.pk}"' not in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_pending_row_still_has_its_controls(page):
    body = _including_decided(page)
    pending = ChangeProposal.objects.get(state=ChangeProposal.PENDING)
    assert f'name="d-{pending.pk}"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_submitting_cannot_revert_a_decision(page):
    """The guarantee underneath: state is only ever left alone."""
    accepted = ChangeProposal.objects.get(state=ChangeProposal.ACCEPTED)
    page.post(reverse("review:proposals"), {f"d-{accepted.pk}": "reject"})
    accepted.refresh_from_db()
    assert accepted.state == ChangeProposal.ACCEPTED


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_label_matches_the_button_that_was_pressed():
    """The reviewer pressed Keep, so the row says Kept -- not "rejected"."""
    assert ChangeProposal(state=ChangeProposal.REJECTED).state_label == "Kept"
    assert ChangeProposal(state=ChangeProposal.ACCEPTED).state_label == "Accepted"
    assert ChangeProposal(state=ChangeProposal.FIXED).state_label == "Fixed"
