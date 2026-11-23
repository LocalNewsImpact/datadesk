# Geography ICR: checking the pipeline's story geography against coders

Development proposal. What datadesk needs in order to measure, against
human coders, whether the geography the enrichment pipeline records for
a story is right, and to say with a stated confidence that it is right
often enough for the coverage maps that use it.

This is the geography counterpart of `docs/CIN_V2_DATADESK.md`. It reuses
that build's rounds, cohorts, assignments, onboarding and gate records,
and adds one new kind of question.

---

## 1. The procedure in one paragraph

Each coder reads a story (headline and text only) and records what the
story is about: one place, a county or wider area, the whole state, or
no place. They then mark that geography on a map. Every answer is turned
into Census GEOIDs using the TIGER boundaries datadesk already ships. Two
coders answer each story, and a third is added when the first two
disagree. Coders must first agree with each other (gate 1). The
pipeline's claim, saved when the story is drawn, is then scored against
the coders' consensus (gates 2 and 3).

## 2. Decided

| # | Question | Decision |
| --- | --- | --- |
| 1 | Which geography is scored | **The central claim only.** Mentions are not scored, except where they are the story's only geography (regional scope, §3). |
| 2 | What coders see | Headline and story text only: no URL, publisher, date or byline, the same as CIN V2 decision 13. Never the pipeline's answer. |
| 3 | When the coder and the pipeline answer at different levels | Compared at **the coarser of the two levels**. A pipeline county point against a coder's pin in a town in that county is a match. Differences in level are reported, and decide nothing. |
| 4 | How a pin becomes a GEOID | Point-in-polygon against the place and county TIGER boundaries in the repo. A pin's raw lat/lon is never compared with another pin's raw lat/lon to decide a match. |
| 5 | Population | The production crawler DB, `articles.status = 'enriched'` only. |
| 6 | Score used | Agreement on GEOIDs with a confidence interval. Brier is computed only where the pipeline gives a probability, which here means `scope_confidence` (§6, M9). Geography carries no confidence of its own. |

## 3. Stories with no central place

Only point scopes get a central place. `profiles.POINT_SCOPES` is
`neighborhood_community`, `city_municipality`, `elsewhere_to_local` and
`local_to_elsewhere`. `repository.py` resolves a story-level point for
those scopes and no others. The other scopes record their geography in
other ways:

| Pipeline scope | Enriched stories (prod, 2026-11-23) | Share | Story-level geography the pipeline records |
| --- | --- | --- | --- |
| Point scopes | 11,659 | 64.4% | `point_geoid` (place, county or block) and `point_lat`/`point_lon`. 254 have no point. |
| `regional` | 4,313 | 23.8% | **No point.** `geo_skip_reason = 'regional_uses_place_set'`. Its geography is the `article_geoids` county rows: `mention`, plus `county_rollup` from mentioned places. |
| `statewide` | 1,538 | 8.5% | The state code. |
| `national`, `international`, `other` | 592 | 3.3% | None (`no_codeable_geography`). |

Regional is a quarter of the corpus, and its county rows feed the
coverage maps. Leaving it out would evaluate the easy two-thirds and
report the result as if it held for all of them. Regional stories are
therefore scored, using **the set of counties the story is about** in
place of a single point:

- **Coder side.** A coder who answers "a county or a wider area" clicks
  counties on the map, drops pins on towns, or does both. Every pin rolls
  up to its county through the same crosswalk the pipeline uses
  (`fips.county_of_place`). The answer is a set of counties.
- **Pipeline side.** The story's county rows in `article_geoids`, all
  sources, saved when the story is drawn (WP5).
- **Score.** County precision (the share of the pipeline's counties that
  the coders chose) and county recall (the share of the coders' counties
  the pipeline has), pooled over stories, with a bootstrap over stories.
  An exact set match is reported beside them.

What the regional stratum holds today:

| County rows on a regional story | Stories |
| --- | --- |
| 0 | 377 |
| 1 | 1,933 |
| 2 | 1,004 |
| 3 | 487 |
| 4 | 229 |
| 5 or more | 283 |

Of the 8,368 county rows on regional stories, 4,618 (55%) are
`county_rollup`: counties the story never named, credited because a town
inside them was mentioned. Precision split by `source` is the figure
that shows whether those rows credit counties the story was actually
about, or the home towns of people in it.

