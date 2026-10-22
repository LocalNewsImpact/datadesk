"""Civic interest areas and their coverage density, drawn by the builder.

docs/CIVIC_INTEREST_MAPS.md. Two figures of one area, made here:

- `area_figure`: a town's civic interest area from the uploaded table (one
  row per 3 km hexagon: its travel value, news-tie lift, shown or not).
  The travel model and its licensed data stay with the analysis; the table
  is what it hands over.
- `coverage_figure`: the local news reaching each hexagon of that area,
  computed from the corpus by the rules the analysis used
  (`coverage_values`), with the area's own frame so a swipe lines the two
  up exactly.

Both are figures: every shape projected into a W x H frame, which is the
shape the runtime's `drawCivicFigure` draws and the swipe composes.

Geography is public Census data, shipped beside this module
(`data/civic_grid_29.json`, made by the analysis's
`flows/export_civic_grid.py`): every Missouri hexagon with its county, town
and 2020 population; every town with its name, centre, population and the
hexagons a story about it reaches; county outlines for Missouri and the
bordering states; the TV-market outlines; two landmarks.
"""

import json
import math
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

GRID_FILE = Path(__file__).resolve().parent / "data" / "civic_grid_29.json"

#: The frame: 354 x 338, padded 4, about 190 x 180 km around the area.
W, H, PAD = 354, 338, 4
HALF_W, HALF_H, HOME_MARGIN = 1.15, 0.82, 0.08
SIZE = 1.5  # hexagon circumradius, km: 3 km across
ROMAN = ("I", "II", "III", "IV", "V")

AREA_SCALE = {"kind": "bands", "floor": 5, "cuts": [10, 20, 40, 80]}
COVERAGE_SCALE = {
    "kind": "log",
    "cap": 9,
    "tiers": {"frequent": 9, "regular": 2},
    "breadth_full": 8,
}
#: Below this a hexagon has no coverage at all.
COVERED = 0.005

SOURCE = (
    "Source: Missouri School of Journalism analysis of local news stories "
    "and Advan Research, Neighborhood Patterns Plus (Dewey Data), "
    "January–July 2026; U.S. Census Bureau 2020 block groups, places "
    "and counties."
)


class CivicError(ValueError):
    """A civic map that cannot be drawn, and why, in the author's terms."""


# --- the grid ----------------------------------------------------------------


@lru_cache(maxsize=1)
def grid():
    """The grid file, with each county's hexagons gathered once."""
    g = json.loads(GRID_FILE.read_text())
    by_county = defaultdict(list)
    for key, (county, _town, _pop) in g["hexes"].items():
        by_county[county].append(key)
    g["county_hexes"] = dict(by_county)
    lat0 = math.radians(g["grid"]["lat0"])
    g["kx"], g["ky"] = 111.32 * math.cos(lat0), g["grid"]["ky"]
    # Story reach measures distance at 38.6 degrees, as the analysis did.
    g["reach_kx"] = 111.32 * math.cos(math.radians(g["grid"]["reach_lat"]))
    return g


def _qr(key):
    q, r = key.split(",")
    return int(q), int(r)


def hex_centre(key):
    """(lon, lat) of a hexagon's centre: pointy-top axial (q, r) on a plane
    of x = lon * kx, y = lat * ky, km."""
    g = grid()
    q, r = _qr(key)
    return SIZE * math.sqrt(3) * (q + r / 2) / g["kx"], SIZE * 1.5 * r / g["ky"]


def hex_corners(key):
    g = grid()
    q, r = _qr(key)
    cx, cy = SIZE * math.sqrt(3) * (q + r / 2), SIZE * 1.5 * r
    return [
        (
            (cx + SIZE * math.cos(math.radians(60 * i - 30))) / g["kx"],
            (cy + SIZE * math.sin(math.radians(60 * i - 30))) / g["ky"],
        )
        for i in range(6)
    ]


