"""A coder's pin is compared as GEOIDs, never as a coordinate.

`review.geo_resolve` is the one lookup that turns a pin into a place and
a county, for the coder's screen, for the saved answer and for the
pipeline's own point (docs/GEOGRAPHY_ICR_DATADESK.md, G-WP4).

The expected answers are the Census Bureau's, from its geocoder
(geographies/coordinates, ACS2023_Current), not this code's. Fourteen of
the pins are within a few hundred metres of a county line and were
chosen because us-atlas' 1:10m national file puts every one of them in
the WRONG county -- which is why the state 500k county files exist
(infra/fetch_boundaries.sh) and are consulted first.
"""

import pytest

from review.geo_resolve import _contains as contains
from review.geo_resolve import national_counties, resolve

#: (kind, lat, lon, county GEOID, place GEOID or None)
PINS = [
    ("town", 38.9517, -92.3341, "29019", "2915670"),  # Boone, Columbia
    ("town", 39.0997, -94.5786, "29095", "2938000"),  # Jackson, Kansas City
    ("town", 38.627, -90.199, "29510", "2965000"),  # St. Louis, St. Louis
    ("town", 37.2089, -93.2923, "29077", "2970000"),  # Greene, Springfield
    ("town", 38.5767, -92.1735, "29051", "2937000"),  # Cole, Jefferson City
    ("town", 37.0842, -94.5133, "29097", "2937592"),  # Jasper, Joplin
    ("town", 39.7675, -94.8467, "29021", "2964550"),  # Buchanan, St. Joseph
    ("town", 36.757, -90.3929, "29023", "2959096"),  # Butler, Poplar Bluff
    ("town", 38.7045, -93.2283, "29159", "2966440"),  # Pettis, Sedalia
    ("town", 39.1689, -91.8829, "29007", "2947648"),  # Audrain, Mexico
    ("town", 40.44, -79.99, "42003", "4261000"),  # Allegheny, Pittsburgh
    ("town", 44.98, -93.27, "27053", "2743000"),  # Hennepin, Minneapolis
    ("town", 47.61, -122.33, "53033", "5363000"),  # King, Seattle
    ("town", 38.8956, -92.2096, "29019", None),  # Boone
    ("rural", 38.7, -92.23, "29019", None),  # Boone
    ("rural", 37.6, -91.2, "29065", None),  # Dent
    ("rural", 40.2, -93.5, "29079", None),  # Grundy
    ("rural", 36.8, -92.9, "29213", None),  # Taney
    ("rural", 39.5, -90.95, "17149", None),  # Pike
    ("county_line", 40.03773, -94.29624, "29063", None),  # DeKalb
    ("county_line", 39.17174, -94.37763, "29047", None),  # Clay
    ("county_line", 36.59584, -91.6772, "29091", None),  # Howell
    ("county_line", 36.55064, -92.119, "29153", None),  # Ozark
    ("county_line", 36.52603, -90.21119, "29069", None),  # Dunklin
    ("county_line", 37.42655, -89.43488, "29031", None),  # Cape Girardeau
    ("county_line", 38.38962, -92.13781, "29051", None),  # Cole
    ("county_line", 40.0579, -95.41882, "29087", None),  # Holt
    ("county_line", 37.31062, -93.07583, "29225", None),  # Webster
    ("county_line", 39.3391, -91.80803, "29137", None),  # Monroe
    ("county_line", 39.84025, -94.91943, "29003", None),  # Andrew
    ("county_line", 38.42814, -92.49445, "29051", None),  # Cole
    ("county_line", 37.73665, -90.96078, "29093", None),  # Iron
    ("county_line", 39.34303, -93.076, "29041", None),  # Chariton
]


@pytest.mark.parametrize("kind,lat,lon,county,place", PINS)
def test_a_pin_resolves_as_the_census_bureau_resolves_it(kind, lat, lon, county, place):
    where = resolve(lat, lon)
    assert where.county == county
    assert where.place == place
    assert where.state == county[:2]


def test_the_coarse_file_alone_would_have_got_the_county_lines_wrong():
    """The point of the 500k files: on every county-line pin the 1:10m
    county is a different one."""
    for kind, lat, lon, county, _ in PINS:
        if kind != "county_line":
            continue
        coarse = next(s for s in national_counties() if contains(s, lon, lat))
        assert coarse.geoid != county


def test_a_pin_in_the_sea_is_in_no_county():
    where = resolve(28.0, -90.0)  # Gulf of Mexico
    assert where.county is None and where.place is None


def test_a_pin_outside_the_built_states_still_finds_its_county():
    """Kansas has no place file: the county comes from the national
    file and there is no place, rather than no answer."""
    where = resolve(39.05, -95.68)  # Topeka's edge, Shawnee County, KS
    assert where.county == "20177" and where.place is None


def test_a_county_or_place_has_a_box_for_the_map():
    from review.geo_resolve import bbox_of

    west, south, east, north = bbox_of("29019")  # Boone County, MO
    assert west < -92.33 < east and south < 38.95 < north
    assert bbox_of("2915670") is not None  # Columbia: a built state's place
    assert bbox_of("0102860") is None  # Ashland, AL: no place file
    assert bbox_of("01001") is not None  # a county anywhere: the 10m file
    assert bbox_of("") is None and bbox_of("29") is None


def test_every_built_town_has_a_point_inside_it():
    """A town chosen by name is pinned at this point, so it must resolve
    back to the town: checked over every place in every built state."""
    from review.geo_resolve import built_states, inside_point, state_places

    for state in built_states():
        for shape in state_places(state):
            assert resolve(*inside_point(shape.geoid)).place == shape.geoid
    assert inside_point("0102860") is None  # a state not built
