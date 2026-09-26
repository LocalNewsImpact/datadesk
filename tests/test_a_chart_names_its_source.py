"""The line under a chart that says whose it is.

It offered the consortium or the dataset's owner, and the second only where
an owner was recorded -- so on most charts there was nothing to change, and
nowhere to write "LNIC analysis of Missouri newsroom stories, March 2026".
"""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from visuals.models import Visual

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def visual(client, crawler_schema):
    user = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return Visual.objects.create(
        slug="credited",
        title="Credited",
        template="builder",
        source_kind="corpus",
        created_by=user,
        config={"kind": "table"},
    )


def _look(client, visual, data=None):
    url = f"/visuals/builder/{visual.slug}/step/theme/"
    return client.post(url, data) if data is not None else client.get(url)


def test_the_look_step_offers_a_line_and_a_link(client, visual):
    body = _look(client, visual).content.decode()
    assert 'name="source_text"' in body and 'name="source_url"' in body


def test_a_written_line_is_saved_with_its_link(client, visual):
    _look(
        client,
        visual,
        {"source_text": "LNIC analysis", "source_url": "https://localnewsimpact.org/x"},
    )
    visual.refresh_from_db()
    assert visual.config["source_text"] == "LNIC analysis"
    assert visual.config["source_url"] == "https://localnewsimpact.org/x"


def test_a_link_that_is_not_a_web_address_is_refused(client, visual):
    _look(client, visual, {"source_text": "LNIC", "source_url": "javascript:alert(1)"})
    visual.refresh_from_db()
    assert "source_url" not in (visual.config or {})


def test_the_written_line_is_what_the_chart_says(visual):
    from visuals.views import _credit_line

    visual.config = {"source_text": "LNIC analysis", "source_url": "https://a.org"}
    assert _credit_line(visual) == ("LNIC analysis", "https://a.org")


def test_a_line_without_a_link_is_plain_text(visual):
    from visuals.views import _credit_line

    visual.config = {"source_text": "LNIC analysis"}
    assert _credit_line(visual) == ("LNIC analysis", None)


def test_blank_falls_back_to_the_consortium(visual):
    from visuals.views import _credit_line

    visual.config = {"source_text": ""}
    assert _credit_line(visual) == (None, None)


def test_an_old_free_text_source_does_not_come_back(visual):
    """ "LNIC research corpus" was a database name, read as a publisher."""
    from visuals.views import _credit_line

    visual.config = {"source": "LNIC research corpus"}
    assert _credit_line(visual) == (None, None)
