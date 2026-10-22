# Civic interest areas, coverage density, and the swipe

Three builder kinds for the Missouri School of Journalism's civic interest area
analysis:

| Kind | Builder label | What it draws |
|---|---|---|
| `civicarea` | Civic interest area | The places a town's residents share through their travel, strengthened by news ties, as 3 km hexagons; home = 100 |
| `coveragemap` | Coverage density | The local news reaching each part of a civic interest area: colour for stories a month, hexagon size for how many of the ten kinds of civic information they cover |
| `swipemap` | Swipe compare | Two of the above on one frame, cut by a divider the reader drags |

All three are drawn from **figures** made on the server
(`visuals/civic.py`): every shape projected into the map's 354 × 338 frame,
with the values on the hexagons. The runtime draws a figure as it arrives.

## Where the data comes from

| Map | Input | Made where |
|---|---|---|
| Civic interest area | The **civic interest area table**, uploaded as its own visual (inline CSV) | The travel model, with the Advan data, outside datadesk (`flows/market_pattern_print.html?table=1`, `flows/export_area_table.py`) |
| Coverage density | The corpus: the visual's datasets and months (Data step) | Here, live (`civic.coverage_values`) |
| Both | `visuals/data/civic_grid_29.json`: public Census geography | `flows/export_civic_grid.py` in the travel-model workspace |

### The civic interest area table

One row per hexagon of each area:

| Column | Meaning |
|---|---|
| `region` | The TV market the area was modelled in (`columbia`, `springfield`, ...) |
| `town`, `area` | The town and its area number (1, 2, 3) |
| `residents`, `share` | Residents travelling in this pattern, and their share of the town |
| `hex` | The hexagon id, axial `q,r` on the grid below |
| `lon`, `lat` | The hexagon's centre |
| `travel` | How local the place is: travel strengthened by news ties, home = 100 (0 for an unshaded hexagon) |
| `lift` | What news ties add, as a share (0 below 10%) |
| `shown` | 1 = shaded; 0 = inside the area's outline, drawn empty |
| `home` | 1 = a home hexagon (100) |

### The grid file

Public Census data only, so it ships in the repository:

- `hexes`: every Missouri hexagon (`q,r`) with its county, the 2020 place
  holding its centre, and its 2020 population (each block group's count over
  the hexagons whose centres lie in it).
- `places`: every Missouri place with its name, centre, 2024 population
  estimate, and the hexagons a story about it reaches (the place and 3 km
  around it).
- `counties`: Missouri and the counties of the eight bordering states within
  1.5° of it (2020, 1:500k, simplified to 0.003°). A map at a state line
  shows the neighbouring state's county borders for context.
- `markets`: each region's TV-market outline; `landmarks`: names for an
  unlabelled cluster.

The grid: 3 km hexagons, 1.5 km circumradius, pointy-top axial (q, r) on a
plane x = lon × 111.32 × cos 38.7°, y = lat × 110.57 (km).

## The walk

1. **Upload the table:** New visual → source Upload → the CSV → Type: Table.
   Leave it unpublished: it is the input, not a page.
2. **Civic interest area:** New visual (any source) → Type: Civic interest
   area → Look: the table's slug, the town, the area → Publish.
3. **Coverage density:** New visual → source Corpus → Type: Coverage density
   → Look: the area map's slug → Data: datasets and months → Publish.
4. **Swipe:** New visual (any source) → Type: Swipe compare → Look: left and
   right map slugs → Publish.

Each map reads the version of what it names that readers see: its pinned
snapshot, else its latest. Republish downstream after republishing upstream
(table → area → coverage → swipe).

## The frame and labels

- **Frame:** a fixed box 2.3° of longitude by 1.64° of latitude (about
  190 × 180 km), centred on the travel-weighted centre of the area's shaded
  hexagons of 10+, shifted only as far as keeps the whole home county in view
  (0.08° margin). Projection: conic equal-area, parallels 36.5° and 40.5°,
  rotated to 92.4° W, fitted to the frame with a 4 px margin. A coverage map
  uses its area map's frame, so a swipe lines the two up exactly; the swipe
  refuses two maps whose frames differ.
