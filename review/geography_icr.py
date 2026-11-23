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


def enriched_pool(_stratum=None, words=True):
    """Stories whose geography can be scored: enriched, under the current
    profile, long enough to read, and not wire.

    Production, `status = 'enriched'`, and nothing else: a story in any
    other status has no placement to score, or had one under rules that
    no longer run. Takes a stratum for `draw_cohort`'s signature; the
    practice draw has one. With `words` False, the same less the word
    count (`classification._pick`).
    """
    from explorer.models import Article
    from review.classification import _furniture, long_enough

    rows = (
        Article.objects.using("crawler")
        .filter(status="enriched")
        .exclude(wire_check_status="wire")
        .exclude(_furniture())
        .extra(where=["(wire IS NULL OR wire::text IN ('null', '[]', '{}'))"])
    )
    if words:
        rows = long_enough(rows)
    version = current_profile_version()
    if version is not None:
        rows = rows.filter(enrichment__profile_version=version)
    return rows


def open_geography_batch(for_round, size):
    """A geography round's stories: (cohort or None, level, message).

    A practice round draws `size` at random from the enriched pool. A
    pilot is the stratified draw (`draw_geography_pilot`), whose size is
    its plan's, with the pipeline's claim saved beside every story.
    """
    from review.classification import ClassificationSample, assign_cohort, draw_cohort

    if not for_round.is_practice:
        cohort, drawn = draw_geography_pilot(for_round)
        if cohort is None:
            return None, "bad", "No enriched stories to draw from."
        made = assign_cohort(cohort)
        total = sum(row["drawn"] for row in drawn)
        short = [row["stratum"] for row in drawn if row["drawn"] < row["wanted"]]
        return (
            cohort,
            "warn" if short else "good",
            f"{total} stories drawn in {len(drawn)} strata, {made} assignments "
            "made; the pipeline's placement of each is saved."
            + (f" Short: {', '.join(short)}." if short else ""),
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


# --------------------------------------------------- the pilot (G-WP5)
#
# The pipeline's answer is copied when a story is drawn. The crawler
# DELETEs and rewrites an article's geoid set on every enrichment run
# (ArticlePlaceManual's docstring), so a claim read at report time could
# be a different claim from the one the coders were scored against.

#: The crawler's point scopes (MizzouNewsCrawler enrichment/profiles.py
#: POINT_SCOPES): the only scopes that get a story-level point.
POINT_SCOPES = frozenset(
    {
        "neighborhood_community",
        "city_municipality",
        "elsewhere_to_local",
        "local_to_elsewhere",
    }
)
NO_PLACE_SCOPES = frozenset({"national", "international", "other"})

#: The pilot's strata, in the order a story is tested against them: a
#: story goes to the FIRST it fits, so the P strata do not overlap (an
#: institution-supported point can also be a `single_city` one). The
#: doubtful kinds of point come first and are oversampled; `p_model` is
#: everything else the model placed.
P_COUNTY = "p_county"
P_ASSUMED = "p_assumed"
P_PUBCITY = "p_pubcity"
P_INSTITUTION = "p_institution"
P_MODEL = "p_model"
P_NONE = "p_none"
R_NONE = "r_0"
R_ONE = "r_1"
R_MANY = "r_2plus"
S_STATE = "s_state"
N_NONE = "n_none"

#: Stories drawn per stratum. The regional 185 is split over its three
#: strata in proportion to their populations when the draw is made.
PILOT_PLAN = {
    P_COUNTY: 25,
    P_ASSUMED: 25,
    P_PUBCITY: 25,
    P_INSTITUTION: 25,
    P_MODEL: 85,
    P_NONE: 10,
    "regional": 185,
    S_STATE: 40,
    N_NONE: 40,
}
REGIONAL_STRATA = (R_NONE, R_ONE, R_MANY)
GEOGRAPHY_STRATA = (
    P_COUNTY,
    P_ASSUMED,
    P_PUBCITY,
    P_INSTITUTION,
    P_MODEL,
    P_NONE,
    R_NONE,
    R_ONE,
    R_MANY,
    S_STATE,
    N_NONE,
)


def pipeline_class(scope):
    """The answer class the pipeline's scope corresponds to, or None
    for a scope that is none of them (unset, or a value from a profile
    that no longer runs)."""
    if scope in POINT_SCOPES:
        return PLACE
    if scope == "regional":
        return REGION
    if scope == "statewide":
        return STATE
    if scope in NO_PLACE_SCOPES:
        return NO_PLACE
    return None


def stratum_of(scope, point_geoid, point_level, point_method, point_support, counties):
    """Which pilot stratum a story falls in, or None.

    `counties` is how many distinct county rows the story has in
    `article_geoids`.
    """
    kind = pipeline_class(scope)
    if kind == PLACE:
        if not point_geoid:
            return P_NONE
        if point_level == "county":
            return P_COUNTY
        if point_method == "publication_place_assumed":
            return P_ASSUMED
        if point_method in ("publication_city", "single_city"):
            return P_PUBCITY
        if point_support == "institution":
            return P_INSTITUTION
        return P_MODEL
    if kind == REGION:
        return R_NONE if counties == 0 else R_ONE if counties == 1 else R_MANY
    if kind == STATE:
        return S_STATE
    if kind == NO_PLACE:
        return N_NONE
    return None


def _populations(taken):
    """stratum -> [(article_id, dataset_id)] over the enriched pool."""
    from explorer.models import ArticleGeoid

    rows = list(
        enriched_pool()
        .exclude(id__in=taken)
        .values_list(
            "id",
            "dataset_id",
            "enrichment__scope",
            "enrichment__point_geoid",
            "enrichment__point_geoid_level",
            "enrichment__point_method",
            "enrichment__point_support",
        )
    )
    regional = [row[0] for row in rows if row[2] == "regional"]
    counts = {}
    for start in range(0, len(regional), 2000):
        for article_id, n in (
            ArticleGeoid.objects.using("crawler")
            .filter(article_id__in=regional[start : start + 2000], geoid_level="county")
            .values("article_id")
            .annotate(n=models.Count("geoid", distinct=True))
            .values_list("article_id", "n")
        ):
            counts[article_id] = n
    out = {stratum: [] for stratum in GEOGRAPHY_STRATA}
    for article_id, dataset_id, *claim in rows:
        stratum = stratum_of(*claim, counts.get(article_id, 0))
        if stratum is not None:
            out[stratum].append((article_id, dataset_id or ""))
    return out


def _wanted(plan, populations):
    """stratum -> stories to draw, the regional total split in proportion
    to the three regional populations (largest remainder, so the parts
    sum to the whole)."""
    wanted = {s: n for s, n in plan.items() if s in GEOGRAPHY_STRATA}
    total = plan.get("regional", 0)
    sizes = {s: len(populations.get(s, ())) for s in REGIONAL_STRATA}
    whole = sum(sizes.values())
    if total and whole:
        exact = {s: total * sizes[s] / whole for s in REGIONAL_STRATA}
        parts = {s: int(exact[s]) for s in REGIONAL_STRATA}
        left = total - sum(parts.values())
        for s in sorted(REGIONAL_STRATA, key=lambda s: exact[s] - parts[s])[::-1][
            :left
        ]:
            parts[s] += 1
        wanted.update(parts)
    return wanted


def draw_geography_pilot(for_round, plan=None, seed=None):
    """Draw a geography pilot and save the pipeline's claim on each story.

    Returns (cohort or None, [{stratum, population, wanted, drawn,
    inclusion_probability}]). The draw -- plan, seed, populations and the
    profile version -- is written to `for_round.draw_rule`, so the round
    says how its stories were chosen.

    Stories already in another geography batch are not drawn again;
    stories in CIN batches are (the two questions are independent).
    """
    import random

    from django.db import transaction

    from review.classification import (
        ClassificationCohort,
        ClassificationSample,
        _one_batch_at_a_time,
        admit_waiting_coders,
        grant_cohort,
        round_coders,
        same_task,
    )

    plan = plan or PILOT_PLAN
    seed = for_round.pk if seed is None else seed
    taken = set(
        same_task(
            ClassificationSample.objects.all(), GEOGRAPHY, "cohort__round"
        ).values_list("article_id", flat=True)
    )
    populations = _populations(taken)
    if not any(populations.values()):
        return None, []
    wanted = _wanted(plan, populations)
    rng = random.Random(seed)
    report, chosen = [], []
    for stratum in GEOGRAPHY_STRATA:
        pool = sorted(populations[stratum])
        want = wanted.get(stratum, 0)
        picked = rng.sample(pool, min(want, len(pool)))
        probability = (len(picked) / len(pool)) if pool else 1.0
        report.append(
            {
                "stratum": stratum,
                "population": len(pool),
                "wanted": want,
                "drawn": len(picked),
                "inclusion_probability": probability,
            }
        )
        chosen += [(stratum, a, d, probability) for a, d in picked]

    with transaction.atomic():
        _one_batch_at_a_time()
        number = (
            ClassificationCohort.objects.aggregate(models.Max("number"))["number__max"]
            or 0
        ) + 1
        cohort = ClassificationCohort.objects.create(
            number=number,
            slug=f"cohort-{number}",
            target_coders=for_round.base_coders,
            codebook_version=for_round.codebook_version,
            round=for_round,
            note="geography pilot",
        )
        ClassificationSample.objects.bulk_create(
            [
                ClassificationSample(
                    cohort=cohort,
                    article_id=article_id,
                    stratum=stratum,
                    inclusion_probability=probability,
                    dataset_id=dataset_id,
                )
                for stratum, article_id, dataset_id, probability in chosen
            ],
            batch_size=500,
        )
        save_claims(for_round, [article_id for _, article_id, _, _ in chosen])
        for_round.draw_rule = {
            "plan": plan,
            "seed": seed,
            "profile_version": current_profile_version(),
            "strata": report,
        }
        for_round.save(update_fields=["draw_rule"])
    for user in round_coders(for_round):
        grant_cohort(user, cohort)
    admit_waiting_coders(for_round, cohort)
    return cohort, report


class GeographyClaim(models.Model):
    """The pipeline's placement of one story, as it stood when drawn.

    Everything later scoring reads about the pipeline's answer comes
    from here and never from the crawler's live tables, which the next
    enrichment run rewrites. Never changed after it is made.
    """

    round = models.ForeignKey(
        "Round", on_delete=models.PROTECT, related_name="geography_claims"
    )
    article_id = models.CharField(max_length=64)
    #: The answer class the pipeline's scope corresponds to (P/R/S/N),
    #: or empty for a scope that is none of them.
    pipeline_class = models.CharField(max_length=1, blank=True, default="")
    scope = models.CharField(max_length=40, blank=True, default="")
    scope_confidence = models.FloatField(null=True, blank=True)
    profile_version = models.IntegerField(null=True, blank=True)
    point_place = models.TextField(blank=True, default="")
    point_geoid = models.CharField(max_length=20, blank=True, default="")
    point_geoid_level = models.CharField(max_length=10, blank=True, default="")
    point_lat = models.FloatField(null=True, blank=True)
    point_lon = models.FloatField(null=True, blank=True)
    point_method = models.CharField(max_length=40, blank=True, default="")
    point_support = models.CharField(max_length=20, blank=True, default="")
    geo_skip_reason = models.CharField(max_length=60, blank=True, default="")
    #: Every `article_geoids` row: [{geoid, level, is_primary, source}].
    geoid_rows = models.JSONField(default=list, blank=True)
    drawn_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["round", "article_id"], name="one_claim_per_story_per_round"
            )
        ]

    def __str__(self):
        return f"{self.article_id}: {self.pipeline_class or '?'} {self.point_geoid}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("a claim is a record of what was drawn; never edited")
        super().save(*args, **kwargs)

    @property
    def counties(self):
        """The county GEOIDs in the story's geoid set, sorted."""
        return sorted(
            {row["geoid"] for row in self.geoid_rows if row.get("level") == "county"}
        )

    @property
    def state_geoid(self):
        """The state code a statewide story carries, or empty."""
        if self.point_geoid_level == "state":
            return self.point_geoid
        rows = [r["geoid"] for r in self.geoid_rows if r.get("level") == "state"]
        return rows[0] if len(rows) == 1 else ""