def hexes_within(lon, lat, km):
    """The Missouri hexagons whose centres lie within `km` of a point."""
    g = grid()
    kx, ky, rkx = g["kx"], g["ky"], g["reach_kx"]
    r0 = math.floor((lat - km / ky) * ky / (1.5 * SIZE)) - 1
    r1 = math.ceil((lat + km / ky) * ky / (1.5 * SIZE)) + 1
    out = []
    for r in range(r0, r1 + 1):
        q0 = math.floor((lon - km / rkx) * kx / (math.sqrt(3) * SIZE) - r / 2) - 1
        q1 = math.ceil((lon + km / rkx) * kx / (math.sqrt(3) * SIZE) - r / 2) + 1
        for q in range(q0, q1 + 1):
            key = f"{q},{r}"
            if key not in g["hexes"]:
                continue
            x, y = hex_centre(key)
            if math.hypot((x - lon) * rkx, (y - lat) * ky) <= km:
                out.append(key)
    return out


# --- projection --------------------------------------------------------------


class Projection:
    """d3.geoConicEqualArea().parallels([36.5, 40.5]).rotate([92.4, 0])
    .fitExtent([[4, 4], [W - 4, H - 4]], frame) -- the projection the
    analysis's print figures use, so a figure made here matches them."""

    def __init__(self, frame):
        p0, p1 = math.radians(36.5), math.radians(40.5)
        self.n = (math.sin(p0) + math.sin(p1)) / 2
        self.c = 1 + math.sin(p0) * (2 * self.n - math.sin(p0))
        self.r0 = math.sqrt(self.c) / self.n
        pts = [self._raw(lon, lat) for lon, lat in frame]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        w, h = W - 2 * PAD, H - 2 * PAD
        self.k = min(w / (x1 - x0), h / (y1 - y0))
        self.tx = PAD + (w - self.k * (x1 + x0)) / 2
        self.ty = PAD + (h - self.k * (y1 + y0)) / 2

    def _raw(self, lon, lat):
        lam = math.radians(lon + 92.4) * self.n
        r = math.sqrt(self.c - 2 * self.n * math.sin(math.radians(lat))) / self.n
        # y grows downward on the page, as d3's does.
        return r * math.sin(lam), r * math.cos(lam) - self.r0

    def __call__(self, lon, lat):
        x, y = self._raw(lon, lat)
        return self.tx + self.k * x, self.ty + self.k * y