- **Labels:** the home town bold; the 8 shaded towns of 2,500+ ranked by how
  local × √population; up to 4 unshaded towns of 20,000+ in grey; a cluster of
  5+ shaded hexagons with no name in it is named by a landmark in it or its
  largest town. Never on a dark hexagon (20+), except the home town wholly on
  its own (40+), in the surface colour.

## Coverage density

Stories in the slice, local outlets only: student, campus and statewide
outlets (`civic.NOT_LOCAL`) are left out. Each story stands for an area set by
its geographic scope, centred where the enrichment placed it:

| Scope | Area |
|---|---|
| `neighborhood_community` | 3 km around the point |
| `city_municipality`, `elsewhere_to_local`, `local_to_elsewhere`, none | The town (its 2020 place) and 3 km around it; a point in no town, 5 km |
| `regional` | 40 km around the point |
| `statewide`, `national`, `international`, `other` | None; their mentions are not counted either |
| A point only at county level | The county |

- **Weights:** central focus 1; each town or county it mentions 0.5. Sports
  counts half.
- **Larger areas count less in each hexagon:** weight × min(1, (7 / hexagons)
  ^ α). Towns and neighbourhoods α = 0.5; counties and regions α = 1 (counted
  once), spread by where people live (the hexagon's share of the area's
  population). A hexagon keeps a story's strongest claim.
- **Per month:** whole calendar months count as months; any other span is
  days ÷ 30.4375.
- **Tiers:** frequent 9+ a month, regular 2–8, sporadic under 2, none.
- **Breadth:** how many of the ten CIN categories reach 1 story a month in the
  hexagon; a balanced diet is any 8.
- A place a person contributed (`article_places_manual`) counts as the
  pipeline's would: a centre where the pipeline found none, else a mention.

### Agreement with the analysis

The rules are `analysis/area_coverage_alignment.py` and
`analysis/cin_diet.py`, ported. Two inputs differ by construction:

- A hexagon's county is its block group's. The analysis used its travel
  units' county, which differs for 74 hexagons along county lines; the
  3,334 hexagons with no travel unit had no county there and have one here.
- A block-level point's town comes from the Census block–place relationship
  (`datasets.blockplace`), not a polygon test.

Checked 2026-10-22, March 2026 computed live from production against the
analysis's numbers for all 12 areas: correlation 1.000 in each; the same tier
in 97–100% of each area's hexagons; the same balanced-diet call in 99–100%;
volume 0–4% higher, the corpus having grown since the analysis's export
(15,150 stories against 12,545).

## Options

- `civicarea`: `civic_table`, `civic_town`, `civic_area` (1, 2, 3);
  `news_ties` -- mark where news ties add 10% or more (default), or travel
  shading only.
- `coveragemap`: `civic_map`; `breadth` -- size hexagons by breadth (default),
  or stories a month only.
- `swipemap`: `swipe_left`, `swipe_right` -- the slugs of the two maps.

## Scales

- Area: bands at the figure's `scale` (floor 5; cuts 10, 20, 40, 80), the
  theme's sequential ramp.
- Coverage: stories a month on a log scale capped at the frequent tier (9+), so
  a city's hundreds read the same as a town's nine; this map's own green ramp.
  Hexagon size 0.4 + 0.6 × min(1, categories met / 8).
- News-tie dots: this map's own warm colour, sized 0.3 / 0.5 / 0.7 of the
  hexagon for lifts of 10% / 25% / 50%+.

## Downloads

A figure is a drawing, not data: its lists are paths and labels, and the
area map's values derive from licensed Advan travel data. `views.tables_in`
and the runtime's `tablesIn` return no tables for a payload carrying `figure`
or `swipe`, so these visuals offer no table view, CSV or Datawrapper copy.
The uploaded table is an ordinary table visual: keep it unpublished.
