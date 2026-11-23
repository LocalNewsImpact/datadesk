"""Coders place a story; the pipeline's placement is scored against them.

The geography counterpart of the CIN rounds, and built on them: a round,
its batches (cohorts), assignments and coder standing are the same
tables. What is new is the question -- where is this story about? --
and the answer, which is a class and some geography rather than a
category. See docs/GEOGRAPHY_ICR_DATADESK.md (G-WP1 to G-WP4 here).

**The answer is a class first.** Only some of the pipeline's scopes get
a central place. A regional story has none: its geography is the set of
counties in `article_geoids`. So a coder says what KIND of geography
the story has, and then marks it:

    P  one place       one pin
    R  county or wider counties clicked, and/or towns pinned
    S  the whole state the state
    U  local, unstated nothing -- the text never says where
    N  not about a place nothing

**A pin is never compared as a coordinate.** It is resolved to a place
and a county (`review.geo_resolve`) and the GEOIDs are what any later
comparison reads. The browser resolves it too, for the coder to see;
the server resolves it again on save and keeps its own answer.

**The coder sees what a CIN coder sees**: headline and story text. No
publisher, URL, date or byline, and never the pipeline's placement. The
publication's own city is what the pipeline falls back to when it
cannot place a story, and a coder who can see the publisher cannot
check that fallback.
"""

from __future__ import annotations

import json
import math

from django.conf import settings
from django.db import models

from review import geo_resolve

GEOGRAPHY = "geography"
CIN = "cin"
TASKS = [(CIN, "CIN categories"), (GEOGRAPHY, "Story geography")]

PLACE = "P"
REGION = "R"
STATE = "S"
UNSTATED = "U"
NO_PLACE = "N"

#: (class, what the coder is shown, the longer explanation). The order
#: is the order on screen.
CLASSES = [
    (
        PLACE,
        "One place",
        "A town, a neighbourhood, an address or a site. Drop one pin on it.",
    ),
    (
        REGION,
        "A county, or an area wider than one town",
        "A county's own business, or several towns or counties with no "
        "single one at the centre. Click the counties, or pin the towns, "
        "or both.",
    ),
    (
        STATE,
        "The whole state",
        "State law, the governor, a state agency acting across the state.",
    ),
    (
        UNSTATED,
        "Local, but the text never says where",
        "The story is plainly about one community and never names it.",
    ),
    (
        NO_PLACE,
        "Not about a place",
        "National or international news, or a story with no geography: a "
        "column about the newsroom, a recipe.",
    ),
]
CLASS_CHOICES = [(code, shown) for code, shown, _ in CLASSES]

#: A pin is accepted only when the map is zoomed in far enough that one
#: town fills the view: the visible width, in kilometres. A pin dropped
#: on a whole-state view lands wherever the cursor happened to be, and
#: resolves to a town nobody chose.
PIN_VIEW_KM = 80

#: More than this and the answer is "the whole state" or a mistake.
MOST_COUNTIES = 40
MOST_PINS = 30

#: The coder's guide, from the pipeline's own scope rules
#: (MizzouNewsCrawler src/enrichment/prompts/geographic_scope.md, v6) in
#: plain language. If coders answer to a different definition, the
#: comparison measures the definitions and not the pipeline.
GUIDE = """\
Say where the story is ABOUT: where its events happen, and whose
community, government or institution it concerns. Not where its effects
reach, and not every place it mentions.

Choose the smallest area that holds the subject. A story set in one
town is about that town, even when it names the county, a highway or
nearby towns.

A county agency acting in a town is about that town: deputies arresting
somebody in Fulton is a Fulton story. A school, library, hospital or fire
district's story about one of its buildings is about the town that
building is in; it is wider only when it is about the whole district (a
district-wide levy, budget or election).

A state event is the venue, not the subject. A team from one town at a
state tournament is a story about that town. A name with the state in it
is still in one place: the University of Missouri and its teams are in
Columbia; the Missouri State Fair is in Sedalia.

A state official acting on named places is about those places. It is
the whole state only when the action applies across it: a law, a rule,
a statewide program.

A county, or wider, is for a county government's own business, a story
set in a county but in no particular town, or two or more communities
sharing the story with no single one at its centre. Where the people in
a story are from is not a second place: a crash near Centerville whose
driver lives in Ellington is a Centerville story.

A dateline alone does not locate a story. "COLUMBIA, Mo. —" says where
the reporter filed from; read the story for where it happened.

Zoom in until the town fills the map before you drop a pin. The name
under the pin is what will be recorded: if it is not the place you
meant, move the pin.
"""


def guide_paragraphs(text=GUIDE):
    blocks = [b.strip() for b in text.split("\n\n")]
    return [" ".join(b.split()) for b in blocks if b.strip()]


