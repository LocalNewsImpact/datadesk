"""A folder heading is a bar, and the row below it has to line up with it.

THE CELL WAS THE FLEX BOX. `display: flex` on a `th` takes it out of
table layout, so the browser sized the columns without it: the spanning
heading stopped spanning and the Visual column was pushed most of the way
across the table. Every title ended up far to the right of its own grab
handle, with the folder's controls sitting where the titles should have
been. The flex box belongs on a wrapper inside the cell.

TWO SMALLER THINGS on the same row. "Theme:" is a caption and was inside
the button, so the whole phrase inherited the heading's weight and the
row carried three bold phrases with nothing saying which could be
pressed. And a folder had no way to show what it contains: a list of
titles answers "what is in here" and cannot answer "do these read as one
set", which is the question a folder palette exists to serve.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from visuals.models import Folder, Visual

pytestmark = pytest.mark.django_db

CSS = Path(__file__).resolve().parent.parent / "static/css/datadesk.css"
INDEX = Path(__file__).resolve().parent.parent / "templates/visuals/index.html"


def _rule(selector):
    source = CSS.read_text()
    start = source.index(selector)
    return source[start : source.index("}", start)]


@pytest.fixture
def designer(client, django_user_model):
    from accounts.models import DATADESK, Grant

    user = django_user_model.objects.create_user(
        "sheet", email="sheet@localnewsimpact.org"
    )
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    client.force_login(user)
    return user


@pytest.fixture
def folder(designer):
    project = Folder.objects.create(
        name="Mapping paper", created_by=designer, theme="datadesk"
    )
    for i in range(2):
        Visual.objects.create(
            slug=f"sheet-{i}",
            title=f"Where Audrain County reports {i}",
            source_kind="inline",
            template="builder",
            config={"kind": "locator"},
            status="published",
            folder=project,
            created_by=designer,
        )
    return project


class TestTheHeadingDoesNotBreakTheColumns:
    def test_the_cell_is_not_the_flex_box(self):
        """THE BUG. A flex `th` is not a table cell for sizing purposes,
        and the columns are then computed without it."""
        assert "display: flex" not in _rule(".visuals-list .folder-head th{")

    def test_the_wrapper_is(self):
        assert "display: flex" in _rule(".folder-head .folder-bar{")

    def test_the_heading_still_spans_every_column(self):
        """It has five columns to cover. A heading that spans fewer puts
        the folder's controls inside the Visual column, which is what
        pushed the titles across."""
        assert 'colspan="5"' in INDEX.read_text()

    def test_every_heading_is_wrapped(self, client, folder, designer):
        """Including Unfiled. One that is not wrapped lays out differently
        from the rest, which reads as a rendering fault."""
        body = client.get(reverse("visuals:index")).content.decode()
        heads = re.findall(r'<th colspan="5"[^>]*>(.*?)</th>', body, re.S)
        assert heads
        for head in heads:
            assert 'class="folder-bar"' in head


class TestTheThemeCaptionIsNotTheControl:
    def test_the_caption_is_outside_the_button(self, client, folder):
        body = client.get(reverse("visuals:index")).content.decode()
        button = re.search(
            r'<button type="button" class="theme-open".*?</button>', body, re.S
        )
        assert button, "the palette control is gone"
        assert "Theme:" not in button.group(0)

    def test_the_palette_name_is_the_button(self, client, folder):
        body = client.get(reverse("visuals:index")).content.decode()
        button = re.search(
            r'<button type="button" class="theme-open".*?</button>', body, re.S
        )
        assert "datadesk" in button.group(0)

    def test_the_caption_is_still_on_the_page(self, client, folder):
        body = client.get(reverse("visuals:index")).content.decode()
        assert 'class="theme-label"' in body
        assert "Theme:" in body

    def test_the_caption_is_not_bold(self):
        """It sat inside a `th` and inherited the heading's weight."""
        assert "font-weight: 400" in _rule(".folder-head .theme-label{")


class TestTheContactSheet:
    def test_the_link_is_on_the_folder_row(self, client, folder):
        body = client.get(reverse("visuals:index")).content.decode()
        assert reverse("visuals:folder_preview", args=[folder.id]) in body

    def test_it_is_a_link_and_not_a_button(self, client, folder):
        """It goes to a page, which is what a link means."""
        body = client.get(reverse("visuals:index")).content.decode()
        assert re.search(r'<a class="folder-preview"', body)

    def test_it_is_outside_the_theme_form(self, client, folder):
        """Inside it, a click would submit the palette."""
        body = client.get(reverse("visuals:index")).content.decode()
        form = re.search(
            r'<form method="post" class="folder-theme".*?</form>', body, re.S
        )
        assert form
        assert "folder-preview" not in form.group(0)

    def test_it_is_visible_with_the_folder_shut(self, client, folder):
        """Looking at what a project contains is a reason to open one, so
        it must not be behind opening it. The rows are hidden by CSS on a
        shut folder; the heading is not."""
        body = client.get(reverse("visuals:index")).content.decode()
        head = re.search(r'<tr class="folder-head">.*?</tr>', body, re.S).group(0)
        assert "folder-preview" in head
        assert "display: none" in _rule("tbody.folder.shut tr:not(.folder-head){")

    def test_the_page_opens(self, client, folder):
        response = client.get(reverse("visuals:folder_preview", args=[folder.id]))
        assert response.status_code == 200

    def test_it_shows_every_visual_in_the_folder(self, client, folder):
        body = client.get(
            reverse("visuals:folder_preview", args=[folder.id])
        ).content.decode()
        for visual in folder.visuals.all():
            assert visual.title in body

    def test_each_one_carries_its_name_and_a_link(self, client, folder):
        body = client.get(
            reverse("visuals:folder_preview", args=[folder.id])
        ).content.decode()
        for visual in folder.visuals.all():
            assert reverse("visuals:page", args=[visual.slug]) in body

    def test_each_preview_is_the_real_embed(self, client, folder):
        """An iframe of the embed, not a thumbnail. The embed already
        serves the pinned snapshot and is what a reader sees; a second
        rendering path would drift and this page would quietly lie about
        the work."""
        body = client.get(
            reverse("visuals:folder_preview", args=[folder.id])
        ).content.decode()
        for visual in folder.visuals.all():
            assert reverse("visuals:embed", args=[visual.slug]) in body
        assert "<iframe" in body

    def test_the_previews_are_reduced_not_narrowed(self):
        """A chart drawn at 300px is not the chart -- its labels collide
        and its legend wraps, so the sheet would show a fault that does
        not exist. Rendered wide and scaled down instead."""
        rule = _rule(".sheet-frame iframe{")
        assert "transform: scale(" in rule
        assert "width: 200%" in rule

    def test_a_folder_with_nothing_in_it_says_so(self, client, designer):
        empty = Folder.objects.create(name="Empty", created_by=designer)
        body = client.get(
            reverse("visuals:folder_preview", args=[empty.id])
        ).content.decode()
        assert "Nothing filed here yet" in body

    def test_an_unknown_folder_is_a_404(self, client, designer):
        assert (
            client.get(reverse("visuals:folder_preview", args=[99999])).status_code
            == 404
        )
