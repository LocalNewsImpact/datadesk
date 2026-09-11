"""Geography a person puts in.

Some articles will never get their geography from the pipeline. A story
behind a paywall arrives as a headline and a subscription prompt, and no
amount of re-running the model reads what was not fetched. The pipeline
is right to stop; the story still happened in a place, and that place
still belongs on a map of where an outlet reports.

The contribution is used exactly as the pipeline's own is — same ladder,
same crosswalk, same `article_geoids` rows — while staying visibly a
person's, because it carries `source = 'human'`.

See `MizzouNewsCrawler/docs/MANUAL_GEOGRAPHY.md` for the storage rule and
why the contribution does not live in `article_geoids` itself.
"""

from django.db.models import Q

from review import kernel

GEOGRAPHY_QUEUE_KEY = "geography"

SET_THE_PLACE = "set_place"
ALSO_MENTIONS = "also_mentions"
NOTHING_TO_ADD = "nothing_to_add"

#: Why an article has no geography, and whether a person can help.
#:
#: The population is smaller than "articles with no geography", and the
#: difference is what makes the queue workable. Measured on the Missouri
#: dataset, excluding wire, obituaries, weather and opinion: 85,496 never
#: enriched, 3,636 enriched with no reason recorded, 1,155 where the
#: pipeline read the story and found no place — against about 1,300 a
#: person is the only route for.
REVIEWABLE_SKIPS = (
    "not_scoped",
    "regional_uses_place_set",
    "publication_city_not_in_census_gazetteer",
    "publication_state_unknown",
    "city_not_in_census_gazetteer",
)

#: The verdict that is NOT a gap.
#:
#: `no_codeable_geography` means the pipeline read the story and found no
#: place in it — a column, a devotional, a national piece with nothing
#: local. Asking somebody to second-guess 1,155 correct answers to find
#: the real gaps is how a queue stops being worked. Spot-checking that
#: verdict is a reasonable thing to want and a different question, asked
#: its own way.
ANSWERED_NOT_MISSING = "no_codeable_geography"


def needs_geography(dataset=None, since=None, until=None, county=None, newsroom=None):
    """Articles a person could give geography to, narrowed.

    343 articles for March across three counties, 273 of them Boone,
    across ten newsrooms. Nobody works a 343-item queue; "Osage, March"
    is 23 and finishable in a sitting. So the four filters are not a
    convenience — without them the queue is not a queue.

    They are the four the visual builder's spec already uses, so the
    console keeps one vocabulary rather than growing a second.
    """
    from explorer.models import Article

    rows = Article.objects.exclude(
        candidate_link__status__in=("wire", "obituary", "weather", "opinion")
    ).filter(
        # No geography of any kind: no centre, and no mentions.
        Q(enrichment__point_geoid__isnull=True)
        & (
            Q(enrichment__geoids__isnull=True)
            | Q(enrichment__geoids="")
            | Q(enrichment__geoids="[]")
        )
    )
    # A paywall stub is the clearest case for a person: the pipeline could
    # not read the story at all. The other reviewable skips are ones where
    # it read it and could not place it.
    rows = rows.filter(
        Q(enrichment__skip_reason__icontains="paywall")
        | Q(enrichment__geo_skip_reason__in=REVIEWABLE_SKIPS)
    ).exclude(enrichment__geo_skip_reason=ANSWERED_NOT_MISSING)

    if dataset:
        rows = rows.filter(dataset_id=dataset)
    if since:
        rows = rows.filter(publish_date__gte=since)
    if until:
        rows = rows.filter(publish_date__lt=until)
    if county:
        rows = rows.filter(candidate_link__source_county=county)
    if newsroom:
        rows = rows.filter(candidate_link__source_id=newsroom)
    return rows


