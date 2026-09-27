"""The outlet stories list: kept by hand, one form per story, and drawn by
any visual whose source is the list.

The MPA table was an upload -- a file made once by a scan, which nobody
could add a story to without making the file again.
"""

import datetime as dt

import pytest
from django.contrib.auth.models import User
from django.test import Client

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from visuals import outlet_stories
from visuals.models import STORIES, OutletStory, Visual
from visuals.outlet_stories import TEXT_LIMIT, StoryError, clean, load, rows, save

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

SEED = outlet_stories.__file__.replace(
    "outlet_stories.py", "data/outlet_stories_seed.csv"
)

STORY = {
    "published": "2026-09-01",
    "source": "Missouri Press Association",
    "headline": "September 2026 Missouri Press News",
    "url": "https://mopress.com/stories/september-2026-missouri-press-news,39849",
    "type": "Ownership or closure",
    "publications": "Weston Chronicle",
    "owners": "Megan Jantos\nJim and Beth McPherson",
    "text": "Megan Jantos is owner and publisher of The Weston Chronicle.",
}


def _user(name, role):
    user = User.objects.create_user(name, email=f"{name}@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role=role)
    return user


@pytest.fixture
def editor():
    return _user("editor", "editor")


class TestAStory:
    def test_the_relevant_text_has_a_limit(self):
        """'One leader, four Leaders' went in whole: 11,113 characters in
        one cell."""
        _, errors = clean({**STORY, "text": "x" * (TEXT_LIMIT + 1)})
        assert any("characters or fewer" in e for e in errors)

    def test_it_needs_a_date_headline_link_and_source(self):
        _, errors = clean({"type": "Historical"})
        assert len(errors) == 4

    def test_not_from_the_future(self):
        later = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        _, errors = clean({**STORY, "published": later})
        assert errors

    def test_names_are_a_list_however_typed(self):
        fields, _ = clean({**STORY, "owners": "A\nB;  C ;"})
        assert fields["owners"] == "A; B; C"


class TestTheList:
    def test_saving_is_audited(self, editor):
        story = save(editor, STORY)
        save(editor, {**STORY, "headline": "Changed"}, story)
        actions = list(
            AuditLogEntry.objects.order_by("id").values_list("action", flat=True)
        )
        assert actions == ["outlet_story:add", "outlet_story:edit"]

    def test_the_rows_a_visual_draws(self, editor):
        save(editor, STORY)
        (row,) = rows()
        assert row["Date"] == "2026-09-01"
        assert row["Headline"] == f"[{STORY['headline']}]({STORY['url']})"
        assert row["Owners"] == "Megan Jantos; Jim and Beth McPherson"

    def test_a_published_visual_follows_the_list(self, editor):
        """Adding a story is how the table changes; a published visual that
        went on showing the old list would hide the story just added."""
        from visuals.services import publish, refresh_snapshot

        save(editor, STORY)
        visual = Visual.objects.create(
            slug="mpa",
            title="MPA",
            template="builder",
            source_kind=STORIES,
            created_by=editor,
            config={"kind": "table"},
        )
        refresh_snapshot(visual, editor)
        publish(visual, editor)
        save(
            editor,
            {
                **STORY,
                "url": "https://mopress.com/stories/other,1",
                "headline": "Another",
            },
        )
        visual.refresh_from_db()
        assert len(visual.pinned_snapshot.data) == 2

    def test_the_seed_loads_clean(self, editor):
        rows_in = outlet_stories.read_csv(SEED)
        added, skipped, errors = load(editor, rows_in)
        assert errors == [] and added == len(rows_in) == 55
        assert max(len(s.text) for s in OutletStory.objects.all()) <= TEXT_LIMIT
        assert load(editor, rows_in)[:2] == (0, 55)


class TestThePages:
    def test_add_from_the_form(self, editor):
        client = Client()
        client.force_login(editor)
        response = client.post("/outlets/stories/new/", STORY)
        assert response.status_code == 302
        assert OutletStory.objects.get().headline == STORY["headline"]
        assert STORY["headline"] in client.get("/outlets/stories/").content.decode()

    def test_a_long_text_is_refused_on_the_form(self, editor):
        client = Client()
        client.force_login(editor)
        response = client.post(
            "/outlets/stories/new/", {**STORY, "text": "x" * (TEXT_LIMIT + 5)}
        )
        assert response.status_code == 400
        assert not OutletStory.objects.exists()

    def test_edit_and_remove(self, editor):
        story = save(editor, STORY)
        client = Client()
        client.force_login(editor)
        client.post(f"/outlets/stories/{story.pk}/", {**STORY, "type": "Historical"})
        assert OutletStory.objects.get().type == "Historical"
        client.post(f"/outlets/stories/{story.pk}/", {**STORY, "delete": "1"})
        assert not OutletStory.objects.exists()

    def test_a_viewer_reads_but_does_not_add(self):
        viewer = _user("viewer", "viewer")
        client = Client()
        client.force_login(viewer)
        assert client.get("/outlets/stories/").status_code == 200
        assert client.post("/outlets/stories/new/", STORY).status_code == 403

    def test_a_visual_can_take_the_list(self, editor):
        client = Client()
        client.force_login(editor)
        save(editor, STORY)
        client.post(
            "/visuals/builder/new/", {"title": "MPA updates", "source_kind": "stories"}
        )
        visual = Visual.objects.get(slug="mpa-updates")
        assert visual.source_kind == STORIES
        assert len(visual.snapshots.get().data) == 1

    def test_invalid_story_raises(self, editor):
        with pytest.raises(StoryError):
            save(editor, {**STORY, "url": "not a link"})