def _ring_d(ring, proj):
    pts = [proj(*p) for p in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z"


def _geometry_d(geometry, proj):
    polys = (
        [geometry["coordinates"]]
        if geometry["type"] == "Polygon"
        else geometry["coordinates"]
    )
    return "".join(_ring_d(ring, proj) for poly in polys for ring in poly if ring)


def _bounds(geometry):
    polys = (
        [geometry["coordinates"]]
        if geometry["type"] == "Polygon"
        else geometry["coordinates"]
    )
    pts = [p for poly in polys for ring in poly for p in ring]
    return (
        min(p[0] for p in pts),
        min(p[1] for p in pts),
        max(p[0] for p in pts),
        max(p[1] for p in pts),
    )


def frame_of(travel, home_county):
    """The frame's four corners, lon/lat: a fixed box about 190 x 180 km,
    centred on the travel-weighted centre of the area's hexagons of 10+,
    shifted only as far as keeps the whole home county in view."""
    strong = [(k, v) for k, v in travel.items() if v >= 10] or list(travel.items())
    if not strong:
        raise CivicError("The area has no hexagons to frame.")
    total = sum(v for _, v in strong) or len(strong)
    cx = sum((v or 1) * hex_centre(k)[0] for k, v in strong) / total
    cy = sum((v or 1) * hex_centre(k)[1] for k, v in strong) / total
    county = grid()["counties"].get(home_county)
    if county:
        x0, y0, x1, y1 = _bounds(county[1])
        cx = min(max(cx, x1 + HOME_MARGIN - HALF_W), x0 - HOME_MARGIN + HALF_W)
        cy = min(max(cy, y1 + HOME_MARGIN - HALF_H), y0 - HOME_MARGIN + HALF_H)
    return [
        [round(cx - HALF_W, 5), round(cy - HALF_H, 5)],
        [round(cx + HALF_W, 5), round(cy - HALF_H, 5)],
        [round(cx - HALF_W, 5), round(cy + HALF_H, 5)],
        [round(cx + HALF_W, 5), round(cy + HALF_H, 5)],
    ]


# --- labels ------------------------------------------------------------------

_NARROW, _SLIM, _WIDE = set("ilj.,'’ |!:;"), set("frtI()-"), set("mwMW")


def text_width(text, size, weight=400):
    """A label's width in px, estimated: the runtime measures nothing, and a
    figure made on the server has no font to measure with."""
    em = 0.0
    for ch in text:
        if ch in _NARROW:
            em += 0.28
        elif ch in _SLIM:
            em += 0.36
        elif ch in _WIDE:
            em += 0.86
        elif ch.isupper():
            em += 0.68
        else:
            em += 0.55
    return em * size * (1.06 if weight >= 600 else 1.0)


def place_labels(proj, values, floor, town, home_hexes):
    """Town names in plain ink, never haloed and never on a dark hexagon.

    The home town bold; towns standing on a shaded hexagon, the 8 most local
    by population; for orientation up to 4 unshaded towns of 20,000+ in
    small grey; and any cluster of 5+ shaded hexagons with no name in it,
    named by a landmark in it or its largest town. Each name beside its dot
    where there is room, else further out with a hairline to it."""
    g = grid()
    on = list(values)
    centre = {k: proj(*hex_centre(k)) for k in on}
    any_key = on[0] if on else next(iter(g["hexes"]))
    hx, hy = proj(*hex_centre(any_key))
    corner = proj(*hex_corners(any_key)[0])
    hex_r = math.hypot(corner[0] - hx, corner[1] - hy)
    dark = [centre[k] for k in on if values[k] >= 20]
    dark40 = [centre[k] for k in on if values[k] >= 40]

    def over_dark(b):
        return any(
            b[0] - hex_r < cx < b[2] + hex_r and b[1] - hex_r < cy < b[3] + hex_r
            for cx, cy in dark
        )

    def all_dark(b):
        for i in range(7):
            for j in range(3):
                x, y = b[0] + (b[2] - b[0]) * i / 6, b[1] + (b[3] - b[1]) * j / 2
                if not any(
                    math.hypot(cx - x, cy - y) <= hex_r * 0.95 for cx, cy in dark40
                ):
                    return False
        return True

    placed = []

    def free(b):
        return (
            b[0] >= 2
            and b[1] >= 2
            and b[2] <= W - 2
            and b[3] <= H - 2
            and not any(
                b[0] < o[2] + 1
                and b[2] > o[0] - 1
                and b[1] < o[3] + 1
                and b[3] > o[1] - 1
                for o in placed
            )
        )

    def local_at(x, y):
        return max(
            (
                values[k]
                for k in on
                if math.hypot(centre[k][0] - x, centre[k][1] - y) <= hex_r
            ),
            default=0,
        )

    def hex_at(x, y):
        best, bd = None, math.inf
        for k in on:
            d = math.hypot(centre[k][0] - x, centre[k][1] - y)
            if d < bd:
                best, bd = k, d
        return best if bd <= hex_r * 1.2 else None

    in_view = lambda x, y: 0 <= x <= W and 0 <= y <= H  # noqa: E731
    # The home town by name, the one nearest the area's home hexagons when
    # Missouri has two of that name.
    hc = [hex_centre(k) for k in home_hexes] or [hex_centre(k) for k in on]
    hlon, hlat = sum(p[0] for p in hc) / len(hc), sum(p[1] for p in hc) / len(hc)
    homes = [p for p in g["places"].values() if p[0] == town]
    home_place = min(
        homes, key=lambda p: math.hypot(p[1] - hlon, p[2] - hlat), default=None
    )
    towns = []
    for p in g["places"].values():
        x, y = proj(p[1], p[2])
        if in_view(x, y):
            towns.append(
                {"name": p[0], "pop": p[3], "lon": p[1], "lat": p[2], "x": x, "y": y}
            )

    def is_home(t):
        return bool(home_place) and t["name"] == town and t["lon"] == home_place[1]

    cand = [
        dict(t, v=local_at(t["x"], t["y"]))
        for t in towns
        if t["pop"] >= 2500 or is_home(t)
    ]
    home = [t for t in cand if is_home(t)]
    market = sorted(
        (t for t in cand if t["name"] != town and t["v"] >= floor),
        key=lambda t: -t["v"] * math.sqrt(t["pop"] or 1),
    )[:8]
    context = sorted(
        (t for t in cand if t["name"] != town and t["v"] < floor and t["pop"] >= 20000),
        key=lambda t: -t["pop"],
    )[:4]

    # Clusters: connected shaded hexagons.
    comp, cid, onset = {}, 0, set(on)
    for k in on:
        if k in comp:
            continue
        stack, comp[k] = [k], cid
        while stack:
            q, r = _qr(stack.pop())
            for dq, dr in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)):
                n = f"{q + dq},{r + dr}"
                if n in onset and n not in comp:
                    comp[n] = cid
                    stack.append(n)
        cid += 1
    labelled = {
        comp.get(hex_at(t["x"], t["y"]), -1) for t in (*home, *market, *context)
    }
    sizes = defaultdict(int)
    for c in comp.values():
        sizes[c] += 1
    in_cluster = defaultdict(list)
    for t in towns:
        k = hex_at(t["x"], t["y"])
        if k is not None:
            in_cluster[comp[k]].append(t)
    marks = defaultdict(list)
    for name, lon, lat in g.get("landmarks", []):
        x, y = proj(lon, lat)
        k = hex_at(x, y)
        if k is not None:
            marks[comp[k]].append(
                {"name": name, "pop": 0, "x": x, "y": y, "landmark": True}
            )
    clusters = []
    for c, n in sizes.items():
        if n < 5 or c in labelled:
            continue
        pick = (marks.get(c) or [None])[0] or max(
            in_cluster.get(c, []), key=lambda t: t["pop"], default=None
        )
        if pick:
            clusters.append(dict(pick, tier=1))

    tiers = [
        *(dict(t, tier=0) for t in home),
        *(dict(t, tier=1) for t in market),
        *clusters,
        *(dict(t, tier=2) for t in context),
    ]
    labels, leaders, dots = [], [], []
    for t in tiers:
        x, y, tier = t["x"], t["y"], t["tier"]
        size, weight = (8, 6.8, 6)[tier], (700, 500, 400)[tier]
        w = text_width(t["name"], size, weight)
        pick = None
        for dist in (3, 8, 13, 19, 26, 34, 44, 56):
            for deg in range(0, 360, 90 if dist == 3 else 15):
                rad = math.radians(deg)
                dx, dy = math.cos(rad), math.sin(rad)
                anchor = "middle" if abs(dx) < 0.3 else "start" if dx > 0 else "end"
                tx = x + dist * dx
                ty = (
                    y
                    + dist * dy
                    + ((size if dy > 0 else 0) if abs(dx) < 0.3 else size * 0.35)
                )
                x0 = (
                    tx - w
                    if anchor == "end"
                    else tx - w / 2 if anchor == "middle" else tx
                )
                b = (x0, ty - size, x0 + w, ty + 2)
                ok = free(b) and (
                    (not over_dark(b) or all_dark(b)) if tier == 0 else not over_dark(b)
                )
                if ok:
                    pick = (tx, ty, anchor, b, dist)
                    break
            if pick:
                break
        if not pick:
            continue
        tx, ty, anchor, b, dist = pick
        placed.append(b)
        label = {
            "text": t["name"],
            "x": round(tx, 1),
            "y": round(ty, 1),
            "anchor": anchor,
            "size": size,
            "weight": weight,
            "muted": tier == 2,
            "italic": bool(t.get("landmark")),
        }
        if tier == 0 and all_dark(b):
            label["on_dark"] = True
        labels.append(label)
        if dist > 3:
            lx, ly = max(b[0], min(x, b[2])), max(b[1], min(y, b[3]))
            leaders.append([round(x, 1), round(y, 1), round(lx, 1), round(ly, 1)])
        dots.append([round(x, 1), round(y, 1), "muted" if tier == 2 else "ink"])
    return labels, leaders, dots


