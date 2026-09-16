"""Geography the pipeline recorded, and geography it threw away.

`review/geography.py` asks a person for geography where the pipeline
found none. These two views ask a different question: where the pipeline
DID decide, was it right?

WHAT THE GATE REFUSED. `src/enrichment/grounding.py` refuses a place the
article does not name in its own reporting — 3,854 claims across the
corpus, 585 central places and 3,269 mentions. It is the right rule by
every measure taken, and it is still a rule applied without a person
seeing it. A refusal that is wrong is invisible: the claim is gone, and
nothing records that it was ever made.

Nothing has to be stored for this. `article_places` holds what the MODEL
said, unfiltered; `article_geoids` holds what survived. The difference is
the refusal, with the model's own `description` and `mention_text`
alongside it — so a reviewer sees the claim, the sentence it came from,
and the place, without opening the article.

WHAT THE GATE KEPT BUT BARELY. A place can pass the gate and still be
doubtful:

  - kept only because an institution sits there, never named itself
    (167 central places). The strongest of the three signals and the
    reason the statewide gazetteer exists — but it is induction, and a
    reader should see the ones it carried.
  - placed by the code fallback rather than by the model (89), which
    assigns the publication's own city.
  - a name that collides with something better known. Missouri has a
    Mexico, a Cuba, a Lebanon, a Nevada, a California, a Paris, a Cairo
    and a Milan, and a story saying "Mexico" usually means the country.

The first two SELECT the queue. The third is carried as a FLAG on the
row rather than as a selection criterion, because it is a hand-written
list of what counts as better known, and 1,033 articles is too many to
put in front of somebody on my judgement alone. A flag informs a
reviewer; a criterion decides for them.
"""

from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

from explorer.models import ArticleEnrichment, ArticleGeoid, ArticlePlace

#: Levels a story-level claim can hold. A country or a state named in
#: passing was never a candidate for `article_geoids`, so its absence
#: there is not a refusal.
STORY_LEVELS = ("place", "county")

#: Placed by code rather than by the model: these assign the
#: publication's own city, which is the bias the grounding gate exists
#: to catch.
FALLBACK_METHODS = ("publication_city", "publication_place_assumed")

#: `article_enrichment.point_support`. The article never named this
#: place; it named an institution that sits there, and the gate reasoned
#: from that. Sound, and the reason the statewide gazetteer exists --
#: but it is induction, and 167 central places rest on it alone.
BY_INSTITUTION = "institution"
NAMED_IN_THE_STORY = "named"

#: Missouri towns whose names are better known as something else. A
#: hand-written list, deliberately visible here rather than buried in a
#: query, because it is a judgement about what a reader knows and it
#: should be arguable. It FLAGS a row; it never selects one.
#:
#: Only matters within a state: Missouri's Mexico competes with the
#: country, not with Mexico, Maine.
BETTER_KNOWN_ELSEWHERE = {
    # countries
    "cuba",
    "lebanon",
    "mexico",
    "peru",
    "china",
    "japan",
    "egypt",
    "brazil",
    "poland",
    "norway",
    "scotland",
    "wales",
    "jordan",
    "syria",
    # world cities
    "cairo",
    "canton",
    "carthage",
    "glasgow",
    "milan",
    "moscow",
    "paris",
    "vienna",
    "versailles",
    "amsterdam",
    "athens",
    "sparta",
    "troy",
    "memphis",
    "alexandria",
    "bethlehem",
    "palmyra",
    "smyrna",
    "odessa",
    # other states
    "california",
    "nevada",
    "new york",
    "washington",
    "florida",
    "louisiana",
    "nebraska",
    "delaware",
    "oregon",
    "montana",
    # people, or a much larger city of the same name
    "clinton",
    "columbia",
    "columbus",
    "hamilton",
    "houston",
    "jackson",
    "jefferson",
    "lincoln",
    "madison",
    "monroe",
    "franklin",
    "cleveland",
    "marshall",
    "boone",
    "grant",
    "sherman",
    "wilson",
    "kennedy",
}


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().replace(".", " ").split())


def refused_claims(dataset=None, since=None, until=None, newsroom=None):
    """Places the model claimed that the gate did not keep.

    A claim is refused when the model recorded it in `article_places`
    with a story-level geoid and no matching row survives in
    `article_geoids`. Restricted to enriched articles, because an
    article the pipeline never read has no claim to refuse.
    """
    survived = ArticleGeoid.objects.filter(
        article_id=OuterRef("article_id"), geoid=OuterRef("geoid")
    )
    enriched = ArticleEnrichment.objects.filter(article_id=OuterRef("article_id"))

    claims = (
        ArticlePlace.objects.filter(
            geoid__isnull=False,
            geoid_level__in=STORY_LEVELS,
        )
        .filter(Exists(enriched))
        .filter(~Exists(survived))
    )
    if dataset:
        claims = claims.filter(article__dataset_id=dataset)
    if since:
        claims = claims.filter(article__publish_date__gte=since)
    if until:
        claims = claims.filter(article__publish_date__lt=until)
    if newsroom:
        claims = claims.filter(article__candidate_link__source_id=newsroom)
    return claims.select_related("article").order_by("article_id", "full_name")


def doubted_points(dataset=None, since=None, until=None, newsroom=None):
    """Central places the gate kept, on evidence worth a second look.

    Selected on the two signals that are measured rather than judged:
    the point survives only because an institution sits there (167), or
    it was placed by the code fallback rather than by the model (86).
    The name-collision flag is added by `flags_for` and does NOT select:
    it rests on a hand-written list of what a reader knows, and 1,033
    articles is too many to put in front of somebody on that basis.
    """
    kept = ArticleEnrichment.objects.filter(
        point_geoid__isnull=False,
        point_geoid_level__in=STORY_LEVELS,
    ).filter(
        # The crawler records WHICH reason kept the point, because the
        # reason is computed against the article text at enrichment and
        # cannot be recovered here. `institution` means the article
        # never named the place -- it named something that sits there.
        Q(point_support=BY_INSTITUTION)
        | Q(point_method__in=FALLBACK_METHODS)
    )

    if dataset:
        kept = kept.filter(article__dataset_id=dataset)
    if since:
        kept = kept.filter(article__publish_date__gte=since)
    if until:
        kept = kept.filter(article__publish_date__lt=until)
    if newsroom:
        kept = kept.filter(article__candidate_link__source_id=newsroom)
    return kept.select_related("article").order_by("article_id")


def flags_for(
    place_name: str | None,
    method: str | None = None,
    support: str | None = None,
) -> list[str]:
    """Why this row might be doubted, in words a reviewer can act on.

    Flags do not select the queue. They say what to look for once a row
    is in front of somebody.
    """
    flags: list[str] = []
    if support == BY_INSTITUTION:
        flags.append("the story never names this place; an institution here does")
    if method in FALLBACK_METHODS:
        flags.append("placed by the code fallback, not the model")
    if _norm(place_name) in BETTER_KNOWN_ELSEWHERE:
        flags.append("the name is better known as somewhere else")
    return flags