def save_claims(for_round, article_ids):
    """Copy the pipeline's placement of each story into `GeographyClaim`.
    Returns how many were made."""
    from explorer.models import ArticleEnrichment, ArticleGeoid

    ids = list(article_ids)
    enrichment = {
        row.article_id: row
        for row in ArticleEnrichment.objects.using("crawler").filter(article_id__in=ids)
    }
    rows = {}
    for row in (
        ArticleGeoid.objects.using("crawler")
        .filter(article_id__in=ids)
        .order_by("id")
        .values("article_id", "geoid", "geoid_level", "is_primary", "source")
    ):
        rows.setdefault(row["article_id"], []).append(
            {
                "geoid": row["geoid"],
                "level": row["geoid_level"],
                "is_primary": bool(row["is_primary"]),
                "source": row["source"] or "",
            }
        )
    made = []
    for article_id in ids:
        e = enrichment.get(article_id)
        made.append(
            GeographyClaim(
                round=for_round,
                article_id=article_id,
                pipeline_class=pipeline_class(getattr(e, "scope", None)) or "",
                scope=getattr(e, "scope", None) or "",
                scope_confidence=getattr(e, "scope_confidence", None),
                profile_version=getattr(e, "profile_version", None),
                point_place=getattr(e, "point_place", None) or "",
                point_geoid=getattr(e, "point_geoid", None) or "",
                point_geoid_level=getattr(e, "point_geoid_level", None) or "",
                point_lat=getattr(e, "point_lat", None),
                point_lon=getattr(e, "point_lon", None),
                point_method=getattr(e, "point_method", None) or "",
                point_support=getattr(e, "point_support", None) or "",
                geo_skip_reason=getattr(e, "geo_skip_reason", None) or "",
                geoid_rows=rows.get(article_id, []),
            )
        )
    GeographyClaim.objects.bulk_create(made, batch_size=500)
    return len(made)