# --- figures -----------------------------------------------------------------


def _draw(measure, frame, home_county, region, values, ring, town, home_hexes):
    """The parts both figures share: frame, hexagon paths, counties of any
    state in view, the TV market and the labels."""
    g = grid()
    proj = Projection(frame)

    def hex_d(key):
        return _ring_d(hex_corners(key), proj)

    counties = []
    for fips, (_name, geometry) in g["counties"].items():
        x0, y0, x1, y1 = _bounds(geometry)
        px = [proj(x, y) for x in (x0, x1) for y in (y0, y1)]
        if max(p[0] for p in px) < 0 or min(p[0] for p in px) > W:
            continue
        if max(p[1] for p in px) < 0 or min(p[1] for p in px) > H:
            continue
        counties.append(
            {"id": fips, "d": _geometry_d(geometry, proj), "home": fips == home_county}
        )
    market = g["markets"].get(region)
    floor = COVERED if measure == "coverage" else AREA_SCALE["floor"]
    labels, leaders, dots = place_labels(proj, values, floor, town, home_hexes)
    hexes = []
    for key, v in values.items():
        cx, cy = proj(*hex_centre(key))
        hexes.append(
            {
                "id": key,
                "d": hex_d(key),
                "c": [round(cx, 1), round(cy, 1)],
                "v": round(v, 2),
            }
        )
    return {
        "W": W,
        "H": H,
        "frame": frame,
        "counties": counties,
        "market": _geometry_d(market[1], proj) if market else "",
        "tv_market": market[0] if market else None,
        "ring": [hex_d(k) for k in ring],
        "hexes": hexes,
        "labels": labels,
        "leaders": leaders,
        "dots": dots,
        "source": SOURCE,
    }


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def area_figure(rows, town, area):
    """A town's civic interest area from the uploaded table."""
    town = (town or "").strip()
    try:
        area = int(_num(area, 1))
    except (TypeError, ValueError):
        area = 1
    if not town:
        raise CivicError("Name the town on the Look step.")
    rows = [r for r in rows or [] if isinstance(r, dict)]
    if rows and not {"town", "area", "hex", "travel", "shown"} <= set(rows[0]):
        raise CivicError(
            "That table is not a civic interest area table: it needs town, "
            "area, hex, travel and shown columns."
        )
    mine = [
        r
        for r in rows
        if str(r.get("town")) == town and int(_num(r.get("area"))) == area
    ]
    if not mine:
        towns = sorted({str(r.get("town")) for r in rows})
        raise CivicError(
            f"The table has no {town} area {area}. Its towns: {', '.join(towns)}."
        )
    hexes = grid()["hexes"]
    mine = [r for r in mine if r.get("hex") in hexes]
    areas_in_town = len(
        {int(_num(r.get("area"))) for r in rows if str(r.get("town")) == town}
    )
    shown = [r for r in mine if int(_num(r.get("shown")))]
    travel = {r["hex"]: _num(r.get("travel")) for r in shown}
    ring = [r["hex"] for r in mine if not int(_num(r.get("shown")))]
    home_hexes = [r["hex"] for r in mine if int(_num(r.get("home")))]
    counted = defaultdict(int)
    for k in home_hexes or list(travel):
        counted[hexes[k][0]] += 1
    home_county = max(counted, key=counted.get) if counted else ""
    region = str(mine[0].get("region") or "")
    frame = frame_of(travel, home_county)
    fig = _draw("area", frame, home_county, region, travel, ring, town, home_hexes)
    lifts = {r["hex"]: _num(r.get("lift")) for r in shown}
    for h in fig["hexes"]:
        lift = lifts.get(h["id"], 0.0)
        if lift >= 0.10:
            h["lift"] = round(lift, 3)
    residents = int(_num(mine[0].get("residents")))
    share = _num(mine[0].get("share"))
    title = f"{town}'s civic interest area" + (
        f" {ROMAN[area - 1]}" if areas_in_town > 1 and area <= len(ROMAN) else ""
    )
    fig.update(
        {
            "figure": 1,
            "measure": "area",
            "region": region,
            "town": town,
            "rank": area,
            "areas_in_town": areas_in_town,
            "home_county": home_county,
            "home": home_hexes,
            "footprint": list(travel) + ring,
            "title": title,
            "subtitle": (
                f"About {residents:,} residents of {town} ({round(100 * share)}%) "
                "travel in this pattern, January–July 2026. How local each "
                "place is: their travel there, strengthened by news ties "
                "– coverage each way, and places both visit and report "
                "on; by 3 km hexagon, home = 100"
            ),
            "residents": residents,
            "share": share,
            "scale": AREA_SCALE,
            "shares": None,
        }
    )
    return fig


