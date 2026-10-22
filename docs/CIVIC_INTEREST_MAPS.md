# Civic interest areas, coverage density, and the swipe

Three builder kinds for the Missouri School of Journalism's civic interest area
analysis:

| Kind | Builder label | What it draws |
|---|---|---|
| `civicarea` | Civic interest area | The places a town's residents share through their travel, strengthened by news ties, as 3 km hexagons; home = 100 |
| `coveragemap` | Coverage density | The local news reaching each part of a civic interest area: colour for stories a month, hexagon size for how many of the ten kinds of civic information they cover |
| `swipemap` | Swipe compare | Two of the above on one frame, cut by a divider the reader drags |

## Where the data comes from

Neither map is computed here. The travel model lives with the Advan data,
outside datadesk, and exports each map as a **figure**: every shape already
projected into the map's W × H frame, with the values on the hexagons.

- Exporter: `flows/export_figures.py` in the travel-model workspace, which
  renders `market_pattern_print.html?figure=1` (and `&measure=coverage`) for
  every area and writes `figures/<region>/<town>-<rank>-area.json` and
  `-coverage.json`.
- Storage: uploaded to `gs://mizzou-news-maps-data/civic-interest/`. A map is a
  builder visual with a **GCS source** pointing at one figure file.
- Shape: `{figure: 1, measure, region, town, rank, W, H, title, subtitle,
  residents, share, counties[{id, d, home}], market, ring[d], hexes[{id, d, c,
  v, n?, lift?}], labels, leaders, dots, scale, shares, source}`.

Because both figures of one area share the frame, a swipe lines them up
exactly. `services.compose_swipe` refuses two maps that are not the same
region, town, rank and frame.

## The walk

Civic interest area and coverage density: **New visual → source GCS → bucket
path of the figure → Type → Look → Publish.** No data, newsrooms, places or
fields steps: the figure is finished.

Swipe: **New visual (any source) → Type: Swipe compare → Look: left and right
map slugs → Publish.** Its data is its two maps' snapshots: each side's pinned
snapshot, else its latest, frozen into the swipe's own snapshot when it is
refreshed. Republish the swipe after republishing either map.

## Options

- `civicarea`: `news_ties` -- mark where news ties add 10% or more (default),
  or travel shading only.
- `coveragemap`: `breadth` -- size hexagons by breadth (default), or stories a
  month only.
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

A figure is a drawing, not data: its lists are paths and labels, and the values
on its hexagons derive from licensed Advan travel data. `views.tables_in` and
the runtime's `tablesIn` return no tables for a payload carrying `figure` or
`swipe`, so these visuals offer no table view, CSV or Datawrapper copy.
