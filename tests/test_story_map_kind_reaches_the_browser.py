"""A story map must not reach the browser calling itself a table.

Two keys say a visual is a story map. `spec["shape"]` is written by the
pivot form on the settings page; `config["kind"]` by step one of the
walk. `services.fetch_source_data` reads both, so a visual carrying only
the spec key still produces story map DATA: an object of areas and
points, not a row array.

The renderer read only `config`. So boone-county-coverage was published
with `kind: "table"` over a two-layer payload, `datadesk-chart` hit

    if (kind !== "storymap" && (!rows || !rows.length))

found no `.length` on an object, and printed "No data." over a feed that
was returning 76 areas and 56 points in 160ms.
"""

import pytest

from visuals.models import Visual
from visuals.services import STORY_MAP_KIND


def _visual(**kwargs):
    return Visual(template="builder", **kwargs)


def test_the_spec_shape_alone_resolves_to_a_story_map():
    """The exact shape of the published bug."""
    visual = _visual(config={"kind": "table"}, spec={"shape": "story_map"})
    assert visual.render_config["kind"] == STORY_MAP_KIND


def test_the_config_kind_alone_is_kept():
    visual = _visual(config={"kind": STORY_MAP_KIND}, spec={})
    assert visual.render_config["kind"] == STORY_MAP_KIND


def test_an_ordinary_visual_is_untouched():
    visual = _visual(config={"kind": "bar", "title": "Something"}, spec={})
    assert visual.render_config == {"kind": "bar", "title": "Something"}


def test_the_rest_of_the_config_survives_resolution():
    visual = _visual(
        config={"kind": "table", "title": "Where Boone County reports"},
        spec={"shape": "story_map"},
    )
    resolved = visual.render_config
    assert resolved["title"] == "Where Boone County reports"
    assert resolved["kind"] == STORY_MAP_KIND


def test_resolution_does_not_mutate_the_stored_config():
    """Read, not written: nothing here should need a migration."""
    stored = {"kind": "table"}
    visual = _visual(config=stored, spec={"shape": "story_map"})
    visual.render_config
    assert stored == {"kind": "table"}


@pytest.mark.parametrize("empty", [None, {}])
def test_absent_config_or_spec_is_not_an_error(empty):
    assert _visual(config=empty, spec=empty).render_config == {}


def test_the_renderer_template_uses_the_resolved_config():
    """The template is the half that was wrong; assert it stays fixed."""
    from pathlib import Path

    from django.conf import settings

    body = (
        Path(settings.BASE_DIR) / "templates/visuals/renderers/builder.html"
    ).read_text()
    assert 'visual.render_config|json_script:"dd-config"' in body
    assert 'visual.config|json_script:"dd-config"' not in body


def test_the_resolved_kind_also_decides_which_libraries_load():
    """The half of the bug that would have survived a config-only fix.

    `libs` is chosen from the chart kind, and a table needs no libraries
    at all. So the published page carried datadesk-chart.js and nothing
    else -- no d3, no topojson -- and could not have drawn the map even
    with the right config.
    """
    from visuals.builder import libs_for

    visual = _visual(config={"kind": "table"}, spec={"shape": "story_map"})
    assert libs_for(visual.render_config.get("kind")) == ("d3", "topojson")


def test_every_renderer_view_uses_the_resolved_kind_for_libraries():
    from pathlib import Path

    from django.conf import settings

    body = (Path(settings.BASE_DIR) / "visuals/views.py").read_text()
    assert 'libs_for((visual.config or {}).get("kind"))' not in body
    assert body.count('libs_for(visual.render_config.get("kind"))') == 4