def coverage_figure(area, values, breadth, months_label=""):
    """The coverage density of an area figure's hexagons, on its frame."""
    footprint = area.get("footprint") or []
    if not footprint:
        raise CivicError("That civic interest area has no hexagons.")
    vals = {k: values.get(k, 0.0) for k in footprint}
    on = {k: v for k, v in vals.items() if v >= COVERED}
    ring = [k for k in footprint if k not in on]
    fig = _draw(
        "coverage",
        area["frame"],
        area.get("home_county", ""),
        area.get("region", ""),
        on,
        ring,
        area["town"],
        area.get("home") or [],
    )
    for h in fig["hexes"]:
        h["n"] = breadth.get(h["id"], 0)
    total = len(vals) or 1

    def share(lo, hi):
        return round(100 * sum(1 for v in vals.values() if lo <= v < hi) / total)

    shares = {
        "frequent": share(9, math.inf),
        "regular": share(2, 9),
        "sporadic": share(COVERED, 2),
        "none": share(-1, COVERED),
        "balanced": round(100 * sum(1 for k in vals if breadth.get(k, 0) >= 8) / total),
    }
    fig.update(
        {
            "figure": 1,
            "measure": "coverage",
            "region": area.get("region", ""),
            "town": area["town"],
            "rank": area["rank"],
            "areas_in_town": area.get("areas_in_town", 1),
            "home_county": area.get("home_county", ""),
            "title": f"{area['title']}: coverage density",
            "subtitle": (
                "Coverage density: local news stories a month whose area "
                "includes each 3 km hexagon"
                f"{', ' + months_label if months_label else ''}; "
                "a story centred there counts 1, one mentioning it 0.5; a story "
                "counts across the area its scope names (neighbourhood, town, "
                "region, county), less in each hexagon the larger that area. "
                f"Frequent coverage (9+ a month): {shares['frequent']}% of the "
                f"area; regular (2–8): {shares['regular']}%; sporadic "
                f"(under 2): {shares['sporadic']}%; none: {shares['none']}%. "
                "Colour shows how many stories; size shows breadth – full "
                "size only where at least 8 of the 10 civic information "
                f"categories each get a story a month – {shares['balanced']}% "
                "of the area"
            ),
            "residents": area.get("residents"),
            "share": area.get("share"),
            "scale": COVERAGE_SCALE,
            "shares": shares,
        }
    )
    return fig


