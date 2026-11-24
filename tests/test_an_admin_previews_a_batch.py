"""An admin sees a batch on the coding screen without coding it.

The preview walks a batch's stories in the screen its coders are given
-- categories, or the map for a geography round -- and Save checks the
answer the way a coder's is checked. Nothing is written: no answer, no
assignment, and the admin never appears among the round's coders.

Also on the round page: coders are picked from the people already in
datadesk, and the answer count is a number.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from accounts.privileges import ADMIN, CLASSIFIER
from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationSample,
    Round,
    open_round,
    round_coders,
)
from review.geography_icr import GEOGRAPHY, GeographyDecision

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def admin(client, db):
    user = User.objects.create_user("admin", email="admin@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, role=ADMIN, scope="")
    client.force_login(user)
    return user


def _stories(count):
    from explorer.models import Article

    for n in range(count):
        Article.objects.using("crawler").create(
            id=f"s-{n}",
            url=f"https://example.com/{n}",
            title=f"story {n}",
            text="word " * 150,
            status="labeled",
            wire_check_status="complete",
            primary_label="Sports",
            primary_label_confidence=0.9,
        )


def _batch(round, count=3):
    cohort = ClassificationCohort.objects.create(
        number=round.pk * 10,
        slug=f"cohort-{round.pk}",
        round=round,
        codebook_version=round.codebook_version,
    )
    for n in range(count):
        ClassificationSample.objects.create(
            cohort=cohort, article_id=f"s-{n}", stratum="random"
        )
    return cohort


def _nothing_written(user):
    assert not ClassificationDecision.objects.filter(decided_by=user).exists()
    assert not GeographyDecision.objects.filter(decided_by=user).exists()
    assert not ClassificationAssignment.objects.filter(assigned_to=user).exists()


def test_the_preview_shows_the_batchs_stories_in_order(client, admin, crawler_schema):
    _stories(3)
    cohort = _batch(open_round("Practice", Round.PRACTICE))
    url = reverse("review:cin_batch_preview", args=[cohort.pk])
    first = client.get(url).content.decode()
    assert "story 0" in first and "story 1 of 3" in first
    second = client.get(url + "?n=1").content.decode()
    assert "story 1" in second and "story 2 of 3" in second


def test_saving_in_the_preview_writes_nothing(client, admin, crawler_schema):
    _stories(3)
    round = open_round("Practice", Round.PRACTICE)
    cohort = _batch(round)
    url = reverse("review:cin_batch_preview", args=[cohort.pk])
    response = client.post(url, {"n": 0, "primary": "Sports"}, follow=True)
    body = response.content.decode()
    assert "would record" in body and "Not saved." in body
    # And on to the next story.
    assert "story 2 of 3" in body
    _nothing_written(admin)
    assert admin not in round_coders(round)


def test_the_preview_refuses_what_a_coder_would_be_refused(
    client, admin, crawler_schema
):
    _stories(1)
    cohort = _batch(open_round("Practice", Round.PRACTICE), count=1)
    url = reverse("review:cin_batch_preview", args=[cohort.pk])
    body = client.post(url, {"n": 0}, follow=True).content.decode()
    assert "Choose a category, or say why you cannot." in body
    body = client.post(url, {"n": 0, "primary": "Nonsense"}, follow=True)
    assert "not one of the choices offered" in body.content.decode()
    _nothing_written(admin)


def test_a_geography_batch_previews_on_the_map(client, admin, crawler_schema):
    _stories(1)
    cohort = _batch(open_round("Where", Round.PRACTICE, task=GEOGRAPHY), count=1)
    url = reverse("review:cin_batch_preview", args=[cohort.pk])
    body = client.get(url).content.decode()
    assert 'id="geo-form"' in body and "Preview" in body
    response = client.post(url, {"n": 0, "answer_class": "N"}, follow=True)
    assert "would record" in response.content.decode()
    _nothing_written(admin)


def test_a_coder_cannot_preview(client, crawler_schema):
    _stories(1)
    cohort = _batch(open_round("Practice", Round.PRACTICE), count=1)
    coder = User.objects.create_user("coder", email="coder@missouri.edu")
    Grant.objects.create(user=coder, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    client.force_login(coder)
    url = reverse("review:cin_batch_preview", args=[cohort.pk])
    assert client.get(url).status_code in (302, 403)


def test_the_empty_queue_offers_an_admin_the_open_batches(
    client, admin, crawler_schema
):
    _stories(1)
    cin = _batch(open_round("Practice", Round.PRACTICE), count=1)
    where = _batch(open_round("Where", Round.PRACTICE, task=GEOGRAPHY), count=1)
    body = client.get(reverse("review:classification")).content.decode()
    assert reverse("review:cin_batch_preview", args=[cin.pk]) in body
    assert reverse("review:cin_batch_preview", args=[where.pk]) not in body
    body = client.get(reverse("review:geography_labeling")).content.decode()
    assert reverse("review:cin_batch_preview", args=[where.pk]) in body


def test_the_round_page_links_each_batch_to_its_preview(client, admin, crawler_schema):
    _stories(2)
    round = open_round("Practice", Round.PRACTICE)
    cohort = _batch(round, count=2)
    body = client.get(reverse("review:cin_round", args=[round.pk])).content.decode()
    assert reverse("review:cin_batch_preview", args=[cohort.pk]) in body
    # The answer count, not the lead's decisions under the same name.
    assert "QuerySet" not in body


def test_a_coder_is_picked_from_the_people_in_datadesk(client, admin, crawler_schema):
    _stories(2)
    round = open_round("Practice", Round.PRACTICE)
    _batch(round, count=2)
    person = User.objects.create_user(
        "jsmith", email="jsmith@missouri.edu", first_name="Jo", last_name="Smith"
    )
    url = reverse("review:cin_round", args=[round.pk])
    assert "Jo Smith — jsmith@missouri.edu" in client.get(url).content.decode()
    body = client.post(url, {"action": "invite", "user": person.pk}).content.decode()
    assert "Added to the round; 2 assignments made." in body
    assert person in round_coders(round)
    # On the round now, so no longer offered.
    assert f'<option value="{person.pk}">' not in body


def test_a_practice_round_page_is_numbered_steps(client, admin, crawler_schema):
    _stories(2)
    round = open_round("Practice", Round.PRACTICE)
    url = reverse("review:cin_round", args=[round.pk])
    body = client.get(url).content.decode()
    assert '<ol class="round-steps">' in body and "Add the coders." in body
    assert "Not done." in body
    _batch(round, count=2)
    body = client.get(url).content.decode()
    assert "Open the batch." in body and "Done." in body


def test_a_round_is_started_with_people_ticked_from_datadesk(
    client, admin, crawler_schema
):
    _stories(30)
    person = User.objects.create_user(
        "jsmith", email="jsmith@missouri.edu", first_name="Jo", last_name="Smith"
    )
    form = client.get(reverse("review:cin_rounds")).content.decode()
    assert f'name="coder_users" value="{person.pk}"' in form
    client.post(
        reverse("review:cin_rounds"),
        {"name": "Test", "kind": Round.PRACTICE, "coder_users": [person.pk]},
    )
    round = Round.objects.get(name="Test")
    assert person in round_coders(round)
    assert ClassificationAssignment.objects.filter(assigned_to=person).exists()


def test_the_round_page_says_when_nobody_is_left_to_add(client, admin, crawler_schema):
    round = open_round("Practice", Round.PRACTICE)
    url = reverse("review:cin_round", args=[round.pk])
    # The admin is the only person with an address; once on the round,
    # nobody is left, and the page says so instead of showing nothing.
    client.post(url, {"action": "invite", "user": admin.pk})
    _batch(round, count=0)
    body = client.get(url).content.decode()
    assert "Everyone who has signed in to datadesk is already on this round." in body
