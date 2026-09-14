"""A folder is a project, and a project's charts are read together.

Theme was a per-visual choice against one house default, so two charts of
the same counties came out in different palettes and nothing noticed: a
story map on `mizzou` shading gold beside a locator on `datadesk` shading
blue, in one folder.

The folder now carries the palette and a visual inherits it. Read at render
time rather than copied into each visual, so moving a visual into a folder
restyles it and moving it out gives it back -- and a folder that changes its
mind does not leave a stale copy in every chart it holds.

THE DOTS also changed, and for a reason that is measurable rather than a
matter of taste. `points` is indexed by geographic precision -- place,
block, county, state, tract -- and in production `place` carries 8,863 of
the corpus's located stories and `state` 2,090, so those two are what a
reader actually has to tell apart. The three colours they replaced spanned
0.205 of relative luminance in total, which is a mono print with three
identical grey dots on it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from visuals.models import Folder, Visual

CHART_JS = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"

pytestmark = pytest.mark.django_db


def _luminance(hex_colour):
    """WCAG relative luminance, which is what greyscale printing keeps."""
    h = hex_colour.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _point_sets():
    """Every theme's `points` array, read out of the renderer."""
    return [
        re.findall(r'"(#[0-9a-f]{6})"', match)
        for match in re.findall(r"points: \[([^\]]*)\]", CHART_JS.read_text())
    ]


@pytest.fixture
def folder(django_user_model):
    user = django_user_model.objects.create_user(
        "palette", email="palette@localnewsimpact.org"
    )
    return user, Folder.objects.create(name="A project", created_by=user, theme="lnic")


class TestAVisualInheritsItsFoldersPalette:
    def test_a_visual_with_no_theme_takes_the_folders(self, folder):
        user, project = folder
        visual = Visual.objects.create(
            slug="v1",
            title="V1",
            source_kind="inline",
            template="builder",
            config={"kind": "locator"},
            folder=project,
            created_by=user,
        )
        assert visual.render_config["theme"] == "lnic"

    def test_its_own_theme_still_wins(self, folder):
        """The folder sets what the project looks like. It does not overrule
        somebody who chose."""
        user, project = folder
        visual = Visual.objects.create(
            slug="v2",
            title="V2",
            source_kind="inline",
            template="builder",
            config={"kind": "locator", "theme": "mizzou"},
            folder=project,
            created_by=user,
        )
        assert visual.render_config["theme"] == "mizzou"

    def test_a_folder_with_no_theme_changes_nothing(self, django_user_model):
        """Blank is what every folder starts as: setting one is a decision,
        not a migration."""
        user = django_user_model.objects.create_user(
            "plain", email="plain@localnewsimpact.org"
        )
        project = Folder.objects.create(name="Unstyled", created_by=user)
        visual = Visual.objects.create(
            slug="v3",
            title="V3",
            source_kind="inline",
            template="builder",
            config={"kind": "locator"},
            folder=project,
            created_by=user,
        )
        assert "theme" not in visual.render_config

    def test_an_unfiled_visual_is_untouched(self, django_user_model):
        user = django_user_model.objects.create_user(
            "loose", email="loose@localnewsimpact.org"
        )
        visual = Visual.objects.create(
            slug="v4",
            title="V4",
            source_kind="inline",
            template="builder",
            config={"kind": "locator"},
            created_by=user,
        )
        assert "theme" not in visual.render_config

    def test_it_is_read_not_written(self, folder):
        """Moving a visual into a folder restyles it; moving it out gives it
        back. A copy written into the visual would go stale the moment the
        folder changed its mind."""
        user, project = folder
        visual = Visual.objects.create(
            slug="v5",
            title="V5",
            source_kind="inline",
            template="builder",
            config={"kind": "locator"},
            folder=project,
            created_by=user,
        )
        assert visual.render_config["theme"] == "lnic"
        assert "theme" not in visual.config, "the folder's choice was copied in"
        visual.folder = None
        assert "theme" not in visual.render_config


