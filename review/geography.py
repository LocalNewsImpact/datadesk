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

from functools import lru_cache

from django.db.models import Q

from explorer.models import ArticlePlaceManual
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
        # BY SLUG, THROUGH THE DATASET. `articles.dataset_id` holds the
        # dataset's UUID and the filter bar offers slugs, so
        # `dataset_id=<slug>` matched nothing: picking a dataset emptied
        # the queue and every dropdown built from it, which read as a
        # dataset with no work in it.
        #
        # `review.queue._in_dataset` is the one definition of this, and
        # its docstring says why: "a dropdown built over a different
        # population than the list offers values that return nothing."
        from review.queue import _in_dataset

        rows = _in_dataset(rows, dataset)
    if since:
        rows = rows.filter(publish_date__gte=since)
    if until:
        rows = rows.filter(publish_date__lt=until)
    if county:
        # On the SOURCE, not the candidate link. `candidate_links` has no
        # county column; writing one raises FieldError the first time
        # somebody picks a county, which is the only filter this queue
        # was built around.
        rows = rows.filter(candidate_link__source__county=county)
    if newsroom:
        rows = rows.filter(candidate_link__source_id=newsroom)
    return rows


def publisher_state(article):
    """The state to rank suggestions by.

    `sources` has `city` and `county` and no `state` column -- the value
    lives in `sources.metadata` JSON, falling back to the dataset's
    `default_state`. That is exactly how enrichment resolves it
    (`repository.py`: `coalesce(nullif(s.metadata::json->>'state',''),
    d.metadata::json->>'default_state')`), and reading it from anywhere
    else is how the console and the pipeline come to disagree about what
    state an outlet is in.
    """
    source = getattr(getattr(article, "candidate_link", None), "source", None)
    meta = getattr(source, "meta", None) or {}
    state = (meta.get("state") or "").strip()
    if state:
        return state

    # Only then the dataset, and only once per dataset: this is called
    # per row, so an unguarded query here is one round trip per article
    # to read a value a page of 25 shares. An article with no dataset
    # asked for `id=None` and got a query for nothing at all.
    dataset_id = getattr(article, "dataset_id", None)
    if not dataset_id:
        return None
    return _default_state(dataset_id)


@lru_cache(maxsize=64)
def _default_state(dataset_id):
    from explorer.models import Dataset

    dataset = Dataset.objects.filter(id=dataset_id).first()
    fallback = (getattr(dataset, "meta", None) or {}).get("default_state") or ""
    return fallback.strip() or None


def parse_places(raw, default_state=None):
    """What a reviewer typed, as places.

    SEVERAL, because a story mentions several. The submit path carries
    one value per article -- `ReviewDecision` is keyed on (subject,
    field, question) and `update_or_create` overwrites -- so a second
    mention submitted separately would replace the first DECISION while
    both rows stayed in the table, leaving the audit trail disagreeing
    with the data it is supposed to explain.

    One decision, one auditable string, several rows: places are
    separated by `;`, and a place may name its own state after a comma.
    Anything that does not is read as the publisher's own.
    """
    out = []
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip().strip(",")
        if not chunk:
            continue
        name, _, state = chunk.rpartition(",")
        if name.strip():
            out.append((name.strip(), state.strip()))
        else:
            out.append((chunk, (default_state or "").strip()))
    return out


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
    from lnic_contracts.geography import (
        state_code,
        suggest_counties,
        suggest_places,
    )

    if kind == "county":
        return [
            dict(h, kind="county")
            for h in suggest_counties(name, prefer_state=publisher_state)
        ]

    places = [
        dict(h, kind="place")
        for h in suggest_places(name, prefer_state=publisher_state)
    ]

    # THE COUNTY RUNG, BECAUSE NOT EVERY PLACE IS A CENSUS PLACE.
    #
    # Frankenstein is a real village in Osage County, Missouri, and it is
    # not in the gazetteer -- it is unincorporated, so it is neither an
    # incorporated place nor a CDP. Offering only places leaves a story
    # about it unenterable, and a reviewer who cannot enter what they
    # read either invents a nearby town or gives up on the row.
    #
    # The county is what the map shades anyway, so the fallback loses
    # precision rather than the article.
    counties = [
        dict(h, kind="county")
        for h in suggest_counties(name, prefer_state=publisher_state)
    ]

    # SLOTS ARE RESERVED, NOT COMPETED FOR. Appending counties after
    # places and truncating meant places took every slot: "Osage" for a
    # Missouri outlet returned Osage IA, MN, OK, WV, WY and Sage CA --
    # and not Osage County, Missouri, which is the answer. There is no
    # Missouri PLACE called Osage, so the rung that had it never got a
    # row.
    merged = places[:6] + counties[:4]

    # Then the working state to the top, across both rungs. `sort` is
    # stable, so the contract's own closeness ranking survives inside
    # each group -- sorting by name is what once returned Asherville and
    # Asheville for "Nashvile" and never Nashville.
    if publisher_state:
        home = state_code(publisher_state) or publisher_state
        merged.sort(key=lambda hit: hit.get("state") != home)
    return merged