# ----------------------------------------- agreement and consensus (G-WP6)


def counted_decisions(version):
    """The geography answers that count under a guide: not practice, not
    check stories, not from a removed coder."""
    from review.classification import CoderStanding, Round, standings

    removed = [
        user_id
        for user_id, status in standings(version).items()
        if status == CoderStanding.REMOVED
    ]
    return (
        GeographyDecision.objects.filter(codebook_version=version, is_check=False)
        .exclude(cohort__round__kind=Round.PRACTICE)
        .exclude(decided_by_id__in=removed)
    )


def where_of(decision):
    """(place GEOID or None, county GEOID or None) of a P answer."""
    pin = (decision.pins or [{}])[0]
    return pin.get("place"), pin.get("county")


def agree(a, b):
    """Do two coders' answers agree?

    The same class, and:

      P  the same GEOID at the coarser of their two levels: the place
         where both pins are in a Census place, otherwise the county. A
         pin in Ashland and one in the countryside outside it agree on
         Boone County; pins in Ashland and Columbia do not.
      R  the same set of counties.
      S  the same state.
      U  N  the class alone.
    """
    if a.answer_class != b.answer_class:
        return False
    if a.answer_class == PLACE:
        (place_a, county_a), (place_b, county_b) = where_of(a), where_of(b)
        if place_a and place_b:
            return place_a == place_b
        return county_a == county_b
    if a.answer_class == REGION:
        return sorted(a.counties) == sorted(b.counties)
    if a.answer_class == STATE:
        return a.state == b.state
    return True