class TestTheDotsSurviveAMonoPrint:
    def test_every_theme_covers_all_five_precision_levels(self):
        """`PRECISION` indexes place, block, county, state and tract. With
        three colours the last two fell through to `series`, so a state dot
        was whatever the categorical palette had in slot 3 -- a colour
        chosen for bar charts."""
        sets = _point_sets()
        assert sets, "no points arrays found"
        for colours in sets:
            assert len(colours) == 5, colours

    def test_place_and_state_are_far_apart_in_greyscale(self):
        """The two that matter: 8,863 located stories sit at `place` and
        2,090 at `state`. Everything else is under 250 rows. If those two
        merge in print the map says nothing."""
        for colours in _point_sets():
            gap = abs(_luminance(colours[0]) - _luminance(colours[3]))
            assert gap > 0.25, f"place/state greyscale gap only {gap:.3f}: {colours}"

    def test_no_theme_puts_a_dot_in_its_own_ramp_hue(self):
        """A dot must not read as another step of the shading underneath it.
        `mizzou` shades gold, so gold is unavailable to it for dots -- which
        is why its `state` is a light blue where the others are gold."""
        source = CHART_JS.read_text()
        mizzou = source[source.index("mizzou: {") : source.index("rji: {")]
        ramp = re.search(r'seqHigh: "(#[0-9a-f]{6})"', mizzou).group(1)
        for colours in [
            re.findall(r'"(#[0-9a-f]{6})"', m)
            for m in re.findall(r"points: \[([^\]]*)\]", mizzou)
        ]:
            assert ramp not in colours
            # And not the light end either.
            assert "#eda100" not in colours, "gold dot on a gold ramp"


# --- setting it, and getting back ---------------------------------------------


@pytest.fixture
def designer(client, django_user_model):
    from accounts.models import DATADESK, Grant

    user = django_user_model.objects.create_user(
        "setter", email="setter@localnewsimpact.org"
    )
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    client.force_login(user)
    return user


class TestSettingAFoldersPalette:
    def test_it_sets_the_theme(self, client, designer):
        from django.urls import reverse

        project = Folder.objects.create(name="P", created_by=designer)
        client.post(
            reverse("visuals:folder_set_theme", args=[project.id]), {"theme": "lnic"}
        )
        project.refresh_from_db()
        assert project.theme == "lnic"

    def test_a_get_does_nothing(self, client, designer):
        """A link that restyles every chart in a project is one a crawler or
        a link prefetch can trip."""
        from django.urls import reverse

        project = Folder.objects.create(name="P2", created_by=designer, theme="lnic")
        assert (
            client.get(
                reverse("visuals:folder_set_theme", args=[project.id])
            ).status_code
            == 404
        )
        project.refresh_from_db()
        assert project.theme == "lnic"

    def test_an_unknown_theme_is_refused(self, client, designer):
        from django.urls import reverse

        project = Folder.objects.create(name="P3", created_by=designer, theme="lnic")
        client.post(
            reverse("visuals:folder_set_theme", args=[project.id]), {"theme": "neon"}
        )
        project.refresh_from_db()
        assert project.theme == "lnic"

    def test_blank_is_allowed_and_means_the_house_default(self, client, designer):
        from django.urls import reverse

        project = Folder.objects.create(name="P4", created_by=designer, theme="mizzou")
        client.post(
            reverse("visuals:folder_set_theme", args=[project.id]), {"theme": ""}
        )
        project.refresh_from_db()
        assert project.theme == ""

    def test_the_way_back_is_offered_on_the_folder(self, client, designer):
        """A setting that changes many things at once needs a way back that
        does not depend on remembering what it was."""
        from django.urls import reverse

        project = Folder.objects.create(name="P5", created_by=designer, theme="mizzou")
        response = client.post(
            reverse("visuals:folder_set_theme", args=[project.id]), {"theme": "lnic"}
        )
        assert f"undo={project.id}" in response["Location"]
        assert "was=mizzou" in response["Location"]


