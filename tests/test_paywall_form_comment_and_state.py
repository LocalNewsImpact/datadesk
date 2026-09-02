"""The paywall row's form: a comment, a visible unsaved state, side labels.

The comment posts as `reason`, which the view already read and `record`
already stored on the audit entry. It had nowhere to be typed and
nowhere to be read, so the field that recorded why a publisher was
marked the way it was went permanently unused.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from explorer.models import Dataset, Source


@pytest.fixture
def editor():
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


@pytest.fixture
def page(crawler_schema, editor):
    Dataset.objects.create(id="d-mo", slug="Mizzou-Missouri-State", label="Missouri")
    Source.objects.create(
        id="s-1",
        host="locked.example",
        host_norm="locked.example",
        canonical_name="The Locked Gazette",
        has_paywall=True,
    )
    client = Client()
    client.force_login(editor)
    return client


def _body(client):
    return client.get(reverse("review:paywalls")).content.decode()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_form_offers_a_comment_field(page):
    body = _body(page)
    assert 'name="reason"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_comment_sits_above_the_save_button(page):
    """Asked for above Save, because that is the last thing read before
    committing the row."""
    body = _body(page)
    assert body.index('name="reason"') < body.index('<button type="submit">Save')


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_saved_comment_reaches_the_audit_entry(page):
    page.post(
        reverse("review:paywalls"),
        {"source_id": "s-1", "has_paywall": "1", "reason": "checked, hard paywall"},
    )
    entry = AuditLogEntry.objects.filter(target_table="sources").first()
    assert entry is not None
    assert entry.reason == "checked, hard paywall"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_previous_comment_is_shown_back(page):
    """Otherwise the box is written into a void."""
    page.post(
        reverse("review:paywalls"),
        {"source_id": "s-1", "has_paywall": "1", "reason": "metered, 3 free"},
    )
    assert "metered, 3 free" in _body(page)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_default_reason_is_not_shown_back_as_a_comment(page):
    """Saving without a comment records "reviewed the paywall" for the
    audit. That is not something a person wrote, so it is not quoted."""
    page.post(reverse("review:paywalls"), {"source_id": "s-1", "has_paywall": "1"})
    assert "last: “reviewed the paywall”" not in _body(page)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_unsaved_row_can_be_marked(page):
    body = _body(page)
    assert "unsaved-note" in body
    assert 'classList.toggle("dirty"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_labels_sit_beside_their_fields(page):
    """Stacked, the column ran deeper than the row needed and pushed the
    table past a laptop's width."""
    body = _body(page)
    assert 'class="side"' in body
    assert 'class="stacked">Subscription' not in body


def test_the_side_label_style_exists():
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / "static/css/datadesk.css").read_text()
    assert "label.side" in css
    assert ".inline-form.dirty" in css