Every story, whatever its class, comes down to one comparable value:
**the counties it is about.** A single place gives one county, a regional
story gives a set, a statewide story gives the state, and no place gives
an empty set. Gate 2 scores single-place stories, which also get the
finer comparison by place. Gate 3 scores the sets.

## 4. What a coder answers

| Class | Coder sees | Coder does | Resolves to | Pipeline equivalent |
| --- | --- | --- | --- | --- |
| P | One place: a town, neighborhood, address or site | Drops one pin. Accepted only at a zoom where one town fills the view. | place GEOID (if inside one), county GEOID | point scopes |
| R | A county, or an area wider than one town | Clicks counties and/or pins towns | set of county GEOIDs | `regional` |
| S | The whole state | Picks the state | state GEOID | `statewide` |
| U | Local, but the text never says where | nothing | n/a | nearest is a `publication_place_assumed` point |
| N | Not about a place: national, international, not geographic | nothing | empty | `national`, `international`, `other` |

U exists because coders cannot see the publisher. When the pipeline
cannot place a story, it falls back to the publication's own city (561
point-scope stories, `publication_place_assumed`). A coder who cannot see
the publisher has nothing to check that fallback against. Stories the
coders agree are U are reported by `point_method`, and are not counted
as pipeline errors or as pipeline successes.