class TestAVisualCanOverrideItsFolder:
    @pytest.fixture
    def visual(self, folder):
        user, project = folder
        return Visual.objects.create(
            slug="ov",
            title="Override",
            source_kind="inline",
            template="builder",
            config={"kind": "locator"},
            folder=project,
            created_by=user,
        )

    def test_the_look_step_accepts_blank(self, visual):
        """BLANK HAS TO BE EXPRESSIBLE. The step wrote a theme on every
        visit, so a visual that had merely been looked at carried an
        explicit palette and could never take its folder's."""
        from visuals.panels import theme_panel

        assert theme_panel(visual, post={"theme": ""})["config"]["theme"] == ""

    def test_it_still_refuses_a_theme_that_does_not_exist(self, visual):
        from visuals.panels import theme_panel

        with pytest.raises(ValueError):
            theme_panel(visual, post={"theme": "neon"})

    def test_a_chosen_theme_is_kept(self, visual):
        from visuals.panels import theme_panel

        assert theme_panel(visual, post={"theme": "rji"})["config"]["theme"] == "rji"

    def test_the_inherit_option_is_offered_and_preselected(self, visual):
        """A visual with no theme of its own marks none of the palettes, so
        the "From <folder>" option takes the mark instead."""
        from visuals.panels import theme_panel

        panel = theme_panel(visual)
        assert panel["theme_chosen"] is False
        assert not any(t["on"] for t in panel["themes"])
        assert panel["folder_swatch"]


class TestSettingAFoldersPaletteRestylesTheVisuals:
    """WHAT THE CONTROL DID NOTHING FOR.

    Inheritance only reaches a visual with no theme of its own, and the
    Look step used to write one on every visit -- so on the existing
    corpus every visual carried an explicit palette, setting a folder's
    palette changed nothing on screen, and the success message said "0
    visuals restyled" over a folder of eight. The only way to get a
    project into one palette was still to open each visual and set it by
    hand, which is what the folder was built to replace.
    """

    @pytest.fixture
    def project(self, client, designer):
        folder = Folder.objects.create(name="Chose their own", created_by=designer)
        for i, theme in enumerate(["mizzou", "rji", ""]):
            Visual.objects.create(
                slug=f"chose{i}",
                title=f"Chose {i}",
                source_kind="inline",
                template="builder",
                config={"kind": "locator", **({"theme": theme} if theme else {})},
                folder=folder,
                created_by=designer,
            )
        return folder

    def _set(self, client, folder, theme, **extra):
        from django.urls import reverse

        response = client.post(
            reverse("visuals:folder_set_theme", args=[folder.id]),
            {"theme": theme, **extra},
        )
        # `folder.visuals.all()` hands each result the folder object it was
        # asked through, so a stale one in the test reads as a stale one in
        # `render_config`. The next request loads it fresh; so does this.
        folder.refresh_from_db()
        return response

    def test_the_folders_palette_reaches_every_visual(self, client, project):
        self._set(client, project, "lnic")
        assert [v.render_config["theme"] for v in project.visuals.all()] == [
            "lnic",
            "lnic",
            "lnic",
        ]

    def test_the_overrides_are_cleared_not_overwritten(self, client, project):
        """Cleared, so the visual goes on following the folder. Writing
        `lnic` into each config would leave three stale copies that the
        folder's next change could not reach."""
        self._set(client, project, "lnic")
        for visual in project.visuals.all():
            assert "theme" not in visual.config

    def test_the_count_is_every_visual_in_the_folder(self, client, project):
        """THE MESSAGE THAT GAVE IT AWAY: it counted only the visuals that
        were already inheriting, which on this folder is one of three and
        on the real one was none of eight."""
        from django.contrib.messages import get_messages

        response = self._set(client, project, "lnic")
        said = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("3 visuals restyled" in m for m in said), said

    def test_a_visual_can_still_refuse_it_afterwards(self, client, project):
        """Clearing is what setting the folder's palette does, not a
        standing ban: the Look step still wins until the folder is set
        again."""
        from visuals.panels import theme_panel

        self._set(client, project, "lnic")
        visual = project.visuals.first()
        visual.config = theme_panel(visual, post={"theme": "mizzou"})["config"]
        visual.save()
        assert visual.render_config["theme"] == "mizzou"