def apply_geography(article, verb, value, user):
    """Carry out one decision and say what it wrote.

    `value` is a STRING. The submit path reads it from a form field and
    stores it on `ReviewDecision.value` verbatim, so this parses rather
    than expecting a structure nothing upstream builds.

    A CENTRE IS ONE PLACE; MENTIONS ARE MANY. Enrichment writes as many
    `mention` rows as the story names, and a person contributing the same
    geography has to be able to say the same thing -- otherwise the human
    path is a lesser version of the machine one and reviewers work around
    it.

    The `ReviewDecision` itself is written by `review/submit.py`, which
    every queue shares; this returns the payload it stores.
    """
    from review.services import audited_create

    if verb == NOTHING_TO_ADD or getattr(verb, "name", verb) == NOTHING_TO_ADD:
        # Writes nothing. The article has no geography and a person has
        # confirmed none can be given, which is what stops the queue
        # asking again.
        return {"wrote": "nothing", "label": article.title or "", "after": "no place"}

    from lnic_contracts.geography import canonical_county, canonical_place

    name = getattr(verb, "name", verb)
    is_point = name == SET_THE_PLACE
    default_state = publisher_state(article)
    wanted = parse_places(value, default_state)
    if not wanted:
        raise ValueError("No place was given.")
    if is_point and len(wanted) > 1:
        # A story has one centre by definition, and the table's partial
        # unique index says so. Refusing here names the reason; letting
        # it through raises IntegrityError on the second row.
        raise ValueError(
            "A story has one central location. List the others under "
            "“It also mentions”."
        )

    # RESOLVE EVERYTHING FIRST, THEN WRITE. Building rows as the loop
    # goes and checking afterwards means a refusal is decided after some
    # of the work is already shaped -- and a future edit that moved the
    # write inside the loop would write half a reviewer's answer and
    # raise on the rest. Two phases make that mistake impossible.
    resolved, unresolved = [], []
    for place, state in wanted:
        # "Osage County" is the county rung, and the suffix is how the
        # suggestion list writes it back. Resolving it as a place would
        # fail -- no gazetteer has a place called "Osage County".
        bare, level = place, "place"
        if place.lower().endswith(" county"):
            bare, level = place[: -len(" county")].strip(), "county"

        if level == "county":
            geoid, official = canonical_county(state or default_state, bare)
        else:
            geoid, official = canonical_place(state or default_state, bare)

        if geoid is None:
            unresolved.append(f"{place}{', ' + state if state else ''}")
            continue
        resolved.append((place, state or default_state, geoid, official, level))

    if unresolved:
        # NOTHING is written when any part did not resolve. A partial
        # write would record some of what a reviewer said and silently
        # drop the rest, and they would have no way to tell which.
        raise ValueError(
            f"{', '.join(unresolved)} did not resolve to a place. "
            "Pick from the suggestions."
        )

    rows = [
        ArticlePlaceManual(
            article_id=article.id,
            full_name=place,
            city=official if level == "place" else None,
            county=official if level == "county" else None,
            state=state,
            geoid=geoid,
            geoid_level=level,
            is_point=is_point,
            added_by=user.email,
            note="",
        )
        for place, state, geoid, official, level in resolved
    ]
    written = [
        {"geoid": geoid, "place": official, "level": level}
        for _place, _state, geoid, official, level in resolved
    ]

    entry = audited_create(
        user,
        rows,
        action=f"geography:{name}",
        reason=f"{', '.join(r['place'] for r in written)} on {article.id}",
    )
    return {
        "label": article.title or "",
        "after": ", ".join(r["place"] for r in written),
        "wrote": {"places": written, "is_point": is_point, "audit": entry.pk},
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
                sublabel=(
                    "Places the story names that are not its centre — "
                    "separate several with ;"
                ),
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