def geography_guide():
    """The geography guide a new round is coded under.

    A `CodebookVersion` with task "geography", so coder standing (who
    has done the practice and been admitted) works exactly as it does
    for CIN, and a geography admission never admits anybody to CIN
    coding or the reverse. Created as GEO-1 on first use.
    """
    from review.classification import CodebookVersion

    row = CodebookVersion.objects.filter(task=GEOGRAPHY).first()
    if row is None:
        row = CodebookVersion.objects.create(
            name="GEO-1",
            task=GEOGRAPHY,
            instructions=GUIDE,
            guidance={code: [shown, about] for code, shown, about in CLASSES},
            display_names={},
            rejections=[],
        )
    return row


class GeographyDecision(models.Model):
    """What one coder said about where one story is.

    Beside `ClassificationDecision`, not in it: a coder can be asked
    both questions of one story, and the CIN table allows one answer per
    coder per story.

    `pins` keep what the coder did (each with the view width it was
    dropped at, and what it resolved to); `counties` and `state` are the
    answer every comparison reads. For a P answer `counties` is the
    pin's county; for R, the clicked counties together with the pins'.
    """

    CLASSES = CLASS_CHOICES

    cohort = models.ForeignKey(
        "ClassificationCohort",
        on_delete=models.PROTECT,
        related_name="geography_decisions",
    )
    article_id = models.CharField(max_length=64, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    answer_class = models.CharField(max_length=1, choices=CLASS_CHOICES)
    #: [{lat, lon, view_km, state, county, county_name, place, place_name}]
    #: as the server resolved them.
    pins = models.JSONField(default=list, blank=True)
    #: County GEOIDs, sorted, no repeats.
    counties = models.JSONField(default=list, blank=True)
    #: Two-digit state FIPS: the state chosen for S, otherwise the one
    #: state every pin and county is in, or empty.
    state = models.CharField(max_length=2, blank=True, default="")
    codebook_version = models.ForeignKey(
        "CodebookVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="geography_decisions",
    )
    #: Copied from the sample, as on a CIN decision.
    stratum = models.CharField(max_length=20, blank=True, default="")
    inclusion_probability = models.FloatField(default=1.0)
    dataset_id = models.CharField(max_length=64, blank=True, default="")
    is_check = models.BooleanField(default=False)
    seconds_spent = models.PositiveIntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-decided_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["article_id", "decided_by"],
                name="one_geography_answer_per_coder_per_story",
            )
        ]
        indexes = [models.Index(fields=["cohort", "article_id"])]

    def __str__(self):
        return f"{self.decided_by} said {self.answer_class} of {self.article_id}"

    @property
    def place(self):
        """The P answer's place GEOID, or None."""
        if self.answer_class != PLACE or not self.pins:
            return None
        return self.pins[0].get("place")


# ------------------------------------------------------------ the answer


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _states():
    from visuals.geofocus import _counties

    return {fips[:2] for fips, _ in _counties()}


def _county_ids():
    from visuals.geofocus import _counties

    return {fips for fips, _ in _counties()}


def clean_answer(answer_class, pins=None, counties=None, state=""):
    """A coder's answer checked and resolved, or ValueError saying what
    is wrong with it in words a coder can act on.

    `pins` and `counties` arrive as JSON text from the form (or as
    lists). Returns {answer_class, pins, counties, state}.
    """
    if answer_class not in dict(CLASS_CHOICES):
        raise ValueError("Say what kind of place the story is about.")
    pins = _loads(pins)
    counties = _loads(counties)
    state = (state or "").strip()

    if answer_class in (UNSTATED, NO_PLACE):
        return {"answer_class": answer_class, "pins": [], "counties": [], "state": ""}

    if answer_class == STATE:
        if state not in _states():
            raise ValueError("Choose the state.")
        return {"answer_class": STATE, "pins": [], "counties": [], "state": state}

    if len(pins) > MOST_PINS:
        raise ValueError(f"At most {MOST_PINS} pins.")
    resolved = []
    for pin in pins:
        lat = _number((pin or {}).get("lat"))
        lon = _number((pin or {}).get("lon"))
        view = _number((pin or {}).get("view_km"))
        if lat is None or lon is None:
            raise ValueError("A pin arrived without a position. Drop it again.")
        if view is None or view > PIN_VIEW_KM:
            raise ValueError(
                "Zoom in until the town fills the map, then drop the pin again."
            )
        where = geo_resolve.resolve(lat, lon)
        if where.county is None:
            raise ValueError("That pin is not in a U.S. county. Move it.")
        resolved.append(
            {"lat": round(lat, 6), "lon": round(lon, 6), "view_km": round(view, 1)}
            | where.as_dict()
        )

    if answer_class == PLACE:
        if len(resolved) != 1:
            raise ValueError("One place is one pin. Drop exactly one.")
        if counties:
            raise ValueError("One place is a pin, not counties.")
        pin = resolved[0]
        return {
            "answer_class": PLACE,
            "pins": resolved,
            "counties": [pin["county"]],
            "state": pin["state"],
        }

    known = _county_ids()
    clicked = [str(c) for c in counties]
    if any(c not in known for c in clicked):
        raise ValueError("A county on the answer is not one the map knows.")
    every = sorted(set(clicked) | {pin["county"] for pin in resolved})
    if not every:
        raise ValueError("Click the counties, or pin the towns, the story is about.")
    if len(every) > MOST_COUNTIES:
        raise ValueError(
            f"More than {MOST_COUNTIES} counties. If it is the whole state, say so."
        )
    states = {c[:2] for c in every}
    return {
        "answer_class": REGION,
        "pins": resolved,
        "counties": every,
        "state": states.pop() if len(states) == 1 else "",
    }


