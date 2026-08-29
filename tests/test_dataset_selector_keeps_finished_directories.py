"""A directory that has been worked to zero must stay on the page.

The chips were built from pending proposals alone, so a directory left
the queue the moment its last question was answered. Missouri reached
zero on 28 August and vanished -- along with the only way to filter to
the 139 accepted and 140 fixed proposals decided there. And because the
template drew the row only when more than one directory had work, the
control did not shrink to one chip, it disappeared entirely.

"Nothing wrong here" and "nobody has looked" are different answers. The
page has to be able to say the first one.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from accounts.models import DATADESK, Grant
from explorer.models import Dataset
from review.proposals import ChangeProposal


@pytest.fixture
def directories(crawler_schema):
    Dataset.objects.create(id="d-mo", slug="Mizzou-Missouri-State", label="Missouri")
    Dataset.objects.create(id="d-vt", slug="VT-Community-News", label="Vermont")


@pytest.fixture
def reviewer():
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


@pytest.fixture
def queue(directories, reviewer):
    """Missouri worked to zero, Vermont still holding work."""
    ChangeProposal.objects.create(
        target="sources",
        record_id="s-mo",
        record_label="A Missouri paper",
        dataset="Mizzou-Missouri-State",
        field="city",
        current_value="",
        proposed_value="Columbia",
        state=ChangeProposal.ACCEPTED,
        flag="city_missing",
    )
    ChangeProposal.objects.create(
        target="sources",
        record_id="s-vt",
        record_label="A Vermont paper",
        dataset="VT-Community-News",
        field="owner",
        current_value="",
        proposed_value="Someone",
        state=ChangeProposal.PENDING,
        flag="owner_missing",
    )
    client = Client()
    client.force_login(reviewer)
    return client


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_finished_directory_still_appears(queue):
    body = queue.get(reverse("review:proposals")).content.decode()
    assert "Mizzou-Missouri-State" in body or "Missouri" in body
    assert "no work" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_selector_is_drawn_even_with_one_directory_holding_work(queue):
    """The row vanished entirely; that is what was reported."""
    body = queue.get(reverse("review:proposals")).content.decode()
    assert 'aria-label="Which directory"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_finished_directory_can_still_be_selected(queue):
    """The decided proposals are reachable again."""
    response = queue.get(
        reverse("review:proposals"),
        {"dataset": "Mizzou-Missouri-State", "state": "accepted"},
    )
    assert response.status_code == 200
    assert "A Missouri paper" in response.content.decode()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_counts_follow_the_state_being_viewed(queue):
    """A chip has to promise what selecting it delivers.

    Counted against pending regardless of the state filter, the Missouri
    chip read zero while its accepted queue held a row.
    """
    body = queue.get(
        reverse("review:proposals"), {"state": "accepted"}
    ).content.decode()
    marker = body[body.index('aria-label="Which directory"') :]
    marker = marker[: marker.index("</nav>")]
    assert "no work" not in marker or "Vermont" in marker
