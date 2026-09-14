"""Advanced settings showed a locator map as a table, with an empty title.

`{{ config_json }}` inside a <script> is AUTOESCAPED: every `"` became
`&quot;`, so `JSON.parse` threw on the first line of the page's script and
took everything after it with it -- the form was never hydrated,
`updateVisibility` never ran, the preview never drew.

The config in the database was correct the whole time. The page could not
read it, and saving from that page wrote the un-hydrated form back --
`kind: table`, no title -- over a working locator. So this did not merely
fail to show the settings: it destroyed them.

`columns` on the same page already used `json_script`, which is why the
column pickers were the one part of it that worked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def designer_and_visual(django_user_model):
    from accounts.models import DATADESK, Grant
    from visuals.models import Visual

    user = django_user_model.objects.create_user(
        "designer2", email="designer2@localnewsimpact.org"
    )
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    visual = Visual.objects.create(
        slug="a-locator",
        # A quote in the title is the cheapest way to prove the escaping:
        # it is what turns into `&quot;` and breaks the parse.
        title='A "quoted" title',
        source_kind="inline",
        template="builder",
        config={"kind": "locator", "title": 'A "quoted" title', "area": "fips"},
        created_by=user,
    )
    return user, visual


def _payload(client, visual, element):
    page = client.get(f"/visuals/builder/{visual.slug}/").content.decode()
    block = re.search(rf'<script id="{element}"[^>]*>(.*?)</script>', page, re.S)
    assert block, f"{element} is gone from the page"
    return json.loads(block.group(1))


class TestThePageCanReadItsOwnPayloads:
    def test_the_config_payload_parses_as_json(self, client, designer_and_visual):
        """THE BUG. Autoescaped this reads `{&quot;kind&quot;: ...}`, and
        `JSON.parse` throws on it -- taking the hydration, the per-kind
        filtering and the preview down with it."""
        user, visual = designer_and_visual
        client.force_login(user)
        parsed = _payload(client, visual, "dd-config")
        assert parsed["kind"] == "locator"
        assert parsed["title"] == 'A "quoted" title'

    def test_the_rows_payload_parses_too(self, client, designer_and_visual):
        """Same escaping, same failure: one apostrophe in the data killed
        the page just as surely as one in the title."""
        user, visual = designer_and_visual
        client.force_login(user)
        _payload(client, visual, "dd-rows")

    def test_the_kind_reaches_the_page(self, client, designer_and_visual):
        """What the screenshot showed: a locator map whose settings page
        said `table`, because the select was left on its first option."""
        user, visual = designer_and_visual
        client.force_login(user)
        assert _payload(client, visual, "dd-config")["kind"] == "locator"


class TestNoPayloadIsInterpolatedRaw:
    """The shape to keep out of the template. `{{ x_json }}` inside a
    <script> is the pattern; `json_script` is the filter that escapes for
    that context."""

    def _page(self):
        return Path("templates/visuals/builder_edit.html").read_text()

    def test_the_raw_interpolations_are_gone(self):
        page = self._page()
        assert "{{ config_json }}" not in page
        assert "{{ preview_json }}" not in page

    def test_json_script_is_used_instead(self):
        page = self._page()
        assert 'json_script:"dd-config"' in page
        assert 'json_script:"dd-rows"' in page

    def test_the_view_hands_over_objects_not_strings(self):
        """`json_script` encodes; handing it an already-encoded string
        would double-encode and break the parse a second way."""
        source = Path("visuals/views.py").read_text()
        assert '"config_json": visual.config or {}' in source
        assert '"preview_json": rows[:5000]' in source