def add_third_coder(cohort, article_id):
    """A third coder for a geography story whose first two answers
    disagree, in a round that asks for one. Returns the assignment, or
    None where there is nothing to do. Safe to call again."""
    from review.classification import assign_third

    for_round = cohort.round
    if (
        for_round is None
        or not for_round.is_geography
        or not for_round.third_on_split
        or for_round.is_practice
    ):
        return None
    answers = list(
        counted_decisions(cohort.codebook).filter(cohort=cohort, article_id=article_id)
    )
    if len(answers) != 2 or agree(*answers):
        return None
    seen = set(
        GeographyDecision.objects.filter(article_id=article_id).values_list(
            "decided_by_id", flat=True
        )
    )
    return assign_third(cohort, article_id, seen)


def _majority(values):
    """The value at least two chose, or None. With three answers there
    can be only one."""
    counts = {}
    for value in values:
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    winners = [value for value, n in counts.items() if n >= 2]
    return winners[0] if len(winners) == 1 else None


def consensus(answers):
    """What a story's coders agreed, or None.

    The class at least two chose. Then, among the answers in that class:
    for P the place at least two chose, else the county at least two
    chose (so a pin in Ashland and a pin just outside it give Boone
    County, at county level); for R the counties at least two chose; for
    S the state at least two chose. Where nothing reaches two, the story
    has no consensus: it is left out of the scoring and counted.

    Returns {answer_class, level, place, counties, state, coders}.
    """
    answers = list(answers)
    if len(answers) < 2:
        return None
    kind = _majority(a.answer_class for a in answers)
    if kind is None:
        return None
    within = [a for a in answers if a.answer_class == kind]
    out = {
        "answer_class": kind,
        "level": "",
        "place": None,
        "counties": [],
        "state": "",
        "coders": len(answers),
    }
    if kind == PLACE:
        places = [where_of(a) for a in within]
        place = _majority(p for p, _ in places)
        if place is not None:
            county = next(c for p, c in places if p == place)
            return out | {"level": "place", "place": place, "counties": [county]}
        county = _majority(c for _, c in places)
        if county is None:
            return None
        return out | {"level": "county", "counties": [county]}
    if kind == REGION:
        tally = {}
        for answer in within:
            for county in set(answer.counties):
                tally[county] = tally.get(county, 0) + 1
        counties = sorted(c for c, n in tally.items() if n >= 2)
        if not counties:
            return None
        return out | {"level": "county", "counties": counties}
    if kind == STATE:
        state = _majority(a.state for a in within)
        if state is None:
            return None
        return out | {"level": "state", "state": state}
    return out


def round_consensus(for_round):
    """article_id -> consensus (or None) for every story in the round
    with at least two counted answers, and the counts an admin reads:
    stories answered twice, agreed by the first two, split, given a
    third, waiting on one, with a consensus, without one."""
    by_story = {}
    for answer in (
        counted_decisions(for_round.codebook_version)
        .filter(cohort__round=for_round)
        .order_by("decided_at", "id")
    ):
        by_story.setdefault(answer.article_id, []).append(answer)
    results, counts = {}, dict.fromkeys(
        ("answered", "agreed", "split", "third", "waiting", "consensus", "none"), 0
    )
    for article_id, answers in by_story.items():
        if len(answers) < 2:
            continue
        counts["answered"] += 1
        first_two = agree(answers[0], answers[1])
        counts["agreed" if first_two else "split"] += 1
        if not first_two:
            counts["third" if len(answers) > 2 else "waiting"] += 1
        if not first_two and len(answers) == 2:
            # A split still waiting on its third coder has no answer yet.
            continue
        results[article_id] = consensus(answers)
        counts["consensus" if results[article_id] else "none"] += 1
    return results, counts


# ------------------------------------------------------- metrics (G-WP7)


def pipeline_where(claim):
    """(place GEOID or None, county GEOID or None) of the pipeline's
    point, through the same lookup a coder's pin goes through."""
    level = claim.point_geoid_level
    if not claim.point_geoid:
        return None, None
    if level == "county":
        return None, claim.point_geoid
    if claim.point_lat is not None and claim.point_lon is not None:
        where = geo_resolve.resolve(claim.point_lat, claim.point_lon)
        return where.place, where.county
    if level == "place":
        from lnic_contracts.geography import county_for_place

        return claim.point_geoid, county_for_place(claim.point_geoid)[0]
    return None, None


