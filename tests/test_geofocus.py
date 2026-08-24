"""Where a map is centred, and how much of the country it paints.

One field used to answer both: `focus` took a FIPS code and the framing was
inferred from its length, so a county always painted its whole state. That
is a reasonable default and a bad rule -- sometimes the county alone is the
point, sometimes it is that county and the four it borders.
"""

import pytest

from visuals.geofocus import (
    AUTO,
    CITY,
    COUNTY,
    CUSTOM,
    SELECTED,
    STATE,
    WHOLE_COUNTY,
    WHOLE_STATE,
    FocusError,
    counties_in_state,
    frame,
    resolve,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

# --- naming the place --------------------------------------------------------


def test_a_county_is_named_not_looked_up():
    """29019 is Boone County and nobody knows that. The gazetteer the
    corpus is coded with already holds it."""
    assert resolve("Boone, MO", COUNTY) == ("29019", COUNTY)
    assert resolve("Boone County, Missouri", COUNTY) == ("29019", COUNTY)


def test_a_city_resolves_to_its_place_code():
    assert resolve("Columbia, MO", CITY) == ("2915670", CITY)


def test_a_state_resolves_to_two_digits():
    assert resolve("Missouri", STATE) == ("29", STATE)
    assert resolve("MO", STATE) == ("29", STATE)


def test_a_code_passes_through():
    """Specs written before this, and by hand, keep working."""
    assert resolve("29019") == ("29019", COUNTY)
    assert resolve("29") == ("29", STATE)
    assert resolve("2915670") == ("2915670", CITY)


def test_nothing_is_nothing():
    assert resolve("") == ("", "")


def test_an_ambiguous_county_is_refused_rather_than_guessed():
    """Boone is a county in eight states. Guessing is how a map ends up
    over the wrong half of the country, and the error names the fix."""
    with pytest.raises(FocusError) as caught:
        resolve("Boone")
    message = str(caught.value)
    assert "MO" in message and "Boone, AR" in message


def test_a_city_needs_its_state():
    with pytest.raises(FocusError):
        resolve("Columbia", CITY)


def test_a_place_that_does_not_exist_says_so():
    with pytest.raises(FocusError):
        resolve("Nowhere, MO", COUNTY)


# --- how far out to paint ----------------------------------------------------


def test_auto_leaves_the_renderer_to_decide():
    """The previous behaviour, kept: frame on where the stories are."""
    assert frame("29019", COUNTY, AUTO) == []


def test_only_what_is_selected():
    assert frame("29019", COUNTY, SELECTED) == ["29019"]


def test_a_city_paints_the_county_it_sits_in():
    """A place has no boundary in the counties file; the honest answer to
    'only this' is the county containing it."""
    assert frame("2915670", CITY, SELECTED) == ["29019"]
    assert frame("2915670", CITY, WHOLE_COUNTY) == ["29019"]


def test_the_whole_state():
    counties = frame("29019", COUNTY, WHOLE_STATE)
    assert "29019" in counties
    assert len(counties) == len(counties_in_state("29"))
    assert len(counties) > 100, "Missouri has 114 counties and one city"


def test_a_state_selected_is_the_state():
    """A state has no single county, so 'only what is selected' can only
    mean the state itself."""
    assert frame("29", STATE, SELECTED) == sorted(counties_in_state("29"))


def test_named_neighbours_join_the_focus():
    """The focus is always painted; the named ones join it."""
    counties = frame("2915670", CITY, CUSTOM, "Callaway, MO; Howard, MO; 29053")
    assert counties == ["29019", "29027", "29053", "29089"]


def test_the_custom_list_splits_on_semicolons_not_commas():
    """A comma belongs to the place -- 'Callaway, MO'. Splitting on it
    turns one county into a county and a state that is not one."""
    assert frame("29019", COUNTY, CUSTOM, "Callaway, MO") == ["29019", "29027"]


def test_a_custom_entry_that_is_not_a_county_is_refused():
    with pytest.raises(FocusError):
        frame("29019", COUNTY, CUSTOM, "Atlantis, MO")


def test_an_unknown_extent_is_refused():
    with pytest.raises(FocusError):
        frame("29019", COUNTY, "everything")


# --- what reaches the renderer ----------------------------------------------


def test_the_builder_saves_the_counties_not_the_rule():
    """The renderer is handed counties to draw rather than a rule to
    apply, so what a published map shows can be read off its config."""
    from visuals.builder import config_from_form

    config = config_from_form(
        {
            "kind": "storymap",
            "focus": "Boone, MO",
            "focus_level": COUNTY,
            "extent": SELECTED,
        }
    )
    assert config["focus"] == "29019"
    assert config["focus_level"] == COUNTY
    assert config["frame"] == ["29019"]


def test_the_builder_reports_an_unresolvable_focus():
    from visuals.builder import BuilderError, config_from_form

    with pytest.raises(BuilderError) as caught:
        config_from_form({"kind": "storymap", "focus": "Boone"})
    assert "Name the state" in str(caught.value)


def test_the_renderer_prefers_an_explicit_frame():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    ).read_text()
    assert "config.frame" in js
    assert "wanted.has(String(f.id))" in js


def test_the_builder_offers_every_rung_and_extent():
    from pathlib import Path

    form = (
        Path(__file__).resolve().parent.parent / "templates/visuals/builder_edit.html"
    ).read_text()
    assert 'name="focus_level"' in form
    assert 'name="extent"' in form
    assert 'name="extent_custom"' in form
    for value in (CITY, COUNTY, STATE):
        assert f'value="{value}"' in form


# --- the dataset usually settles it -----------------------------------------
#
# A dataset is commonly a state, and carries `default_state`. So the
# ambiguity above is mostly theoretical: a map of a Missouri dataset saying
# "Boone" means Boone County, Missouri, and asking the author to type ", MO"
# is asking them for something already recorded.


def test_a_bare_name_resolves_against_the_datasets_state():
    assert resolve("Boone", COUNTY, "MO") == ("29019", COUNTY)
    assert resolve("Columbia", CITY, "MO") == ("2915670", CITY)


def test_a_named_state_beats_the_datasets():
    """The default fills a gap; it does not overrule what was typed."""
    assert resolve("Boone, KY", COUNTY, "MO") == ("21015", COUNTY)


def test_the_state_comes_from_what_the_visual_is_wired_to(crawler_schema):
    from explorer.models import Dataset
    from visuals.geofocus import state_of

    Dataset.objects.create(
        id="d-mo", slug="mizzou", label="Missouri", meta={"default_state": "MO"}
    )
    assert state_of(["mizzou"]) == "MO"


def test_two_states_have_no_single_answer(crawler_schema):
    """A map across Missouri and Pennsylvania cannot assume either, so the
    author is asked rather than told."""
    from explorer.models import Dataset
    from visuals.geofocus import state_of

    Dataset.objects.create(
        id="d-mo2", slug="mizzou2", label="Missouri", meta={"default_state": "MO"}
    )
    Dataset.objects.create(
        id="d-pa", slug="lehigh", label="Lehigh", meta={"default_state": "PA"}
    )
    assert state_of(["mizzou2", "lehigh"]) == ""
    assert state_of([]) == ""


def test_the_custom_list_uses_the_datasets_state_too():
    """Naming neighbours should not need the state repeated on each."""
    assert frame("29019", COUNTY, CUSTOM, "Callaway; Howard", "MO") == [
        "29019",  # Boone, the focus, always painted
        "29027",  # Callaway
        "29089",  # Howard
    ]