class TestTheWayBackPutsTheOverridesBack:
    @pytest.fixture
    def project(self, client, designer):
        folder = Folder.objects.create(name="Undo me", created_by=designer, theme="rji")
        Visual.objects.create(
            slug="had-one",
            title="Had one",
            source_kind="inline",
            template="builder",
            config={"kind": "locator", "theme": "mizzou"},
            folder=folder,
            created_by=designer,
        )
        Visual.objects.create(
            slug="had-none",
            title="Had none",
            source_kind="inline",
            template="builder",
            config={"kind": "locator"},
            folder=folder,
            created_by=designer,
        )
        return folder

    def _post(self, client, folder, **data):
        from django.urls import reverse

        return client.post(reverse("visuals:folder_set_theme", args=[folder.id]), data)

    def test_it_restores_the_visual_that_had_chosen(self, client, project):
        """Undo has more to put back than the folder's own value now.
        Restoring only the folder would leave the override flattened for
        good -- and the row offers undo precisely so that setting a
        palette is safe to try."""
        self._post(client, project, theme="lnic")
        self._post(client, project, theme="rji", restore="1")
        assert project.visuals.get(slug="had-one").config["theme"] == "mizzou"

    def test_it_leaves_the_one_that_had_not_inheriting(self, client, project):
        self._post(client, project, theme="lnic")
        self._post(client, project, theme="rji", restore="1")
        assert "theme" not in project.visuals.get(slug="had-none").config

    def test_it_restores_the_folder_too(self, client, project):
        self._post(client, project, theme="lnic")
        self._post(client, project, theme="rji", restore="1")
        project.refresh_from_db()
        assert project.theme == "rji"

    def test_a_restore_does_not_clear_on_its_way_through(self, client, project):
        """It comes back through the same view. Without the flag it reads
        as a second setting and flattens what it was sent to restore."""
        self._post(client, project, theme="lnic")
        self._post(client, project, theme="rji", restore="1")
        self._post(client, project, theme="rji", restore="1")
        assert project.visuals.get(slug="had-one").config["theme"] == "mizzou"

    def test_restoring_the_house_default_is_not_a_no_op(self, client, project):
        """`was` is blank when the folder had no palette, and the early
        return on "already that theme" would skip the restore entirely."""
        project.theme = ""
        project.save(update_fields=["theme"])
        self._post(client, project, theme="lnic")
        self._post(client, project, theme="", restore="1")
        project.refresh_from_db()
        assert project.theme == ""
        assert project.visuals.get(slug="had-one").config["theme"] == "mizzou"

    def test_an_ordinary_set_still_offers_the_way_back(self, client, project):
        response = self._post(client, project, theme="lnic")
        assert f"undo={project.id}" in response["Location"]


class TestTheHeadingLaysOutAsABar:
    """It did not. Every control in a folder heading is a form, a form is
    block-level, and a table cell stacks them: the palette control broke
    to its own line under the folder name and the undo form to a third.
    One heading, three rows, and a control described as hidden until asked
    for that was sitting in the open because nothing held it beside the
    name.

    Asserted on the stylesheet because the shape is the fix -- there is no
    layout to measure in a test runner, and what went wrong is visible in
    the rules.
    """

    CSS = Path(__file__).resolve().parent.parent / "static/css/datadesk.css"

    def _rule(self, selector):
        source = self.CSS.read_text()
        start = source.index(selector)
        return source[start : source.index("}", start)]

    def test_the_heading_lays_out_as_a_flex_row(self):
        """ON A WRAPPER, NOT ON THE CELL -- which is where this test
        first put it, and that was its own bug. `display: flex` on a `th`
        takes it out of table layout, so the browser sized the columns
        without it: the spanning heading stopped spanning and every
        visual title was pushed across the table, away from its handle.
        See test_the_folder_row_reads_left_to_right."""
        assert "display: flex" not in self._rule(".visuals-list .folder-head th{")
        rule = self._rule(".folder-head .folder-bar{")
        assert "display: flex" in rule
        assert "align-items: center" in rule

    def test_the_palette_control_is_laid_out(self):
        """THE MISSING RULE. `.folder-theme` had no styling at all, which
        is why it was a bare block button on its own line."""
        rule = self._rule(".folder-head form.folder-theme{")
        assert "inline-flex" in rule
        assert "margin-left: auto" in rule, "it belongs at the end of the bar"

    def test_the_undo_form_is_laid_out_too(self):
        assert "inline-flex" in self._rule(".folder-head form.folder-theme-undo{")

    def test_the_buttons_have_chrome(self):
        source = self.CSS.read_text()
        assert ".folder-head .theme-open" in source
        assert ".folder-head .theme-save" in source

    def test_nothing_forces_the_drawer_open(self):
        """The select and the apply button carry `hidden`, and a display
        declaration in these rules would beat it -- the drawer would be
        open on every folder on the page."""
        for selector in (
            ".folder-head form.folder-theme select{",
            ".folder-head .theme-open,",
        ):
            assert "display:" not in self._rule(selector)