def place_matches(claim, agreed):
    """M5: does the pipeline's point match the coders' agreed place, at
    the coarser of the two levels?"""
    place, county = pipeline_where(claim)
    if county is None:
        return False
    if agreed["level"] == "place" and place:
        return place == agreed["place"]
    return county in agreed["counties"]


def _county_value(kind, counties, state):
    """What a story credits on a coverage map: its counties, or its
    state for a statewide story."""
    if kind == STATE:
        return ("state", state)
    return frozenset(counties)


def pipeline_value(claim):
    """M10: the pipeline's answer in the same terms as the coders'."""
    kind = claim.pipeline_class
    if kind == PLACE:
        _, county = pipeline_where(claim)
        return frozenset({county} if county else set())
    if kind == REGION:
        return frozenset(claim.counties)
    if kind == STATE:
        return ("state", claim.state_geoid)
    return frozenset()


def better_known(claim):
    """The point is named after somewhere better known (Mexico, Paris):
    `doubted_geography`'s list, which flags and never selects."""
    from review.doubted_geography import BETTER_KNOWN_ELSEWHERE

    name = " ".join((claim.point_place or "").lower().replace(".", "").split())
    return name in BETTER_KNOWN_ELSEWHERE


def _cut(items, key, measure, **options):
    """measure() over items grouped by key(item), as {group: figure}."""
    groups = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    return {group: measure(rows, **options) for group, rows in sorted(groups.items())}


