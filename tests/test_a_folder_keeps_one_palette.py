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
