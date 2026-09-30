# Layered map

A map type, `layermap`, that draws what the outlet map and the story map
draw -- newsrooms as dots, coverage as county shading -- and lets the
author lay Census measures over the same counties or tracts. A reader
switches between the layers the author chose. It is modelled on the
Census Bureau's Data Mapper (maps.geo.census.gov/ddmv), which shades
counties by one ACS measure at a time, and adds what that cannot show:
where the newsrooms are, what they covered, and (to be tested) coverage
per resident.

## Two gates, not one

The reader never picks from the Census catalogue.

1. **The registry** (`visuals.census.VARIABLES`): the twenty-eight ACS
   measures a map author can choose from, under plain names, in groups
   (population, race and ethnicity, households, education, income and
   work, housing). Decided 2026-09-30.
2. **The author's layers**: from the registry, for one map, at one
   geography. These are what the reader toggles.

## Decisions (2026-09-30)

| Question | Decision |
|---|---|
| Product | ACS 5-year 2020–2024 only. The 1-year covers only counties over 65,000 people; the decennial is not needed. |
| Geography | Chosen by the map's author: county, or tract. |
| Percent or count | Chosen by the map's author, where the measure is a share. A median or a mean is mapped as it is. |
| Unreliable estimates | Hatched, never shaded: a cell whose 90% margin is more than 30% of its estimate (the Bureau's own line). The Data Mapper hides this; this map does not. |
| Tract level | Offered only for measures that hold there. Nine were unreliable in more than a third of Missouri's tracts (unemployment, Hispanic, foreign born, language, vacancy, Black alone, uninsured, poverty, veterans): county only. The flag is `tract` on each variable; `fetch_census_layers --report` re-measures it. |
| Labels | Plain names, never table codes. |
| Derived layers | Stories per 10,000 residents, outlets per 100,000: viability to be tested before any is offered. |
| How many layers | Up to eight switchable fill layers per map, chosen from the registry, plus the two base layers always available to the author: newsrooms as points, and coverage shading. The cap is one constant in the builder. |

## Where the numbers live

`census_layer_value` (`CensusValue`): one row per year, level, place and
variable, with the estimate, its margin, and the Bureau's own percent and
margin where the measure is a share. Filled by

    CENSUS_API_KEY=... python manage.py fetch_census_layers --level county
    CENSUS_API_KEY=... python manage.py fetch_census_layers --level tract

for Missouri (115 counties; 1,654 tracts), in batches under the API's
50-column limit. The key is the `census-api-key` secret in lnic-datadesk;
the `datadesk-manage` job and `dd-prod` carry it as `CENSUS_API_KEY`.
The API refuses calls without one.

A published map is built from this table, never from a call to the
Census at publish time, so it serves like every other snapshot; "keep
updated" re-reads the table at the next publish.

## What a layer is

`{id, source, variable, level, as}`: `source` is `outlets`, `coverage` or
`census`; `variable` is a registry key for a Census layer; `as` is
`percent` or `count`. The payload carries `points` (outlets), and `areas`
per layer with the value, the margin and whether the cell is unreliable.

A map carries at most eight Census fill layers (`MAX_LAYERS`), and the two
base layers. Only one fill layer shows at a time; two choropleths cannot
stack. The
points layer is always drawn. A second quantity can go on the outline or
a hatch. Each layer carries its own legend; the legend swaps with the
layer.

## Build order

1. Store, fetch command, variable registry (this document).
2. `layermap` kind: builder step, payload, renderer with the layer control
   and legends.
3. Derived layers, if they prove out, and hatching in the renderer.
4. A reader-chosen variable, served from the store by
   `/visuals/<slug>/layer.json?variable=...` -- later, if wanted.