# --- coverage density --------------------------------------------------------
#
# Each story stands for an area set by its geographic scope, centred where
# the enrichment placed it:
#
#   neighborhood_community        3 km around the point
#   city_municipality,            the town (its 2020 place) and 3 km around
#   elsewhere_to_local,           it; a point in no town, 5 km around it
#   local_to_elsewhere, unscoped
#   regional                      40 km around the point
#   statewide, national,          no local area, and their mentions are not
#   international, other          local coverage of the places they name
#
# A story placed only at a county stands for the county. Central focus 1;
# each town or county it mentions 0.5. The larger the area the less each
# hexagon gets: towns and neighbourhoods by the square root of their size,
# counties and regions counted once and spread by where people live
# (weight x min(1, (7 / hexagons) ^ alpha)). A hexagon keeps a story's
# strongest claim. Sports counts half: a game involves two places by nature.

CATEGORIES = (
    "Emergencies and Public Safety",
    "Civic information",
    "Political life",
    "Education",
    "Environment and Planning",
    "Health",
    "Transportation Systems",
    "Economic Development",
    "Civic Life",
    "Sports",
)
#: Outlets that are not local news for where they are filed: student and
#: campus outlets in Columbia, and statewide outlets covering the capitol.
NOT_LOCAL = frozenset(
    {
        "The Maneater",
        "CoMoBuzz",
        "Missouri Business Alert",
        "The Missouri Independent",
        "Missouri Net",
        "Missouri Scout",
        "The Missouri Times",
    }
)
REACH_KM = {"neighborhood_community": 3.0, "regional": 40.0}
TOWN_SCOPES = frozenset(
    {"city_municipality", "elsewhere_to_local", "local_to_elsewhere", ""}
)
WIDE = frozenset({"statewide", "national", "international", "other"})
TOWN_MISS_KM = 5.0
A0, ALPHA_TOWN, ALPHA_REGION = 7, 0.5, 1.0
MENTION, SPORTS_WEIGHT = 0.5, 0.5
BALANCED_FLOOR = 1.0


