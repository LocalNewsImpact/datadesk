"""Where a pin is: the place and county a lat/lon falls inside.

The geography ICR (docs/GEOGRAPHY_ICR_DATADESK.md, G-WP4) never compares
two pins by their coordinates to decide a match. A pin becomes GEOIDs,
and GEOIDs are compared. This module is that step, and the only place it
happens: the coder's screen resolves a pin in the browser for feedback
("Ashland, Boone County"), the server resolves it again when the answer
is saved and keeps its own answer, and the pipeline's own point is
resolved by the same function when it is scored. One lookup, so a
disagreement is a disagreement about the story and not about two
lookups.

Pure Python over the TopoJSON files the maps already ship
(`static/geo/`): no GEOS, no PostGIS, no call to anybody's geocoder. The
county file is the 500k cartographic boundary per state where one has
been built (`static/geo/counties/<state>.json`, `infra/fetch_boundaries.sh`),
and us-atlas' 1:10m national file everywhere else. Places are per state,
500k, where built.

Planar ray casting in lon/lat. At county scale the difference from a
spherical test is far below the generalisation of the boundaries
themselves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

from django.conf import settings

GEO_DIR = Path(settings.BASE_DIR) / "static" / "geo"


@dataclass(frozen=True)
class Shape:
    geoid: str
    name: str
    #: Each polygon is a list of rings; the first is the outer ring, the
    #: rest holes. Rings are lists of (lon, lat).
    polygons: tuple
    bbox: tuple  # (min_lon, min_lat, max_lon, max_lat)


@dataclass(frozen=True)
class Where:
    """What a pin resolved to. Any part may be missing: a pin in the
    Gulf of Mexico is in no county, and most of rural Missouri is in no
    Census place."""

    state: str | None = None
    county: str | None = None
    county_name: str = ""
    place: str | None = None
    place_name: str = ""

    def as_dict(self):
        return {
            "state": self.state,
            "county": self.county,
            "county_name": self.county_name,
            "place": self.place,
            "place_name": self.place_name,
        }


def _decode_arcs(topology):
    """Absolute lon/lat for every arc, undoing quantisation and delta
    encoding."""
    transform = topology.get("transform")
    arcs = []
    for arc in topology["arcs"]:
        points, x, y = [], 0, 0
        for position in arc:
            if transform:
                x += position[0]
                y += position[1]
                sx, sy = transform["scale"]
                tx, ty = transform["translate"]
                points.append((x * sx + tx, y * sy + ty))
            else:
                points.append((position[0], position[1]))
        arcs.append(points)
    return arcs


def _ring(arcs, indexes):
    """One ring from its arc indexes. A negative index is the arc
    reversed (~i); consecutive arcs share an endpoint, kept once."""
    ring = []
    for index in indexes:
        points = arcs[index] if index >= 0 else arcs[~index][::-1]
        ring.extend(points[1:] if ring else points)
    return ring


def _shapes(topology, layer, geoid_of, name_of):
    arcs = _decode_arcs(topology)
    out = []
    for geometry in topology["objects"][layer]["geometries"]:
        kind = geometry.get("type")
        if kind == "Polygon":
            parts = [geometry["arcs"]]
        elif kind == "MultiPolygon":
            parts = geometry["arcs"]
        else:
            continue
        polygons = tuple(
            tuple(_ring(arcs, ring) for ring in polygon) for polygon in parts
        )
        xs = [x for polygon in polygons for x, _ in polygon[0]]
        ys = [y for polygon in polygons for _, y in polygon[0]]
        if not xs:
            continue
        out.append(
            Shape(
                geoid=geoid_of(geometry),
                name=name_of(geometry),
                polygons=polygons,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
            )
        )
    return tuple(out)


def _inside_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        crosses = (yi > lat) != (yj > lat)
        if crosses and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _contains(shape, lon, lat):
    west, south, east, north = shape.bbox
    if not (west <= lon <= east and south <= lat <= north):
        return False
    for polygon in shape.polygons:
        if _inside_ring(lon, lat, polygon[0]) and not any(
            _inside_ring(lon, lat, hole) for hole in polygon[1:]
        ):
            return True
    return False


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def national_counties():
    """us-atlas 1:10m: every county, coarse."""
    topology = _load(GEO_DIR / "counties-10m.json")
    return _shapes(
        topology,
        "counties",
        lambda g: str(g.get("id", "")).zfill(5),
        lambda g: (g.get("properties") or {}).get("name", ""),
    )


@cache
def state_counties(state):
    """The 500k county boundaries for one state, or None if not built."""
    path = GEO_DIR / "counties" / f"{state}.json"
    if not path.exists():
        return None
    topology = _load(path)
    return _shapes(
        topology,
        "counties",
        lambda g: (g.get("properties") or {}).get("GEOID", ""),
        lambda g: (g.get("properties") or {}).get("NAME", ""),
    )


@cache
def state_places(state):
    """The 500k place boundaries for one state, or () if not built."""
    path = GEO_DIR / "places" / f"{state}.json"
    if not path.exists():
        return ()
    topology = _load(path)
    return _shapes(
        topology,
        "places",
        lambda g: (g.get("properties") or {}).get("GEOID", ""),
        lambda g: (g.get("properties") or {}).get("NAME", ""),
    )


def built_states():
    """States whose place boundaries are in the repo."""
    return sorted(path.stem for path in (GEO_DIR / "places").glob("*.json"))


def resolve(lat, lon):
    """The state, county and place a point falls inside.

    The national file finds the state; that state's 500k counties, where
    built, then decide the county, because the 1:10m lines are a few
    hundred metres out and a pin in a town on a county line is exactly
    the case that matters.
    """
    lat, lon = float(lat), float(lon)
    coarse = next((s for s in national_counties() if _contains(s, lon, lat)), None)
    state = coarse.geoid[:2] if coarse else None
    county = coarse
    # Near a state line the coarse county can be in the wrong state, so
    # try the fine files of that state and its neighbours' too.
    for candidate in _candidate_states(state, lon, lat):
        fine = state_counties(candidate)
        if fine is None:
            continue
        hit = next((s for s in fine if _contains(s, lon, lat)), None)
        if hit is not None:
            county, state = hit, candidate
            break
    if county is None:
        return Where()
    place = next(
        (s for s in state_places(state) if _contains(s, lon, lat)),
        None,
    )
    return Where(
        state=state,
        county=county.geoid,
        county_name=county.name,
        place=place.geoid if place else None,
        place_name=place.name if place else "",
    )


def _candidate_states(state, lon, lat):
    """The coarse state first, then any other built state whose county
    boxes contain the point."""
    seen = []
    if state:
        seen.append(state)
    for path in sorted((GEO_DIR / "counties").glob("*.json")):
        if path.stem in seen:
            continue
        fine = state_counties(path.stem)
        if fine and any(
            s.bbox[0] <= lon <= s.bbox[2] and s.bbox[1] <= lat <= s.bbox[3]
            for s in fine
        ):
            seen.append(path.stem)
    return seen


def county_of(geoid):
    """A county GEOID's name, from the national file."""
    for shape in national_counties():
        if shape.geoid == geoid:
            return shape.name
    return ""