def geography_report(for_round, *, resamples=None, seed=0):
    """Every G-WP7 figure for one round: M1 to M11 and the consensus
    counts. Computed from the answers and saved claims on demand; nothing
    here is stored."""
    from review import geography_metrics as gm
    from review.classification import ClassificationSample

    share_n = resamples or gm.RESAMPLES
    alpha_n = resamples or gm.ALPHA_RESAMPLES
    by_story = {}
    for answer in (
        counted_decisions(for_round.codebook_version)
        .filter(cohort__round=for_round)
        .order_by("decided_at", "id")
    ):
        by_story.setdefault(answer.article_id, []).append(answer)
    samples = {
        s.article_id: s
        for s in ClassificationSample.objects.filter(cohort__round=for_round)
    }
    claims = {c.article_id: c for c in GeographyClaim.objects.filter(round=for_round)}
    agreed, counts = round_consensus(for_round)

    def weight(article_id):
        sample = samples.get(article_id)
        p = getattr(sample, "inclusion_probability", 1.0) or 1.0
        return 1.0 / p

    def stratum(article_id):
        return getattr(samples.get(article_id), "stratum", "") or ""

    def alphas(units_by_story, distance):
        overall = gm.alpha_with_interval(
            list(units_by_story.values()), distance, resamples=alpha_n, seed=seed
        )
        cuts = {}
        for article_id, units in units_by_story.items():
            cuts.setdefault(stratum(article_id), []).append(units)
        return {
            "overall": overall,
            "by_stratum": {
                key: gm.alpha_with_interval(
                    rows, distance, resamples=alpha_n, seed=seed
                )
                for key, rows in sorted(cuts.items())
            },
        }

    # M1-M3: do coders agree? Read from each story's first two coders:
    # a third is added only where those two split, so alpha over every
    # coder would measure the rule that added them.
    first_two = {a: answers[:2] for a, answers in by_story.items()}
    classes = {
        a: [d.answer_class for d in pair]
        for a, pair in first_two.items()
        if len(pair) == 2
    }
    places = {}
    regions = {}
    for article_id, pair in first_two.items():
        p = [where_of(d)[1] for d in pair if d.answer_class == PLACE]
        if len(p) == 2:
            places[article_id] = p
        r = [frozenset(d.counties) for d in pair if d.answer_class == REGION]
        if len(r) == 2:
            regions[article_id] = r

    # M4: how far apart are coders who agree?
    apart = []
    for answers in by_story.values():
        pins = [d for d in answers if d.answer_class == PLACE and d.pins]
        for i, a in enumerate(pins):
            for b in pins[i + 1 :]:
                if agree(a, b):
                    apart.append(
                        gm.haversine_km(
                            a.pins[0]["lat"],
                            a.pins[0]["lon"],
                            b.pins[0]["lat"],
                            b.pins[0]["lon"],
                        )
                    )

    # M5, M8: single places.
    place_rows, distance_rows = [], []
    for article_id, result in agreed.items():
        claim = claims.get(article_id)
        if not result or claim is None:
            continue
        if result["answer_class"] != PLACE or claim.pipeline_class != PLACE:
            continue
        place_rows.append(
            {
                "hit": place_matches(claim, result),
                "weight": weight(article_id),
                "method": claim.point_method or "(none)",
                "support": claim.point_support or "(none)",
                "level": claim.point_geoid_level or "(none)",
                "stratum": stratum(article_id),
                "better_known": better_known(claim),
            }
        )
        if claim.point_lat is not None and claim.point_lon is not None:
            distances = [
                gm.haversine_km(
                    claim.point_lat, claim.point_lon, d.pins[0]["lat"], d.pins[0]["lon"]
                )
                for d in by_story[article_id]
                if d.answer_class == PLACE and d.pins
            ]
            if distances:
                distance_rows.append(
                    {
                        "km": gm.percentile(distances, 50),
                        "level": claim.point_geoid_level or "(none)",
                        "method": claim.point_method or "(none)",
                    }
                )

    def share(rows, **options):
        return gm.weighted_share(
            [(row["hit"], row["weight"]) for row in rows], **options
        )

    options = {"resamples": share_n, "seed": seed}

    # M6: regions.
    precision, recall, exact, by_source = [], [], [], {}
    for article_id, result in agreed.items():
        claim = claims.get(article_id)
        if not result or claim is None:
            continue
        if result["answer_class"] != REGION or claim.pipeline_class != REGION:
            continue
        coders = set(result["counties"])
        pipeline = set(claim.counties)
        w = weight(article_id)
        common = len(coders & pipeline)
        precision.append((common, len(pipeline), w))
        recall.append((common, len(coders), w))
        exact.append((coders == pipeline, w))
        rows = {}
        for row in claim.geoid_rows:
            if row.get("level") == "county":
                rows.setdefault(row.get("source") or "(none)", {})[row["geoid"]] = 1
        for source, geoids in rows.items():
            by_source.setdefault(source, []).append(
                (sum(1 for g in geoids if g in coders), len(geoids), w)
            )

    # M7, M9, M10, M11: every story with an agreed answer.
    confusion, calibration, end_to_end, unplaced = {}, [], [], {}
    for article_id, result in agreed.items():
        claim = claims.get(article_id)
        if not result or claim is None:
            continue
        w = weight(article_id)
        kind = result["answer_class"]
        cell = confusion.setdefault(claim.pipeline_class or "?", {}).setdefault(
            kind, {"n": 0, "weight": 0.0}
        )
        cell["n"] += 1
        cell["weight"] += w
        if kind == UNSTATED:
            key = claim.point_method or "(none)"
            unplaced[key] = unplaced.get(key, 0) + 1
            continue
        calibration.append((claim.scope_confidence, claim.pipeline_class == kind, w))
        coders = _county_value(kind, result["counties"], result["state"])
        end_to_end.append(
            {
                "hit": pipeline_value(claim) == coders,
                "weight": w,
                "stratum": stratum(article_id),
            }
        )
    total = sum(c["weight"] for row in confusion.values() for c in row.values())
    for row in confusion.values():
        for c in row.values():
            c["share"] = c["weight"] / total if total else None

    def ratio(rows, **o):
        return gm.weighted_ratio(rows, **o)

    return {
        "counts": counts,
        "m1_class_alpha": alphas(classes, gm.nominal),
        "m2_place_alpha": alphas(places, gm.nominal),
        "m3_region_alpha": alphas(regions, gm.masi),
        "m4_coder_distance": gm.spread(apart),
        "m5_place_accuracy": {
            "overall": share(place_rows, **options),
            **{
                f"by_{key}": _cut(place_rows, lambda r, k=key: r[k], share, **options)
                for key in ("method", "support", "level", "stratum", "better_known")
            },
        },
        "m6_region": {
            "precision": ratio(precision, **options),
            "recall": ratio(recall, **options),
            "exact": gm.weighted_share(exact, **options),
            "precision_by_source": {
                source: ratio(rows, **options)
                for source, rows in sorted(by_source.items())
            },
        },
        "m7_confusion": confusion,
        "m8_pipeline_distance": {
            "overall": gm.spread(r["km"] for r in distance_rows),
            "by_level": _cut(
                distance_rows,
                lambda r: r["level"],
                lambda rows: gm.spread(r["km"] for r in rows),
            ),
            "by_method": _cut(
                distance_rows,
                lambda r: r["method"],
                lambda rows: gm.spread(r["km"] for r in rows),
            ),
        },
        "m9_scope_calibration": gm.brier(calibration),
        "m10_end_to_end": {
            "overall": share(end_to_end, **options),
            "by_stratum": _cut(end_to_end, lambda r: r["stratum"], share, **options),
        },
        "m11_unplaceable": dict(sorted(unplaced.items())),
    }


# ------------------------------------------------------- the gates (G-WP8)

#: Gate 1: coders agree. Krippendorff's alpha, each of M1-M3, on the
#: observed figure -- the usual standard for a reliable variable
#: (Krippendorff 2004), as the CIN pilot uses.
GATE_ALPHA = 0.80
#: Gate 2: the pipeline's single places. The lower bound of M5's 95%
#: interval.
GATE_PLACE = 0.90
#: Gate 3: the pipeline's regions. The lower bound of each of M6's
#: precision and recall. A starting point, not a measured one
#: (docs/GEOGRAPHY_ICR_DATADESK.md §7).
GATE_REGION = 0.80