Under the pin, the screen shows what the pin resolved to ("Ashland, Boone
County"), so a coder can see and correct a pin that landed in the wrong
polygon.

**Coder instructions follow the pipeline's scope rules.** The coder guide
restates `src/enrichment/prompts/geographic_scope.md` (v6) in plain
language, for example: a county agency acting in a town is about that
town; a state tournament is the venue and not the subject; a dateline
alone does not locate a story. If coders work to a different definition,
the comparison measures that difference in definition and not pipeline
error. When the prompt changes, the guide is versioned with it, as WP1
of CIN V2 versions the codebook.

## 5. Work packages

### G-WP1. A geography question on a round

`Round.question` (CIN_ROUNDS.md) takes `geography` alongside `cin`. A
geography round has the same kinds (practice, comparison), the same
cohorts and assignments, and the same finish rules. Practice rounds are
excluded from every metric by kind.

Done when: a geography practice round and a geography comparison round
can be created from the admin, and a coder's queue serves a geography
assignment with the map screen in place of the category menu.

### G-WP2. Coder screen

- Headline and the whole `text`, as the CIN queue shows them (WP1 of
  CIN V2).
- The class choice (§4), followed by the map for P and R.
- The map is drawn with the vendored d3 and topojson-client and the
  county and place boundaries that ship with the repo. It has no tiles
  and makes no calls to third parties. A place-name search box jumps the
  view to a place, using the Census gazetteer. The search is a
  navigation aid only. The answer is the pin.
- **As built, revised (2026-11-23).** The first practice round showed
  that a coder without streets cannot tell which side of a state line a
  pin is on (a Chiefs story pinned in Kansas City, Kansas), and that
  Census place labels were too dense where a county has many CDPs and
  missing in states without a place file. The map is now vendored
  Leaflet 1.9.4 over CARTO Voyager raster tiles, in two layers so the
  place names sit above the county outlines. Voyager was chosen over
  Positron for its coloured major roads. The CARTO key is the
  `carto-basemaps-key` secret, set as `CARTO_BASEMAPS_KEY`. It is locked
  to the production hostname, so it is harmless in the page. With no
  key, the map uses OpenStreetMap's standard tiles. Zooming works by the
  +/− control, the wheel or a pinch, and a double click. A single click
  waits 260 ms to tell it from a double click.
  County outlines are still the repo's TIGER files: 500k in the built
  states, 10m elsewhere. A click on one is an answer, so the outline
  has to be the county the server resolves. The search box takes a town
  or county from the gazetteer, now with a bounding box to fit to; a
  query naming "county" searches counties. On Enter it also takes a
  street address, sent through the server (`review/geocode.py`) to the
  Census geocoder (house numbers, intersections) and then OpenStreetMap
  Nominatim (bare streets, landmarks). Answers are cached for a day.
  The search still only moves the map: the pin is the answer, and the
  80 km pin rule is unchanged.
- `seconds_spent` and the zoom at each pin are recorded.

Boundaries: 500k place and county boundaries are committed for MO, PA,
MN and WA (`static/geo/places/`, `static/geo/counties/`, built by
`infra/fetch_boundaries.sh`). Elsewhere a pin still resolves to its
county, from us-atlas' 1:10m national file, and to no place.

Done when: a coder can answer each of the five classes; a P pin at
state-wide zoom is refused; the resolved place and county are shown
under every pin.

### G-WP3. `GeographyDecision`

`assignment`, `coder`, `answer_class` (P/R/S/U/N), `pins` (JSON: lat,
lon, zoom, resolved place GEOID, resolved county GEOID), `counties` (list
of county GEOIDs: clicked counties together with the counties of the pins),
`state`, `seconds_spent`, `decided_at`. One per coder per story, as for
`ClassificationDecision`.

### G-WP4. Resolution

A single function (`review/geo_resolve.py`) turns a lat/lon into (place
GEOID or none, county GEOID), using the same TIGER files. The coder's
screen asks the server for it when a pin is dropped, and the server runs
it again when the decision is saved; the saved value is the server's,
and nothing the browser sends about a pin but its position is kept. The
state's 500k counties decide the county where they are built: the 1:10m
national file is a few hundred metres out at county lines, enough to
put a town on a line in the wrong county. The pipeline's own `point_lat`/`point_lon`
is resolved by the same function at report time, so both sides go through
one lookup.

Done when: a test fixture of 30 pins with hand-checked GEOIDs, including
pins outside any incorporated place and pins close to county lines,
resolves exactly.

### G-WP5. Draw, and a snapshot of the claim

The crawler deletes and rewrites an article's geoid set on every
enrichment run (`ArticlePlaceManual`'s docstring). A claim read at report
time could therefore differ from the one the coders were scored against.
The claim is copied when the story is drawn:

**`GeographyClaim`** (`review/geography_icr.py`): `round`, `article_id`,
`pipeline_class` (P/R/S/N from the scope), `scope`, `scope_confidence`,
`profile_version`, `point_place`, `point_geoid`, `point_geoid_level`,
`point_lat`, `point_lon`, `point_method`, `point_support`,
`geo_skip_reason`, `geoid_rows` (JSON: every `article_geoids` row with
geoid, level, is_primary, source), `drawn_at`. Counties and the state
code are read from `geoid_rows`. Never edited once made.

**Draw** (`draw_geography_pilot`). From the enriched pool the practice
round uses (current profile version, at least 100 words, not wire, no
page furniture), stratified by how the pipeline placed the story, with
the inclusion probability recorded per story. A story goes to the first
stratum it fits, in this order, so the P strata do not overlap.
Populations are production on 2026-11-23, after the pool's filters:

| Stratum | Pipeline placed it | Population | Drawn |
| --- | --- | --- | --- |
| `p_county` | point at county level | 291 | 25 |
| `p_assumed` | `publication_place_assumed` | 465 | 25 |
| `p_pubcity` | `publication_city` / `single_city` | 295 | 25 |
| `p_institution` | supported only by an institution | 293 | 25 |
| `p_model` | every other point | 8,895 | 85 |
| `p_none` | point scope, no point | 174 | 10 |
| `r_0` | regional, no county rows | 313 | 15 |
| `r_1` | regional, one county | 1,727 | 80 |
| `r_2plus` | regional, two or more | 1,935 | 90 |
| `s_state` | statewide | 1,422 | 40 |
| `n_none` | national, international, other | 536 | 40 |
| **Total** | | **16,346** | **460** |

The regional 185 is split over its three strata in proportion to their
populations when the draw is made. `p_none` was not in the first version
of this table: leaving out point-scope stories the pipeline could not
place would score the pipeline only where it answered. The plan, the
seed (the round's id unless given), the populations and the profile
version are written to the round's `draw_rule`. A geography pilot takes
no batch size: its size is its plan's. Stories in another geography
batch are not drawn again; stories in CIN batches are.

Measured against production with nothing written to it: 460 drawn and
460 claims saved in 17 seconds.

Coders' queues are shuffled, so strata never come in a run. The mix is
shown to admins only.

Done when: a drawn story's claim is unchanged by a later enrichment run,
and inclusion probabilities reproduce the stratum populations.

### G-WP6. Consensus and the third coder

Two coders **agree** when they give the same class and:

- P: the same GEOID at the coarser of their two levels: the place
  where both pins are in a Census place, otherwise the county. Pins in
  Ashland and in the countryside outside it agree on Boone County; pins
  in Ashland and Columbia do not;
- R: the same county set;
- S: the same state;
- U, N: class alone.

A disagreement adds one assignment to an admitted coder who is not on
the story and has not answered it anywhere, the one with the least
outstanding work: the CIN step (CIN V2 WP5), shared
(`classification.assign_third`). A geography pilot started from the
admin asks for thirds by default; practice rounds never do.

**Consensus** (`geography_icr.consensus`): the class at least two chose.
Then, among the answers in that class: for P, the place at least two
chose, else the county at least two chose (three pins in three towns of
one county give that county, at county level); for R, the counties at
least two chose; for S, the state at least two chose. If nothing reaches
two, the story has no consensus. It is left out and counted, as stories
with no agreed label are in CIN.

`round_consensus` gives every story's consensus and the counts an admin
reads: answered twice, agreed by the first two, split, given a third,
waiting on one, with a consensus, without one. Practice rounds, check
stories and removed coders' answers are not counted.

Expected load: about 920 first-pair codings, plus third coders on about
15% of P stories and about 25% of R stories. About 1,000 codings in all.

### G-WP7. Metrics

The same conventions as CIN V2 WP7: computed on demand, practice rounds
and removed coders excluded, 95% intervals from 2,000 seeded bootstrap
resamples over stories. Corpus-wide figures are weighted by inclusion
probability.

| # | Metric | Definition | Cut by | Used for |
| --- | --- | --- | --- | --- |
| M1 | Class alpha | Krippendorff's alpha, nominal, on the five classes, over all coders on a story | stratum | gate 1 |
| M2 | Place alpha | Alpha, nominal, on the county GEOID, over stories where at least two coders answered P | stratum | gate 1 |
| M3 | Region alpha | Alpha on county sets, MASI distance, over stories where at least two coders answered R | count of counties | gate 1 |
| M4 | Coder-to-coder distance | Haversine km between pins of coders who agreed (P) | level | sets any distance margin: the 90th percentile |
| M5 | Pipeline place accuracy | Share of consensus-P stories where the pipeline's point has the same GEOID at the coarser level, pipeline class P | `point_method`, `point_support`, level, places sharing a name with a better-known place | gate 2 |
| M6 | Pipeline county precision / recall | Pooled over consensus-R stories, pipeline class R | `source` (mention / county_rollup), count of counties | gate 3 |
| M7 | Class confusion | Pipeline class × consensus class, counts and weighted shares | stratum | reported |
| M8 | Pipeline distance error | Haversine km, pipeline point to consensus pin, consensus P and pipeline P | level, `point_method` | reported |
| M9 | Scope calibration | Brier score of `scope_confidence` against "pipeline class = consensus class", with a reliability table by tenth of confidence and the Brier of a constant forecast at the observed rate | stratum | reported |
| M10 | End-to-end county match | Share of all consensus stories, every class, whose pipeline county set equals the consensus county set | stratum | reported; the figure the coverage maps depend on |
| M11 | Unplaceable | Consensus U stories by `point_method` | n/a | reported |

M9: `scope_confidence` is a confidence in the nine-way scope label. The
outcome scored here is the coarser five-way class, which is matched at
least as often as the nine-way label. The scores therefore lean toward
the pipeline looking under-confident, not over-confident. Today's mean
`scope_confidence` is 0.937.

As built (`review/geography_metrics.py` for the arithmetic, numpy for the
sums; `geography_icr.geography_report(round)` gathers the answers,
saved claims, weights and consensus and returns M1-M11 with the G-WP6
counts):

- Alpha is Krippendorff's general form, `1 - (n-1) * sum D_u / t.D.t`,
  with the nominal distance for M1 and M2 and MASI for M3; nominal alpha
  is identical to `cin_metrics.krippendorff_alpha`. Alpha is computed
  over every coder on a story, as the CIN pilot's is. Cuts are by
  stratum.
- M5 resolves the pipeline's own point through `geo_resolve`, as a pin
  is, and compares at the coarser of the two levels. "Places sharing a
  name" uses `doubted_geography.BETTER_KNOWN_ELSEWHERE` on `point_place`.
- M6 precision by source counts each county row once per story.
- M8 is the median, per story, of the distances from the pipeline's
  point to each P coder's pin; then the median and 90th percentile over
  stories.
- M10's pipeline value is the point's county for a P claim (the central
  claim only, decision 1), the claim's county rows for R, its state code
  for S, nothing for N. Consensus-U stories are left out of M9 and M10
  and counted in M11.
- On a 460-story round with two answers each (simulated from the saved
  claims of a production draw), the whole report takes under a second.

Done when: each metric has a unit test against a hand-computed fixture,
and M5 and M6 reproduce from saved claims after the crawler has
re-enriched the drawn stories.

### G-WP8. Gates

The pass/fail record is `GateResult` (CIN V2 WP8), unchanged.

| Gate | Condition | Pass |
| --- | --- | --- |
| 1. Coders agree | M1, M2 and M3 each ≥ 0.80 | all three |
| 2. Single places | M5, weighted | lower bound of the 95% interval ≥ 0.90 |
| 3. Regions | M6 precision and recall | lower bound of each ≥ 0.80 |

Gate 1 is evaluated first. If it fails, gates 2 and 3 return **not
enough data**: without a reliable human answer, the pipeline has nothing
to be scored against.

As built (`geography_icr.geography_gate`): gate 1 is judged on the
observed alpha, as the CIN pilot is; gates 2 and 3 on the lower bound.
M1-M3 are read from each story's first two coders, because a third
exists only where those two split. Until gate 1 passes, gates 2 and 3
are shown with their figures and marked "not judged". The round's
outcome is **not enough data** while any drawn story lacks its two
answers or a split waits on its third, whatever the figures; then
**fail** if any condition fails, **not enough data** if any cannot be
computed, otherwise **pass**. A recorded evaluation keeps the whole
G-WP7 report in `GateResult.measures`. A geography practice round has
the CIN practice conditions: every coder has coded every story, and the
review has been held. The thresholds are `GATE_ALPHA`, `GATE_PLACE` and
`GATE_REGION` in `review/geography_icr.py`.

**Sample size.** Gate 2 is a one-sided test of accuracy ≤ 0.90 against
an expected 0.95, at α = 0.05 and 80% power: 183 stories, so 185 P
stories are drawn. Gate 3 uses the same 185 as its planning figure. The
bootstrap interval sets its precision, and the first 60 regional
consensus stories will show whether 185 is enough.

| Must show accuracy above | Expecting | Stories |
| --- | --- | --- |
| 80% | 90% | 83 |
| 85% | 92% | 136 |
| 90% | 95% | 183 |

### G-WP9. Admin screens

- **Geography → Rounds**: the round page with gate panel, M1–M11 and
  the coder table.
- **Disagreements**: per story, every coder's pins and counties and the
  pipeline's claim on one map. Admin only. This is the screen where a
  definition problem shows up as a pattern before it shows up in alpha.
- **Coders**: shared with CIN. Onboarding, pause and remove work as in
  CIN V2 WP6.

As built: a geography round uses the CIN round page
(`/review/cin/rounds/<id>/`) for its batches, coders, standing and
practice review, with `_geography_round.html` in place of the CIN
pilot's panels: the gate table, recording an evaluation, the coding
counts, M5, M6, M10 with intervals, M5 by method, M6 precision by
source, M7, and M4, M8, M9 and M11 as readings. The coder table's
agreement is each coder's share of first pairs agreed. The
disagreements page (`/review/cin/rounds/<id>/disagreements/`, `?all=1`
for every story) lists each split with every coder's answer in words
and the saved claim, and draws the chosen story on one map: coders'
counties and pins in their own colours, the pipeline's point as a black
square and its counties outlined. In a practice round it lists every
story on which any two coders differ, and is what the review is held
over.

## 6. Build order

| Order | Packages | Unblocks |
| --- | --- | --- |
| 1 | G-WP1, G-WP2, G-WP3, G-WP4, coder guide | practice round: 20 stories, every coder |
| 2 | G-WP5, G-WP6 | comparison round |
| 3 | G-WP7, G-WP8, G-WP9 | gates |

Depends on CIN V2 WP2 (rounds), WP5 (third on split), WP6 (onboarding)
and WP8 (gate record). Geography adds no second copy of any of them.

## 7. Open

1. **Gate 3 thresholds.** 0.80 precision and recall is a starting point,
   not a measured one. If `county_rollup` rows turn out to credit home
   towns, precision will fail on those rows. The fix would then be in the
   crawler, not in the threshold.
2. **Check stories** (CIN V2 WP6) for geography coders: the first
   comparison round's three-way-agreed stories could seed them, as the
   CIN pilot seeds its own.
3. **Mentions.** Scoring the full mention set for point-scope stories is
   left out by decision 1. It would use the same set score as gate 3.