class _Reach:
    """The hexagons a story claims, and the weight on each, cached by area."""

    def __init__(self, footprint):
        self.g = grid()
        self.footprint = set(footprint)
        self.cache = {}

    def _weights(self, key, hexes, w, by_pop):
        if (key, w) in self.cache:
            return self.cache[(key, w)]
        n = len(hexes)
        out = {}
        if n:
            per = w * min(1.0, (A0 / n) ** (ALPHA_REGION if by_pop else ALPHA_TOWN))
            pop = {k: self.g["hexes"][k][2] for k in hexes}
            total = sum(pop.values())
            for k in hexes:
                if k in self.footprint:
                    out[k] = per * n * pop[k] / total if by_pop and total > 0 else per
        self.cache[(key, w)] = out
        return out

    def town(self, geoid, w):
        place = self.g["places"].get(geoid)
        return self._weights(("town", geoid), place[4] if place else [], w, False)

    def county(self, fips, w):
        return self._weights(
            ("county", fips), self.g["county_hexes"].get(fips, []), w, True
        )

    def around(self, lon, lat, km, w, by_pop):
        key = ("km", round(lon, 5), round(lat, 5), km)
        if (key, w) not in self.cache:
            return self._weights(key, hexes_within(lon, lat, km), w, by_pop)
        return self.cache[(key, w)]