def _on_figure(gate, name, population, measured, threshold):
    """A condition met when the observed figure reaches the threshold."""
    from review.classification import GateResult

    rate = measured.get("rate")
    return {
        "gate": gate,
        "name": name,
        "population": population,
        "value": rate,
        "low": measured.get("low"),
        "high": measured.get("high"),
        "n": measured.get("n", 0),
        "threshold": threshold,
        "on": "figure",
        "outcome": (
            GateResult.NOT_ENOUGH
            if rate is None
            else GateResult.PASS if rate >= threshold else GateResult.FAIL
        ),
    }


def _on_bound(gate, name, population, measured, threshold):
    """A condition met when the LOWER bound of the 95% interval reaches
    the threshold: the claim is that the pipeline is at least this good,
    and the interval is what can say so."""
    from review.classification import GateResult

    low = measured.get("low")
    return {
        "gate": gate,
        "name": name,
        "population": population,
        "value": measured.get("rate"),
        "low": low,
        "high": measured.get("high"),
        "n": measured.get("n", 0),
        "threshold": threshold,
        "on": "lower bound",
        "outcome": (
            GateResult.NOT_ENOUGH
            if low is None
            else GateResult.PASS if low >= threshold else GateResult.FAIL
        ),
    }


def _jsonable(value):
    """The report as JSON can hold it: keys as strings, tuples as lists."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return value


def round_finished(for_round, counts=None):
    """A geography pilot is finished when every story drawn has its two
    answers and no split is waiting on its third coder."""
    from review.classification import ClassificationSample

    if counts is None:
        _, counts = round_consensus(for_round)
    stories = ClassificationSample.objects.filter(cohort__round=for_round).count()
    return bool(stories) and counts["answered"] == stories and not counts["waiting"]


def geography_gate(for_round, *, by=None, record=False, report=None):
    """Measure a geography round against its gates; with `record`, write
    the result down as a `GateResult` (never changed; evaluating again
    adds a row).

    A practice round has the CIN practice conditions: every coder has
    coded every story, and the review has been held. A pilot has three
    gates:

      1. Coders agree: M1, M2 and M3 each at least GATE_ALPHA.
      2. Single places: M5's lower bound at least GATE_PLACE.
      3. Regions: M6 precision's and recall's lower bounds at least
         GATE_REGION.

    Gate 1 comes first. Until it passes, gates 2 and 3 are NOT ENOUGH
    DATA whatever their figures: without a reliable human answer the
    pipeline has nothing to be scored against. A round still being coded
    is NOT ENOUGH as a whole, however the figures look so far.
    """
    from review.classification import GateResult, round_report

    if for_round.is_practice:
        progress = round_report(for_round)
        conditions = [
            {
                "name": "Every coder has coded every story",
                "outcome": (
                    GateResult.PASS if progress["coded"] else GateResult.NOT_ENOUGH
                ),
            },
            {
                "name": "The review has been held",
                "outcome": (
                    GateResult.PASS if progress["reviewed"] else GateResult.NOT_ENOUGH
                ),
            },
        ]
        outcomes = {c["outcome"] for c in conditions}
        outcome = (
            GateResult.PASS if outcomes == {GateResult.PASS} else GateResult.NOT_ENOUGH
        )
        measures = {}
    else:
        report = report or geography_report(for_round)
        alpha = {
            "m1": report["m1_class_alpha"]["overall"],
            "m2": report["m2_place_alpha"]["overall"],
            "m3": report["m3_region_alpha"]["overall"],
        }
        first = [
            _on_figure(
                1,
                "Coders agree on the kind of place",
                "every story, first two coders",
                alpha["m1"],
                GATE_ALPHA,
            ),
            _on_figure(
                1,
                "Coders agree on the county of a single place",
                "stories both first coders placed at one place",
                alpha["m2"],
                GATE_ALPHA,
            ),
            _on_figure(
                1,
                "Coders agree on a region's counties",
                "stories both first coders called a county or wider",
                alpha["m3"],
                GATE_ALPHA,
            ),
        ]
        later = [
            _on_bound(
                2,
                "The pipeline places a single place right",
                "coders agreed on one place and the pipeline gave a point",
                report["m5_place_accuracy"]["overall"],
                GATE_PLACE,
            ),
            _on_bound(
                3,
                "The pipeline's regional counties are the coders' (precision)",
                "coders agreed on a region and so did the pipeline",
                report["m6_region"]["precision"],
                GATE_REGION,
            ),
            _on_bound(
                3,
                "The coders' regional counties are the pipeline's (recall)",
                "coders agreed on a region and so did the pipeline",
                report["m6_region"]["recall"],
                GATE_REGION,
            ),
        ]
        if any(c["outcome"] != GateResult.PASS for c in first):
            for c in later:
                c["outcome"] = GateResult.NOT_ENOUGH
                c["held"] = "gate 1 has not passed"
        conditions = first + later
        outcomes = {c["outcome"] for c in conditions}
        if not round_finished(for_round, report["counts"]):
            outcome = GateResult.NOT_ENOUGH
        elif GateResult.FAIL in outcomes:
            outcome = GateResult.FAIL
        elif GateResult.NOT_ENOUGH in outcomes:
            outcome = GateResult.NOT_ENOUGH
        else:
            outcome = GateResult.PASS
        measures = _jsonable(report)

    result = {"outcome": outcome, "conditions": conditions, "measures": measures}
    if record:
        result["record"] = GateResult.objects.create(
            round=for_round,
            evaluated_by=by,
            outcome=outcome,
            conditions=conditions,
            measures=measures,
        )
    return result


# --------------------------------------------------- admin screens (G-WP9)


def coder_figures(for_round):
    """coder id -> {agreement: {rate, n}, median_seconds} for the round
    page. Agreement is over the first pairs a coder is in. For admins
    only: a coder who can watch their agreement codes toward the others.
    """
    import statistics

    by_story, seconds = {}, {}
    for answer in (
        counted_decisions(for_round.codebook_version)
        .filter(cohort__round=for_round)
        .order_by("decided_at", "id")
    ):
        by_story.setdefault(answer.article_id, []).append(answer)
        if answer.seconds_spent is not None:
            seconds.setdefault(answer.decided_by_id, []).append(answer.seconds_spent)
    pairs = {}
    for answers in by_story.values():
        if len(answers) < 2:
            continue
        a, b = answers[0], answers[1]
        same = agree(a, b)
        for coder in (a.decided_by_id, b.decided_by_id):
            hits, n = pairs.get(coder, (0, 0))
            pairs[coder] = (hits + same, n + 1)
    return {
        coder: {
            "agreement": (
                {"rate": pairs[coder][0] / pairs[coder][1], "n": pairs[coder][1]}
                if coder in pairs
                else None
            ),
            "median_seconds": (
                int(statistics.median(seconds[coder])) if coder in seconds else None
            ),
        }
        for coder in set(pairs) | set(seconds)
    }


def describe(answer):
    """A coder's answer in words, for the disagreements page."""
    shown = dict(CLASS_CHOICES)[answer.answer_class]
    if answer.answer_class == PLACE and answer.pins:
        pin = answer.pins[0]
        where = pin.get("place_name") or "outside any town"
        return f"{shown}: {where}, {pin.get('county_name', '')} County"
    if answer.answer_class == REGION:
        names = [geo_resolve.county_of(c) or c for c in answer.counties]
        return f"{shown}: " + ", ".join(names)
    if answer.answer_class == STATE:
        return f"{shown}: {dict(state_choices()).get(answer.state, answer.state)}"
    return shown