def _loads(value):
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ValueError("The map's answer could not be read. Try again.") from error
    if not isinstance(parsed, list):
        raise ValueError("The map's answer could not be read. Try again.")
    return parsed


# -------------------------------------------------------------- the draw


def is_geography(cohort_or_round):
    """True for a geography round, or a batch of one."""
    if cohort_or_round is None:
        return False
    target = getattr(cohort_or_round, "round", cohort_or_round)
    return getattr(target, "task", CIN) == GEOGRAPHY


def current_profile_version():
    """The enrichment profile the newest stories were placed under."""
    from explorer.models import ArticleEnrichment

    return ArticleEnrichment.objects.using("crawler").aggregate(
        models.Max("profile_version")
    )["profile_version__max"]


def enriched_pool(_stratum=None):
    """Stories whose geography can be scored: enriched, under the current
    profile, long enough to read, and not wire.

    Production, `status = 'enriched'`, and nothing else: a story in any
    other status has no placement to score, or had one under rules that
    no longer run. Takes a stratum for `draw_cohort`'s signature; the
    practice draw has one.
    """
    from explorer.models import Article
    from review.classification import MIN_WORDS, _furniture

    rows = (
        Article.objects.using("crawler")
        .filter(status="enriched")
        .exclude(wire_check_status="wire")
        .exclude(_furniture())
        .extra(
            where=[
                "(wire IS NULL OR wire::text IN ('null', '[]', '{}'))",
                "array_length(regexp_split_to_array("
                "btrim(coalesce(articles.text, ''),"
                " ' ' || chr(9) || chr(10) || chr(13)),"
                " '\\s+'), 1) >= %s",
            ],
            params=[MIN_WORDS],
        )
    )
    version = current_profile_version()
    if version is not None:
        rows = rows.filter(enrichment__profile_version=version)
    return rows


def open_geography_batch(for_round, size):
    """A geography round's stories: (cohort or None, level, message).

    A practice round draws at random from the enriched pool. A pilot's
    stratified draw, with the pipeline's claim saved beside each story,
    is G-WP5 and is not built: until it is, a geography pilot opens with
    no stories rather than with a draw the procedure does not describe.
    """
    from review.classification import ClassificationSample, assign_cohort, draw_cohort

    if not for_round.is_practice:
        return (
            None,
            "warn",
            "The round is open. Its stratified draw, which saves the "
            "pipeline's placement beside each story, is not built yet "
            "(G-WP5), so no stories were drawn.",
        )
    cohort, drawn = draw_cohort(
        size,
        for_round=for_round,
        shares={ClassificationSample.RANDOM: 1.0},
        pool=enriched_pool,
    )
    made = assign_cohort(cohort)
    total = sum(drawn.values())
    return (
        cohort,
        "good" if total == size else "warn",
        f"{total} of {size} enriched stories drawn, {made} assignments made.",
    )


# ------------------------------------------------------- the coder's map


def map_start(dataset_id):
    """The state the map opens on: the dataset's, as two-digit FIPS.

    The dataset's state, not the publisher's town. The map has to open
    somewhere, and a Missouri dataset's stories are mostly in Missouri;
    that says nothing a coder could use to guess which outlet ran it.
    """
    from explorer.models import Dataset
    from visuals.geofocus import state_fips

    postal = None
    if dataset_id:
        row = (
            Dataset.objects.using("crawler")
            .filter(id=dataset_id)
            .values_list("meta", flat=True)
            .first()
        )
        if isinstance(row, dict):
            postal = row.get("default_state")
    return state_fips(postal or "MO") or "29"


def state_choices():
    """(FIPS, name) for every state, for the S answer."""
    path = geo_resolve.GEO_DIR / "states-10m.json"
    with open(path, encoding="utf-8") as handle:
        topology = json.load(handle)
    rows = [
        (str(g.get("id")).zfill(2), (g.get("properties") or {}).get("name", ""))
        for g in topology["objects"]["states"]["geometries"]
    ]
    return sorted(rows, key=lambda row: row[1])