def why_no_geography(article):
    """What a reviewer needs to know before opening the link.

    A paywall stub reads differently from a story nobody scoped: the
    first needs somebody to go and read it, the second may have the place
    in the text already.
    """
    enrichment = getattr(article, "enrichment", None)
    if enrichment is None:
        return "never enriched", "Nothing ran on this article."
    reason = (enrichment.skip_reason or "").lower()
    if "paywall" in reason:
        return (
            "behind a paywall",
            "Only a headline and a subscription prompt were captured. "
            "The link is the only way to read it.",
        )
    geo = enrichment.geo_skip_reason or ""
    if geo == "not_scoped":
        return "never scoped", "Enrichment stopped before it reached geography."
    if geo == "regional_uses_place_set":
        return (
            "a regional story with no places",
            "It was read as regional, whose geography is the places it "
            "names — and it named none that resolved.",
        )
    if geo.endswith("not_in_census_gazetteer"):
        return (
            "a place the gazetteer does not have",
            "The publication's own city did not resolve, so nothing "
            "could be placed relative to it.",
        )
    return geo or "no reason recorded", ""


def suggest(name, publisher_state=None, kind="place"):
    """What to offer for a typed name, the publisher's state first.

    RANKED, NEVER FILTERED. Ranking catches the error the pipeline made:
    a story about the sewer trustees of Freeburg, a village in Osage
    County, Missouri, was extracted as "Freeburg, IL" — Illinois has one,
    the lookup succeeded, and the story was filed three hundred miles
    away. Somebody typing "Freeburg" for a Missouri outlet is offered
    Missouri's first.

    Filtering would be wrong: Whiteman Air Force Base, Nashville, Wichita
    State and Seattle were all covered by Missouri outlets in one month.
    A same-state-only list makes real coverage unenterable, which is how
    a queue teaches people to work around it.

    The suggestions come from the same table the crawler resolves
    against, so what is offered is what will resolve.
    """
    from lnic_contracts.geography import suggest_counties, suggest_places

    find = suggest_places if kind == "place" else suggest_counties
    return find(name, prefer_state=publisher_state)


def apply_geography(article, verb, value, user):
    """Carry out one decision and say what it wrote.

    The `ReviewDecision` itself is written by `review/submit.py`, which
    every queue shares; this returns the payload it stores.
    """
    from review.services import audited_create

    if verb == NOTHING_TO_ADD:
        # Writes nothing. The article has no geography and a person has
        # confirmed none can be given, which is what stops the queue
        # asking again.
        return {"wrote": "nothing", "article": article.id}

    from lnic_contracts.geography import canonical_place

    geoid, official = canonical_place(value.get("state"), value.get("name"))
    if geoid is None:
        raise ValueError(
            f"{value.get('name')!r} is not a place in "
            f"{value.get('state')!r}. Pick one of the suggestions."
        )
    from explorer.models import ArticlePlaceManual

    entry = audited_create(
        user,
        [
            ArticlePlaceManual(
                article_id=article.id,
                full_name=value.get("name"),
                city=official,
                state=value.get("state"),
                geoid=geoid,
                geoid_level="place",
                is_point=verb == SET_THE_PLACE,
                added_by=user.email,
                note=value.get("note") or "",
            )
        ],
        action=f"geography:{verb}",
        reason=f"{official} on {article.id}",
    )
    return {
        "geoid": geoid,
        "place": official,
        "is_point": verb == SET_THE_PLACE,
        "audit": entry.pk,
    }


GEOGRAPHY_QUEUE = kernel.register(
    kernel.Queue(
        key=GEOGRAPHY_QUEUE_KEY,
        subject_type="article",
        verbs=(
            kernel.Verb(
                name=SET_THE_PLACE,
                label="This is where it is",
                sublabel="The story's central location — one per article",
                past="placed",
                tone="fix",
                takes_value=True,
                value_required=True,
            ),
            kernel.Verb(
                name=ALSO_MENTIONS,
                label="It also mentions",
                sublabel="Somewhere the story names that is not its centre",
                past="noted",
                tone="fix",
                takes_value=True,
                value_required=True,
            ),
            kernel.Verb(
                name=NOTHING_TO_ADD,
                label="No place in it",
                sublabel="The queue stops asking; nothing is written",
                past="confirmed",
                tone="reject",
                takes_value=False,
            ),
        ),
        apply=apply_geography,
    )
)