def disagreements(for_round, *, include_agreed=False):
    """Every story whose first two coders split (or every story, with
    `include_agreed`), with each coder's answer and the pipeline's claim,
    for the admin's map. The coders are named here: the page is admin
    only, and a definition problem shows up as one coder's pattern."""
    from review.classification import CoderStanding, standings, stories_for

    # By round rather than `counted_decisions`, which leaves practice
    # out: a practice review is exactly where these are read.
    removed = [
        user_id
        for user_id, status in standings(for_round.codebook_version).items()
        if status == CoderStanding.REMOVED
    ]
    by_story = {}
    for answer in (
        GeographyDecision.objects.filter(cohort__round=for_round, is_check=False)
        .exclude(decided_by_id__in=removed)
        .select_related("decided_by")
        .order_by("decided_at", "id")
    ):
        by_story.setdefault(answer.article_id, []).append(answer)
    agreed, _ = round_consensus(for_round)
    claims = {c.article_id: c for c in GeographyClaim.objects.filter(round=for_round)}
    wanted = [
        article_id
        for article_id, answers in by_story.items()
        if len(answers) >= 2
        and (
            include_agreed
            # In practice every coder codes every story: any two apart.
            or not all(agree(answers[0], other) for other in answers[1:])
        )
    ]
    titles = stories_for(wanted)
    rows = []
    for article_id in wanted:
        claim = claims.get(article_id)
        rows.append(
            {
                "article_id": article_id,
                "title": getattr(titles.get(article_id), "title", "") or "",
                "answers": [
                    {
                        "coder": str(answer.decided_by),
                        "class": answer.answer_class,
                        "said": describe(answer),
                        "pins": [
                            {"lat": p["lat"], "lon": p["lon"]} for p in answer.pins
                        ],
                        "counties": list(answer.counties),
                        "state": answer.state,
                    }
                    for answer in by_story[article_id]
                ],
                "agreed": agreed.get(article_id),
                "pipeline": (
                    {
                        "class": claim.pipeline_class,
                        "scope": claim.scope,
                        "point": (
                            {"lat": claim.point_lat, "lon": claim.point_lon}
                            if claim.point_lat is not None
                            else None
                        ),
                        "point_place": claim.point_place,
                        "point_method": claim.point_method,
                        "counties": claim.counties,
                        "state": claim.state_geoid,
                    }
                    if claim
                    else None
                ),
            }
        )
    return rows


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