def coverage_values(stories, mentions, footprint, months=1.0):
    """Stories a month and categories met, per footprint hexagon.

    `stories`: (id, scope, level, geoid, lon, lat, label, town) for each
    story, `town` the place holding a block-level point. `mentions`: (id,
    level, geoid) for every place and county each story names.
    Returns ({hexagon: stories a month}, {hexagon: categories met})."""
    reach = _Reach(footprint)
    places = reach.g["places"]
    claims = defaultdict(dict)
    scope_of, label_of = {}, {}

    def claim(story, weights):
        mine = claims[story]
        for k, w in weights.items():
            if mine.get(k, 0) < w:
                mine[k] = w

    for sid, scope, level, geoid, lon, lat, label, town in stories:
        scope = scope or ""
        scope_of[sid], label_of[sid] = scope, label
        has_point = lat is not None and lon is not None
        if level == "county":
            if scope in REACH_KM or scope in TOWN_SCOPES:
                claim(sid, reach.county(geoid, 1.0))
            continue
        if level not in ("block", "place"):
            continue
        if scope in REACH_KM and has_point:
            claim(
                sid, reach.around(lon, lat, REACH_KM[scope], 1.0, scope == "regional")
            )
        elif scope in TOWN_SCOPES:
            g_ = geoid if level == "place" else town
            if g_ in places:
                claim(sid, reach.town(g_, 1.0))
            elif has_point:
                claim(sid, reach.around(lon, lat, TOWN_MISS_KM, 1.0, False))
    for sid, level, geoid in mentions:
        if sid not in scope_of or scope_of[sid] in WIDE:
            continue
        if level == "place" and geoid in places:
            claim(sid, reach.town(geoid, MENTION))
        elif level == "county":
            claim(sid, reach.county(geoid, MENTION))

    volume = defaultdict(float)
    by_category = defaultdict(lambda: defaultdict(float))
    for sid, hexes in claims.items():
        sw = SPORTS_WEIGHT if label_of.get(sid) == "Sports" else 1.0
        for k, w in hexes.items():
            volume[k] += w * sw
            if label_of.get(sid) in CATEGORIES:
                by_category[label_of[sid]][k] += w * sw
    months = months or 1.0
    values = {k: volume.get(k, 0.0) / months for k in footprint}
    breadth = {
        k: sum(
            1
            for c in CATEGORIES
            if by_category[c].get(k, 0.0) / months >= BALANCED_FLOOR
        )
        for k in footprint
    }
    return values, breadth


def months_in(spec):
    """How many months the slice covers: whole calendar months counted as
    months, any other span in days over 30.4375."""
    try:
        start = date.fromisoformat(str(spec.get("from")))
        end = date.fromisoformat(str(spec.get("to")))
    except (TypeError, ValueError) as exc:
        raise CivicError("Choose the months on the Data step.") from exc
    if end < start:
        raise CivicError("The Data step's dates run backwards.")
    following = date.fromordinal(end.toordinal() + 1)
    if start.day == 1 and following.day == 1:
        return float((following.year - start.year) * 12 + following.month - start.month)
    return ((end - start).days + 1) / 30.4375


def stories_for(spec, scopes):
    """The slice's stories and the places they mention, from the corpus.

    A place a person contributed is used as the pipeline's own would be: a
    centre where the pipeline found none, otherwise a mention."""
    from datasets.blockplace import places_for_blocks
    from datasets.geo import centroid
    from explorer.models import ArticlePlace, ArticlePlaceManual
    from visuals.corpus import _base_queryset

    base = _base_queryset(spec, scopes).exclude(
        candidate_link__source__canonical_name__in=NOT_LOCAL
    )
    rows = {}
    for sid, scope, level, geoid, lon, lat, label in base.values_list(
        "id",
        "enrichment__scope",
        "enrichment__point_geoid_level",
        "enrichment__point_geoid",
        "enrichment__point_lon",
        "enrichment__point_lat",
        "primary_label",
    ):
        rows.setdefault(
            str(sid),
            [
                str(sid),
                scope or "",
                level,
                geoid,
                None if lon is None else float(lon),
                None if lat is None else float(lat),
                label,
                None,
            ],
        )
    mentions = [
        (str(sid), level, geoid)
        for sid, level, geoid in ArticlePlace.objects.filter(
            article_id__in=base.values("id")
        ).values_list("article_id", "geoid_level", "geoid")
        if geoid
    ]
    for sid, geoid, level, is_point in ArticlePlaceManual.objects.filter(
        article_id__in=base.values("id")
    ).values_list("article_id", "geoid", "geoid_level", "is_point"):
        row = rows.get(str(sid))
        if not row or not geoid:
            continue
        if is_point and row[2] is None:
            lat, lon = centroid(geoid)
            row[2], row[3], row[4], row[5] = level, geoid, lon, lat
        else:
            mentions.append((str(sid), level, geoid))
    blocks = places_for_blocks(r[3] for r in rows.values() if r[2] == "block" and r[3])
    for r in rows.values():
        if r[2] == "block":
            r[7] = blocks.get(r[3])
    return [tuple(r) for r in rows.values()], mentions
