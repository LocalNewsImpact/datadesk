# Newspaper history: reviewer corrections

Three visuals -- the river, the county family trees and the event map -- are
built from other people's catalogues: the State Historical Society of
Missouri's county directories, the Library of Congress's links, and
newspapers.com's title list. The build is in the crawler repo
(`scripts/newspaper_history`). What a reviewer knows better than those
catalogues is recorded here, in datadesk's database, and nowhere else.

## Where each fact lives

| Fact | Table |
|---|---|
| A newsroom's founding year | `outlet_event`, a `launched` event on the outlet (year precision) |
| A merger | `outlet_event`, a `merged` event, or a `status` event that says merged |
| A correction to the catalogues | `newspaper_history_correction` |
| A broadcast station whose news we collect, its first air date and call letters | `broadcast_station`, `broadcast_call_sign` |

A correction is added in the admin (Visuals > History corrections) with its
evidence. Like an outlet event it is written once: a wrong one is withdrawn by
adding a `retraction` that names it, and both stay.

| kind | what the row says | fields |
|---|---|---|
| `set` | a catalogue field is wrong | title and/or town, county (to match); field (`town`, `county`, `start_year`, `end_year`, `held_first`, `held_last`), value |
| `drop` | an entry is the same film as another | title, county |
| `reels_belong_to` | an entry's reels are another paper's; the entry goes and its last reel moves over | title, county, town; other_title (exact spelling), other_county; field `end_year`, value |
| `moved` | one paper filmed under several towns | title; value = the towns in film order, `" > "` between |
| `link` | one title carried on as another, a renaming or merger the catalogue does not link | title, county -> other_title, other_county |
| `unlink` | a catalogue link a reviewer's chain replaces | title, county -> other_title, other_county |
| `registry_alias` | the registry's name for a catalogue title that shares no word with it | title, county; other_title, other_town = the registry's name and town |
| `not_a_newsroom` | a registry outlet not counted as a local newsroom | title = the registry outlet |
| `not_the_towns_paper` | a registry outlet that is not its town's continuing paper | title = the registry outlet |
| `print_ended` | a registry outlet's print run ended | title = the registry outlet; field `merged` or `closed`; value = the year; other_county, other_title = the catalogue entry, where the registry's name would not find it |

## Broadcast stations

Only stations whose news we collect are recorded: the registry's broadcast
outlets, not every station the FCC licenses. A station is its FCC facility id,
not its call letters, which move between stations (KMOX-TV was today's KMOV,
and before that the permit that became KPLR). Each station starts on its own
first air date, even where several publish their news through one registry
outlet (Northwest Mo Media: KMRN, KAAN-FM, KAAN, KKWK), and keeps its identity
through every change of call letters: one `broadcast_call_sign` row per span of
years, the current one with no end year.

The first air date is the day the station went on the air, from its history
(Wikipedia, citing the FCC's history cards and broadcasting yearbooks). The
FCC's first-licensed date is usually later -- a station broadcasts under a
construction permit before its license is granted -- and is not used. A date
known only to the month or the year keeps that precision.

Stations are added and edited in the admin (Visuals > Broadcast stations), or
loaded from a reviewed sheet with
`manage.py import_broadcast_stations <csv> --by <email>`, which updates a
station already recorded by its facility id.

## The build

```bash
~/.config/datadesk/dd-prod export_newspaper_history_inputs <folder>
```

writes `reviewer_corrections.csv`, `registry_founded.csv`,
`datadesk_merged_events.csv` and `broadcast_stations.csv` from these tables. The crawler's `build.py` runs
it before every build; the files are output, never edited. A correction
reaches the visuals with a rebuild, `--publish` and a re-pin -- no pull
request.
