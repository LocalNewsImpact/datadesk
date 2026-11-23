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

**`GeographyClaim`**: `story`, `round`, `scope`, `scope_confidence`,
`profile_version`, `point_geoid`, `point_geoid_level`, `point_lat`,
`point_lon`, `point_method`, `point_support`, `county_rows` (JSON:
geoid, source), `state_geoid`, `drawn_at`.

**Draw.** Drawn only from stories enriched under the current profile
version (3: 18,084 of 18,102 enriched today), stratified by the
pipeline's class, with the inclusion probability recorded per story as
`draw_cohort` already does:

| Stratum | Population | Drawn | Split |
| --- | --- | --- | --- |
| P: `focus_model`, named | 9,823 | 85 | random |
| P: supported only by an institution | 395 | 25 | random |
| P: `publication_place_assumed` | 561 | 25 | random |
| P: `publication_city` / `single_city` | 336 | 25 | random |
| P: county-level point | 320 | 25 | random |
| R | 4,313 | 185 | by county-row count: 0 / 1 / 2+ in proportion |
| S | 1,538 | 40 | random |
| N | 592 | 40 | random |
| **Total** | | **450** | |

The P strata overlap (an institution-supported point can also be a
`single_city` point), so each story goes to the first stratum it fits,
in this order: county-level, `publication_place_assumed`,
`publication_city` / `single_city`, institution, `focus_model` named. The
populations in the table are counted before that rule is applied and
will shrink slightly after it.

Coders' queues are shuffled, so strata never come in a run. The mix is
shown to admins only.

Done when: a drawn story's claim is unchanged by a later enrichment run,
and inclusion probabilities reproduce the stratum populations.

### G-WP6. Consensus and the third coder

Two coders **agree** when they give the same class and:

- P: the same GEOID at the coarser of their two levels (place if both
  pins are in one place, otherwise county);
- R: the same county set;
- S: the same state;
- U, N: class alone.

A disagreement adds one assignment to an admitted coder not already on
the story (CIN V2 WP5). **Consensus**: the class at least two of three
chose. For P, the GEOID at least two chose. For R, the counties at least
two chose. If there is no majority, the story has no consensus. It is
left out and counted, as stories with no agreed label are in CIN.

Expected load: about 900 first-pair codings, plus third coders on about
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
