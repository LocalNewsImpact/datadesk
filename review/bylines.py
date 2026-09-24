"""Bylines: what a byline string is, decided one string at a time.

`articles.author` is what a parser made of a page, and across a dataset the same
reporter appears under several spellings, a desk name appears as a person, and a
job title rides along after a name. Counting those strings counts spellings.

WHAT THIS PAGE IS NOT. It is not a list of every byline: the crawler computes
which strings show a defect and writes them to `byline_review_candidates`, and
this renders that. On Mizzou it is about 150 rows against 1,824 people.

WHAT A DECISION IS. One row per (dataset, raw string) in `byline_normalizations`
saying which people the string names -- or nobody. The crawler applies it to
`articles.author`, so the permanent record carries the corrected name, and the
row survives a re-extraction that writes the raw form again.

CO-AUTHORS ARE NOT A DEFECT and never appear here. A byline naming three
reporters is three records, which the crawler's `author_records` produces.
"""

import re

from django.db import router, transaction
from django.utils import timezone

#: How a byline string separates the people it names. The crawler's
#: `byline_review._SEPARATORS` in the form this console needs.
_SEPARATORS = re.compile(r"\s*(?:,|;|\band\b|&|\u2022|\|)\s*")


def _norm(value):
    """A name flattened for comparison: case and spacing only."""
    return " ".join((value or "").split()).casefold()


def write_alias():
    """The alias a crawler write goes through: `crawler_rw` in production.

    `crawler` is the READ-ONLY connection -- it authenticates as `datadesk_ro`,
    which Postgres refuses every write on. The router already knows this
    (`explorer/routers.py`), and the suite pops `crawler_rw` so both aliases are
    one sqlite file locally. So `using("crawler")` on a write passes every test
    and fails in production only, as "permission denied for table …". That is
    exactly how it shipped.

    Reads stay on `crawler` -- that is what it is for.
    """
    from explorer.models import BylineNormalization

    return router.db_for_write(BylineNormalization)


#: The decisions a reviewer can take on a string.
ACCEPT = "accept"  # the string is already right
FIX = "fix"  # these are the people it names
#: The string is not a real name: a desk, a bot, a contact address, a CMS
#: account. The stored value stays `drop` because the crawler reads it and a
#: decision already recorded carries it; only what a reviewer reads changed.
#: "Names nobody" was the label, and it read backwards -- as though the reviewer
#: were naming nobody rather than saying the string does not name anybody.
DROP = "drop"

DECISION_LABELS = {
    ACCEPT: "accepted as it stands",
    FIX: "fixed",
    DROP: "not a real name",
}


def candidates(dataset_id, signal="", limit=200):
    """The queue for one dataset, worst signal first, then biggest.

    Ordered in the database rather than in Python: the page shows a slice and
    the count underneath it is of the whole queue.
    """
    from explorer.models import BylineReviewCandidate

    rows = BylineReviewCandidate.objects.using("crawler").filter(dataset_id=dataset_id)
    if signal:
        rows = rows.filter(signal=signal)
    return rows.order_by("signal", "-articles", "raw_byline")[:limit]


def signal_counts(dataset_id):
    """`[(signal, label, count)]` for the filter chips, biggest first."""
    from django.db.models import Count, Max

    from explorer.models import BylineReviewCandidate

    rows = (
        BylineReviewCandidate.objects.using("crawler")
        .filter(dataset_id=dataset_id)
        .values("signal")
        .annotate(n=Count("id"), label=Max("signal_label"))
        .order_by("-n")
    )
    return [(row["signal"], row["label"], row["n"]) for row in rows]


def parse_names(value):
    """The names a reviewer typed, one per line or comma-separated.

    Both, because a co-authored byline is natural to type on one line and a
    correction is natural to type on its own.
    """
    parts = []
    for line in (value or "").replace(",", "\n").splitlines():
        name = " ".join(line.split())
        if name:
            parts.append(name)
    return parts


def decide(dataset_id, raw_byline, decision, names, user, reason=""):
    """Record what a string is, and take it off the queue.

    The candidate row goes now rather than at the next refresh, so a worked
    queue empties as it is worked. The crawler rewrites it on the next refresh
    only if the string still shows a defect -- which a decided string does not,
    because `refresh_candidates` reads the decisions.

    `articles.author` is NOT written here. The crawler owns that write
    (`byline-report apply`), which keeps one writer for the column and keeps a
    review request from updating thousands of rows.
    """
    if decision not in DECISION_LABELS:
        raise ValueError(f"not a decision: {decision!r}")
    canonical = [] if decision == DROP else list(names)

    alias = write_alias()
    with transaction.atomic(using=alias):
        return _decide(alias, dataset_id, raw_byline, decision, canonical, user, reason)


def _decide(alias, dataset_id, raw_byline, decision, canonical, user, reason):
    """The body of `decide`, inside its transaction."""
    from explorer.models import BylineNormalization, BylineReviewCandidate

    # Read on the write alias too, inside the transaction that is about to
    # update it: reading the row on one connection and updating it on another
    # is how two reviewers deciding the same string at once lose one answer.
    existing = (
        BylineNormalization.objects.using(alias)
        .filter(dataset_id=dataset_id, raw_byline=raw_byline)
        .first()
    )
    if existing:
        existing.canonical_names = canonical
        existing.decision = decision
        existing.reason = reason or None
        existing.decided_by = getattr(user, "username", "") or None
        existing.decided_at = timezone.now()
        # Re-decided, so the write to `articles.author` is owed again.
        existing.applied_at = None
        existing.save(using=alias)
        row = existing
    else:
        import uuid

        row = BylineNormalization.objects.using(alias).create(
            id=str(uuid.uuid4()),
            dataset_id=dataset_id,
            raw_byline=raw_byline,
            canonical_names=canonical,
            decision=decision,
            reason=reason or None,
            decided_by=getattr(user, "username", "") or None,
            decided_at=timezone.now(),
        )
    BylineReviewCandidate.objects.using(alias).filter(
        dataset_id=dataset_id, raw_byline=raw_byline
    ).delete()
    return row


def decided(dataset_id, limit=50):
    """What has been decided, newest first -- the page's receipt."""
    from explorer.models import BylineNormalization

    return (
        BylineNormalization.objects.using("crawler")
        .filter(dataset_id=dataset_id)
        .order_by("-decided_at")[:limit]
    )


# ---------------------------------------------------------------------------
# The two reports the review exists to produce.
#
# LOCAL_STATUSES is what "local news" means here: an article that reached
# enrichment. A byline on a wire story, a paywall stub or a page that was never
# an article is not a local reporter's byline, and counting it inflates every
# number on both reports.
# ---------------------------------------------------------------------------

LOCAL_STATUSES = ("enriched", "enrichment_skipped")


def _author_rows(dataset_id, statuses=LOCAL_STATUSES):
    """`(author, host, owner)` for the dataset's local articles that have one.

    `.values()` rather than model instances: the reports read three columns off
    a few thousand rows and building an Article for each is the whole cost.
    """
    from explorer.models import Article

    return (
        Article.objects.using("crawler")
        .filter(dataset_id=dataset_id, status__in=statuses)
        .exclude(author__isnull=True)
        .exclude(author="")
        .values_list(
            "author",
            "candidate_link__source__host",
            "candidate_link__source__owner",
        )
    )


def _resolve(raw, decisions):
    """The people a raw byline names, after what a reviewer decided about it.

    A string that is not a real name contributes to neither report -- which is
    what saying so was for. An undecided string is split on the separators
    the crawler writes, so co-authors count as the people they are.
    """
    if raw in decisions:
        return list(decisions[raw])
    parts = []
    for piece in str(raw).replace(" and ", ", ").split(","):
        name = " ".join(piece.split())
        if name:
            parts.append(name)
    return parts


def decisions_map(dataset_id):
    """`{raw string: [canonical names]}` for one dataset. Dropped -> `[]`."""
    from explorer.models import BylineNormalization

    rows = BylineNormalization.objects.using("crawler").filter(dataset_id=dataset_id)
    return {r.raw_byline: (r.canonical_names or []) for r in rows}


def bylines_with_hosts(dataset_id, statuses=LOCAL_STATUSES):
    """Unique local bylines, and the hosts each one appears on.

    More than one host is not a defect: a stringer files to several papers, and
    papers under one owner share copy. The report says which hosts, and the
    reader judges it.
    """
    decisions = decisions_map(dataset_id)
    found = {}
    for raw, host, owner in _author_rows(dataset_id, statuses):
        for name in _resolve(raw, decisions):
            row = found.setdefault(
                name, {"byline": name, "articles": 0, "hosts": set(), "owners": set()}
            )
            row["articles"] += 1
            if host:
                row["hosts"].add(host)
            if owner:
                row["owners"].add(owner)
    rows = []
    for row in found.values():
        row["hosts"] = sorted(row["hosts"])
        row["owners"] = sorted(row["owners"])
        rows.append(row)
    rows.sort(key=lambda r: (-r["articles"], r["byline"].lower()))
    return rows


def hosts_with_bylines(dataset_id, statuses=LOCAL_STATUSES):
    """Unique hosts, and how many distinct local bylines each one carries.

    The count is of PEOPLE, not of byline strings: two spellings of one
    reporter are one byline here only because the review collapsed them, which
    is the whole reason this report waits on the queue being worked.
    """
    decisions = decisions_map(dataset_id)
    found = {}
    for raw, host, owner in _author_rows(dataset_id, statuses):
        if not host:
            continue
        row = found.setdefault(
            host, {"host": host, "owner": owner or "", "articles": 0, "names": set()}
        )
        row["articles"] += 1
        row["names"].update(_resolve(raw, decisions))
    rows = []
    for row in found.values():
        row["bylines"] = len(row["names"])
        row["examples"] = sorted(row["names"])[:3]
        row.pop("names")
        rows.append(row)
    rows.sort(key=lambda r: (-r["bylines"], r["host"]))
    return rows


# ---------------------------------------------------------------------------
# Reading the evidence, and excluding a byline that is not local reporting.
#
# `cross_owner` is 96 of Mizzou's 148 candidates and is the softest signal: a
# byline appearing under unrelated owners is legitimate for a stringer and for
# papers sharing copy, and a defect for a wire reporter the parser credited as
# local. Nothing on the row distinguishes those, so the reviewer has to read a
# couple of the stories -- which means the stories have to be on the page.
# ---------------------------------------------------------------------------

#: The excluded byline's stories are RE-DISPOSED, not deleted, and with the
#: extraction queue's own vocabulary rather than a second list beside it. Those
#: two lists drifted once before -- extraction offered one word for seventeen
#: things discovery could name -- and the reviewer's answer here ("these are
#: wire stories") is the same answer the extraction queue already takes.
#:
#: What is left out: `news` and the bad-capture type. Both mean "this IS a story
#: we keep", which is the opposite of excluding the byline, and offering them
#: here would let one dropdown both exclude and restore.
_NOT_EXCLUSIONS = {"news"}


def exclusion_types():
    """The dispositions a byline can be excluded as, in the queue's own words."""
    from review.dispositions import BAD_CAPTURE, CONTENT_TYPES

    return [
        t
        for t in CONTENT_TYPES
        if t["value"] not in _NOT_EXCLUSIONS and t["value"] != BAD_CAPTURE
    ]


CLUSTER = "cluster"
DECISION_LABELS[CLUSTER] = "settled with its other spellings"

REPLACE = "replace"
DECISION_LABELS[REPLACE] = "replaced on some of its stories"

EXCLUDE = "exclude"
DECISION_LABELS[EXCLUDE] = "excluded, with its stories re-disposed"


def exclude(dataset_id, raw_byline, content_type, user, reason=""):
    """Take the byline out of local reporting, and its stories with it.

    Two writes, because the byline and the stories are two different claims: the
    articles are re-disposed to what the reviewer says they are (wire, an
    obituary, a section front), and the byline is recorded as naming nobody so
    neither report counts it.

    The article write goes through `dispositions.record`, the same call the
    extraction queue makes, so an article excluded here carries the same
    decision note, the same status and the same `ReviewDecision` row as one
    dispositioned one at a time. A second implementation would be a second set
    of rules about the same column.

    Returns how many articles it re-disposed.
    """
    from explorer.models import Article
    from review.dispositions import REJECT, TYPE_BECOMES, record, stage_of

    if content_type not in TYPE_BECOMES:
        raise ValueError(f"not a disposition: {content_type!r}")
    alias = write_alias()

    # Every article carrying the string, whatever its status: a byline being
    # excluded is a statement about the byline, and stopping at the local
    # statuses would leave the same wire stories at `labeled` to be enriched
    # next week and come back.
    articles = list(
        Article.objects.using(alias).filter(dataset_id=dataset_id, author=raw_byline)
    )
    # ONE transaction over both writes. They are one answer: articles
    # re-disposed with no decision recorded would be asked about again, and a
    # decision recorded over articles that were not re-disposed would be a
    # ruling the corpus never received.
    with transaction.atomic(using=alias):
        for article in articles:
            record(
                article,
                decision=REJECT,
                stage=stage_of(article),
                user=user,
                content_type=content_type,
                reason=reason or f"byline excluded: {raw_byline}",
                label=(article.title or "")[:300],
            )

        row = decide(
            dataset_id, raw_byline, EXCLUDE, [], user, reason=reason or content_type
        )
        # STAMPED APPLIED HERE, unlike every other decision.
        #
        # The crawler's nightly `apply-byline-decisions` writes a decision's
        # names onto `articles.author`, and an exclusion carries no name -- so left
        # unapplied it would blank the byline on every one of these stories. The
        # byline is not wrong: a wire reporter really wrote the wire story, and
        # the answer this decision records is about the STORIES, which the loop
        # above already carried out.
        row.applied_at = timezone.now()
        row.articles_updated = len(articles)
        row.save(using=alias, update_fields=["applied_at", "articles_updated"])
    return len(articles)


# ---------------------------------------------------------------------------
# One story's byline, when the string is right on the rest of them.
#
# Christopher Replogle has 896 stories on ky3.com and one on
# unterrifieddemocrat.com whose page reads "By Neal A. Johnson, UD Editor".
# Neither decision on the string is right for that: excluding throws out 896
# genuine stories, and fixing the string renames them. The correction belongs to
# the one story, so it is made on the one story.
# ---------------------------------------------------------------------------


def replace_on(dataset_id, raw_byline, article_ids, new_byline, user, reason=""):
    """Write a different byline onto the stories the reviewer picked.

    NOT a normalization. A normalization says what the string means everywhere,
    and the case this answers is the opposite: the string is right on 896 stories
    and wrong on one, so only the one is written and the string keeps its
    meaning. Nothing is recorded against the byline, and the candidate stays in
    the queue until somebody decides the string itself.

    Through `audited_update`, the same write the extraction queue's field edit
    makes, so a byline corrected in bulk here is revertible exactly like one
    corrected on its own article page.
    """
    from explorer.models import Article
    from review.services import audited_update

    new_byline = " ".join((new_byline or "").split())
    if not new_byline:
        raise ValueError("A replacement needs a name")

    # A story the string names, alone or beside a co-author: "Rudi Keller, Steph
    # Quinn" is a story of Steph Quinn's whose byline may be wrong. Read on the
    # alias they are about to be written on, so the rows checked against the
    # byline are the rows updated.
    named = {str(row["id"]) for row in articles_naming(dataset_id, raw_byline)}
    articles = list(
        Article.objects.using(write_alias()).filter(
            dataset_id=dataset_id,
            id__in=[i for i in article_ids if str(i) in named],
        )
    )
    if not articles:
        raise ValueError("None of those stories carries that byline")
    audited_update(
        user,
        articles,
        {"author": new_byline},
        action="byline:replace",
        reason=reason or f"{raw_byline} was not the byline on these stories",
    )
    return len(articles)


# ---------------------------------------------------------------------------
# A spelling cluster: one question about one person.
#
# The crawler groups the spellings of a name and sends them as `group` -- each
# with its own article count and hosts -- because "Bruce E Stidham" and
# "Bruce E. Stidham" were two rows, and a reviewer answered the same person
# twice and hoped the two answers agreed.
# ---------------------------------------------------------------------------

#: The answer that the spellings are not one person after all.
DIFFERENT = "__different__"


def cluster_spellings(candidate):
    """The spellings in this row's cluster, or `[]` when it is a single name."""
    group = getattr(candidate, "group", None) or []
    names = [entry.get("name") for entry in group if entry.get("name")]
    return names if len(names) > 1 else []


def decide_cluster(dataset_id, spellings, canonical, user, reason=""):
    """Settle every spelling of one name in one answer.

    `canonical` is the spelling that is right, and every other spelling is
    recorded as meaning it -- so the corpus ends up with one name and the
    decisions survive a re-extraction that writes an old spelling again.

    `DIFFERENT` says they are not one person: each spelling is accepted as
    itself, which takes them all off the queue without renaming anybody. That is
    a real answer and has to be recordable, or a reviewer who sees two people
    has no way to say so and the pair is offered again every night.

    Returns how many spellings were decided.
    """
    spellings = [name for name in spellings if name]
    if not spellings:
        raise ValueError("A cluster needs its spellings")

    # ONE TRANSACTION over every spelling. A cluster half-decided is worse than
    # one not decided at all: the queue would show the spellings that failed
    # while the corpus already carried the rename.
    with transaction.atomic(using=write_alias()):
        return _decide_cluster(dataset_id, spellings, canonical, user, reason)


def _decide_cluster(dataset_id, spellings, canonical, user, reason):
    """The body of `decide_cluster`, inside its transaction."""

    if canonical == DIFFERENT:
        for name in spellings:
            decide(dataset_id, name, ACCEPT, [name], user, reason=reason)
        return len(spellings)

    if canonical not in spellings:
        # Not one of the offered spellings: a stale form, or a hand-made
        # request. Refused rather than written, because the names being merged
        # are what the answer means.
        raise ValueError("Pick one of the spellings")

    for name in spellings:
        if name == canonical:
            # Right as it stands. Recorded anyway, so the queue stops asking.
            decide(dataset_id, name, ACCEPT, [name], user, reason=reason)
        else:
            decide(dataset_id, name, FIX, [canonical], user, reason=reason)
    return len(spellings)


# ---------------------------------------------------------------------------
# Which newsroom is wrong.
#
# `cross_owner` is 119 of Mizzou's 138 rows and is the softest signal the queue
# has: a byline under unrelated owners is legitimate for a stringer and for
# papers sharing copy. The row used to say only that the condition held, and the
# stories that could settle it were two disclosures deep -- "Show every story"
# sat inside a collapsed "Read 8", and the one outlying story was one unlabelled
# row among eight spread two-per-host.
# ---------------------------------------------------------------------------


def articles_naming_any(dataset_id, names):
    """`{name: [story, ...]}`: every story whose byline names each person.

    Co-authored stories included, and read in ONE query for however many names
    are asked about. The queue page used to ask per name, twice, and an
    unindexed `author LIKE '%name%'` is a scan of the whole articles table:
    25 rows was 50 scans, every time a decision redirected back to the page.

    A `contains` match is the only way to reach "Rudi Keller, Steph Quinn" from
    "Steph Quinn", and on its own it would also reach "Dan Fox" from "Dan". So
    the parts are checked properly afterwards: the database narrows, Python
    decides.
    """
    from django.db.models import Q

    from explorer.models import Article

    names = list(dict.fromkeys(name for name in names if name))
    found: dict[str, list[dict]] = {name: [] for name in names}
    if not names:
        return found
    narrowing = Q()
    for name in names:
        narrowing |= Q(author__contains=name)
    wanted = {_norm(name): name for name in names}
    rows = (
        Article.objects.using("crawler")
        .filter(narrowing, dataset_id=dataset_id)
        .values(
            "id",
            "author",
            "url",
            "title",
            "status",
            "publish_date",
            "candidate_link__source__host",
            "candidate_link__source__owner",
        )
    )
    for row in rows:
        parts = {_norm(part) for part in _SEPARATORS.split(row["author"] or "")}
        for norm in parts & wanted.keys():
            found[wanted[norm]].append(row)
    return found


def articles_naming(dataset_id, name):
    """Every story whose byline names this person, co-authored ones included."""
    return articles_naming_any(dataset_id, [name])[name]


# ---------------------------------------------------------------------------
# One newsroom is the reporter's; the rest are republishing.
#
# Steph Quinn has 112 stories on missouriindependent.com, where she works, and
# 288 across 41 other domains -- Sedalia, Jefferson City, Warrensburg, Joplin,
# public radio, Sinclair -- because States Newsroom copy is syndicated across
# Missouri. Almost all of those 288 are already `wire`; about ten are not, and
# two of those reached the local export as her local reporting.
#
# Across the queue that is 125 bylines, 12,179 stories on a non-primary host,
# 8,031 of them not yet wire.
#
# The reviewer says which newsroom is hers. That is not something the data can
# settle: the same shape -- one big host and many small ones -- is a syndicated
# staff writer AND a stringer who files everywhere, and only somebody who knows
# the publication can tell them apart.
# ---------------------------------------------------------------------------

PRIMARY = "primary"
DECISION_LABELS[PRIMARY] = "newsrooms ruled one at a time"

#: What a newsroom's select means when nothing is chosen for it: leave those
#: stories alone. The default everywhere, because a ruling that excludes by
#: default is a blanket ruling with extra steps -- one careless submit on a
#: byline with 42 newsrooms would re-dispose all of them.
KEEP = ""


#: How many of a newsroom's stories to offer. Enough to read what the
#: newsroom does with this byline; a drawer of hundreds is one nobody opens.
SAMPLE_PER_HOST = 5


def host_choices(candidate, stories=None, per_host=SAMPLE_PER_HOST):
    """`[{host, articles, owner, samples}]` for the newsrooms this byline is on.

    Biggest first, which is usually the reporter's own -- but the order is a
    reading aid, not a ruling. Every newsroom is decided on its own.

    `samples` are that newsroom's own stories, newest first: the drawer under
    the newsroom, so the reviewer reads what THIS newsroom published before
    ruling on it. `printed` is the name the page itself carries where the
    crawler found one, the only provable answer to "whose story is this".

    `stories` is what `articles_naming_any` already read, so a page of rows is
    one query and not one per row.
    """
    if stories is None:
        stories = articles_naming(candidate.dataset_id, candidate.raw_byline)
    printed = {
        str(story.get("article_id")): story.get("printed")
        for story in (candidate.mismatches or [])
        if story.get("article_id")
    }
    # The owner comes from the same rows as the counts. The candidate's `hosts`
    # and `owners` are two independent lists, so pairing them by position would
    # caption a newsroom with somebody else's owner.
    hosts: dict[str, dict] = {}
    dated = sorted(
        (row for row in stories if row["publish_date"]),
        key=lambda row: row["publish_date"],
        reverse=True,
    )
    undated = [row for row in stories if not row["publish_date"]]
    for row in dated + undated:
        host = row["candidate_link__source__host"]
        if not host:
            continue
        entry = hosts.setdefault(
            host,
            {
                "host": host,
                "articles": 0,
                "owner": row["candidate_link__source__owner"] or "",
                "samples": [],
            },
        )
        entry["articles"] += 1
        if len(entry["samples"]) < per_host:
            entry["samples"].append(
                {
                    "id": row["id"],
                    "url": row["url"],
                    "title": row["title"] or row["url"],
                    "status": row["status"],
                    "publish_date": row["publish_date"],
                    "author": row["author"],
                    "printed": printed.get(str(row["id"]), ""),
                }
            )
    return sorted(hosts.values(), key=lambda row: (-row["articles"], row["host"]))


def rule_newsrooms(
    dataset_id,
    raw_byline,
    rulings,
    user,
    reason="",
    reasons=None,
    settle=True,
    credit=False,
):
    """Decide each newsroom carrying this byline, one at a time.

    `rulings` maps a host to what its stories are: a disposition from the
    extraction queue's list, or `KEEP` to leave them alone. NOT a primary and a
    blanket for the rest -- Steph Quinn's 41 other domains are not all the same
    thing, and a reviewer who knows the state can say that Sedalia republishes
    while Columbia has her filing directly.

    THE BYLINE ITSELF IS ACCEPTED, not dropped. She is a real reporter with a
    real name; the ruling is about which stories are local reporting.

    A story already carrying the disposition is skipped rather than written
    again, so a second pass over a byline costs nothing and the count reported is
    of what actually changed.

    `reasons` maps a host to why, and wins over `reason` for that host: each
    newsroom is its own ruling and carries its own reason.

    `settle=False` leaves the byline undecided, for a caller that is deciding the
    string itself in the same submit.

    `credit=True` ALSO RECORDS WHERE THE WIRE COPIES CAME FROM. A wire ruling
    is otherwise subtractive -- the copies leave the export and both reports,
    and the newsroom whose work they are gets nothing. With the box ticked,
    every story ruled `wire` in this submission takes the newsroom left at
    `local reporting` as its origin.

    Only `wire`. An obituary or a section front is not somebody else's
    reporting; it is not reporting.

    IT NEEDS EXACTLY ONE NEWSROOM LEFT. Two unruled newsrooms is not "a home
    and some outliers", it is an unfinished judgement -- Sherman Smith has
    three Missouri newsrooms left and works for none of them -- so the credit
    is skipped rather than guessed at, and `credited` comes back 0.

    Returns `{"stories": n, "hosts": n, "kept": n, "credited": n}`.
    """
    from explorer.models import Article
    from review.dispositions import REJECT, TYPE_BECOMES, record, stage_of

    rows = articles_naming(dataset_id, raw_byline)
    carried = {row["candidate_link__source__host"] for row in rows}
    decided = {
        host: value for host, value in (rulings or {}).items() if host in carried
    }
    for host, value in decided.items():
        if value != KEEP and value not in TYPE_BECOMES:
            raise ValueError(f"Not a disposition for {host}: {value!r}")
    if not decided:
        raise ValueError("None of those newsrooms carries this byline")

    wanted = []
    for row in rows:
        value = decided.get(row["candidate_link__source__host"], KEEP)
        if value == KEEP or row["status"] == TYPE_BECOMES[value]:
            continue
        wanted.append((row, value))

    alias = write_alias()
    with transaction.atomic(using=alias):
        by_id = {
            str(article.id): article
            for article in Article.objects.using(alias).filter(
                id__in=[row["id"] for row, _ in wanted]
            )
        }
        for row, value in wanted:
            article = by_id.get(str(row["id"]))
            if article is None:
                continue
            record(
                article,
                decision=REJECT,
                stage=stage_of(article),
                user=user,
                content_type=value,
                reason=(reasons or {}).get(row["candidate_link__source__host"])
                or reason
                or f"{raw_byline} on {row['candidate_link__source__host']}",
                label=(article.title or "")[:300],
            )
        credited = _credit_the_home_newsroom(rows, decided, carried, alias, credit)
        if settle:
            decide(dataset_id, raw_byline, ACCEPT, [raw_byline], user, reason=reason)
    return {
        "stories": len(wanted),
        "hosts": len({host for host, v in decided.items() if v != KEEP}),
        "kept": len([h for h, v in decided.items() if v == KEEP]),
        "credited": credited,
    }


def _credit_the_home_newsroom(rows, decided, carried, alias, credit):
    """Write the origin onto every copy this submission ruled `wire`.

    The home newsroom is the one carrying this byline that the reviewer LEFT
    ALONE. It is never named directly -- the form only records the rulings --
    so it is what is left after the ruled hosts are taken out.

    Exactly one, or nothing happens. Two newsrooms left is an unfinished
    judgement rather than a home and its outliers, and guessing by article
    count picks the wrong one: Sherman Smith's largest Missouri footprint is
    the newsroom that republishes him eighteen times over, not his employer,
    who is in Kansas and not in this table at all.

    A NEWSROOM DOES NOT SYNDICATE TO ITSELF. The home host is excluded
    explicitly, not left to the fact that it was never ruled -- the 16
    `/repub/` national roundups sitting at `wire` on the Missouri Independent
    are precisely the rows that would otherwise be credited to the Independent,
    on the Independent.
    """
    if not credit:
        return 0
    from explorer.models import Article, Source

    left = sorted(carried - set(decided))
    if len(left) != 1:
        return 0
    home_host = left[0]
    home = Source.objects.using(alias).filter(host=home_host).values("id").first()
    if not home:
        return 0

    wired = [
        row["id"]
        for row in rows
        if decided.get(row["candidate_link__source__host"]) == "wire"
        and row["candidate_link__source__host"] != home_host
    ]
    if not wired:
        return 0
    return (
        Article.objects.using(alias)
        .filter(id__in=wired)
        .update(syndicated_from_source_id=home["id"])
    )


# ---------------------------------------------------------------------------
# One submit for the page.
#
# The queue is worked 25 rows at a time. Every field on every row is a proposal
# and nothing is written until the reviewer submits at the bottom, which then
# disposes of everything on the page at once. The old page had a submit per
# control -- a decision, a newsroom ruling, an outlier replacement, a cluster --
# and each one redirected back to a page that re-read the whole queue.
#
# Field names carry the row's position on the page (`decision-3`) because a
# string cannot be a field name and two rows can share a host. What a row holds:
#
#   decision-N       "" | accept | fix | drop | exclude (with content_type-N)
#   names-N          the names, for `fix`
#   canonical-N      "" | a spelling | DIFFERENT, for a cluster (`spelling-N`)
#   reason-N         why, for the string
#   host-N           every newsroom shown; ruling-N-<host> and why-N-<host>
#   edit-N-<id>      a different byline for that one story
#   use-N-<id>       the name the page prints, ticked to use it for that story
#
# Blank is "leave it alone" everywhere, so a submit with nothing touched changes
# nothing, and a row nobody touched stays in the queue.
# ---------------------------------------------------------------------------


class PageError(ValueError):
    """Everything wrong with a submit, at once, so it is fixed in one pass."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def read_page(post):
    """The submit as one instruction per touched row. Writes nothing.

    Validated in full BEFORE anything is applied: a page with one bad row is
    refused whole. Applying the other 24 and refusing one would leave the
    reviewer to work out which is which.
    """
    from review.dispositions import TYPE_BECOMES

    instructions, problems = [], []
    for index in post.getlist("row"):
        raw = post.get(f"raw-{index}", "")
        if not raw:
            continue
        problem = problems.append
        decision = post.get(f"decision-{index}", "")
        content_type = ""
        if decision == EXCLUDE:
            content_type = post.get(f"content_type-{index}", "")
            if content_type not in TYPE_BECOMES:
                problem(f"{raw}: say what its stories are")
        elif decision and decision not in (ACCEPT, FIX, DROP):
            problem(f"{raw}: not a decision")

        names = parse_names(post.get(f"names-{index}", ""))
        if decision == FIX and not names:
            # An empty name list is what DROP means: a fix with nothing typed
            # would silently drop a real reporter.
            problem(f"{raw}: type the name or names it should be")

        spellings = [s for s in post.getlist(f"spelling-{index}") if s]
        canonical = post.get(f"canonical-{index}", "")
        if canonical:
            if decision:
                problem(f"{raw}: settle the spellings or decide the string, not both")
            elif canonical != DIFFERENT and canonical not in spellings:
                problem(f"{raw}: pick one of the spellings offered")
            decision = CLUSTER

        rulings, whys = {}, {}
        for host in post.getlist(f"host-{index}"):
            ruling = post.get(f"ruling-{index}-{host}", KEEP)
            if ruling != KEEP and ruling not in TYPE_BECOMES:
                problem(f"{raw}: {host} is not a disposition: {ruling!r}")
            elif ruling != KEEP:
                rulings[host] = ruling
            why = " ".join(post.get(f"why-{index}-{host}", "").split())
            if why:
                whys[host] = why

        # "Give syndication credit": the wire copies this submission rules are
        # somebody's reporting, and this says whose. Only meaningful beside a
        # ruling, so it rides with them rather than as a decision of its own.
        credit = bool(post.get(f"credit-{index}"))

        edits = {}
        prefix = f"edit-{index}-"
        for key in post:
            if key.startswith(prefix):
                name = " ".join(post.get(key, "").split())
                if name:
                    edits[key[len(prefix) :]] = name
        # Ticked "use the page's name" is a name typed for you. What was typed
        # wins where both are given.
        used = f"use-{index}-"
        for key in post:
            if key.startswith(used):
                name = " ".join(post.get(key, "").split())
                if name:
                    edits.setdefault(key[len(used) :], name)

        if not (decision or rulings or edits):
            continue
        instructions.append(
            {
                "raw": raw,
                "decision": decision,
                "content_type": content_type,
                "names": names,
                "canonical": canonical,
                "spellings": spellings,
                "reason": " ".join(post.get(f"reason-{index}", "").split()),
                "rulings": rulings,
                "whys": whys,
                "edits": edits,
                "credit": credit,
            }
        )
    if problems:
        raise PageError(problems)
    return instructions


def apply_page(dataset, instructions, user):
    """Carry out what `read_page` found, in one transaction on the write alias.

    A row's order is: the newsrooms, then the single stories, then the string.
    A row the reviewer touched without deciding its string is ACCEPTED -- the
    name is right, the reviewer read the row and acted on it, and leaving it in
    the queue would make every row worked need a second answer.

    Returns `{"rows", "decisions", "stories", "edited"}`.
    """
    from audit.models import AuditLogEntry

    total = {"rows": 0, "decisions": 0, "stories": 0, "edited": 0, "credited": 0}
    with transaction.atomic(using=write_alias()):
        for row in instructions:
            raw, reason = row["raw"], row["reason"]
            acted = False

            if row["rulings"]:
                result = rule_newsrooms(
                    dataset.id,
                    raw,
                    row["rulings"],
                    user,
                    reason=reason,
                    reasons=row["whys"],
                    settle=False,
                    credit=row["credit"],
                )
                total["stories"] += result["stories"]
                total["credited"] += result["credited"]
                acted = True
                AuditLogEntry.objects.create(
                    actor=user,
                    action="byline:newsrooms",
                    target_table="articles",
                    target_ids=[f"{dataset.slug}:{raw}"],
                    after={
                        "rulings": row["rulings"],
                        "stories": result["stories"],
                        # The home newsroom is never in `rulings` -- it is the
                        # one left alone -- so without this the audit cannot
                        # say who was credited, only that somebody was.
                        "credited": result["credited"],
                    },
                    reason=reason or f"newsrooms ruled for {raw}",
                )

            if row["edits"]:
                host_of = {
                    str(story["id"]): story["candidate_link__source__host"]
                    for story in articles_naming(dataset.id, raw)
                }
                grouped: dict[tuple[str, str], list[str]] = {}
                for article_id, name in row["edits"].items():
                    why = row["whys"].get(host_of.get(article_id, ""), "") or reason
                    grouped.setdefault((name, why), []).append(article_id)
                for (name, why), ids in grouped.items():
                    total["edited"] += replace_on(
                        dataset.id, raw, ids, name, user, reason=why
                    )
                acted = True

            decision = row["decision"]
            if not decision and acted:
                decision, row["names"] = ACCEPT, [raw]

            if decision == CLUSTER:
                decide_cluster(
                    dataset.id, row["spellings"], row["canonical"], user, reason
                )
            elif decision == EXCLUDE:
                written = exclude(
                    dataset.id, raw, row["content_type"], user, reason=reason
                )
                total["stories"] += written
                AuditLogEntry.objects.create(
                    actor=user,
                    action="byline:exclude",
                    target_table="articles",
                    target_ids=[f"{dataset.slug}:{raw}"],
                    after={"content_type": row["content_type"], "articles": written},
                    reason=reason or f"{raw} is not local reporting",
                )
            elif decision:
                # Keep-as-is names the string itself unless names were typed.
                names = row["names"] or ([raw] if decision == ACCEPT else [])
                settled = decide(dataset.id, raw, decision, names, user, reason)
                AuditLogEntry.objects.create(
                    actor=user,
                    action="byline:decide",
                    target_table="byline_normalizations",
                    target_ids=[f"{dataset.slug}:{raw}"],
                    after={"decision": decision, "names": settled.canonical_names},
                    reason=reason or f"{raw} — {decision}",
                )
            if decision:
                total["decisions"] += 1
            total["rows"] += 1
    return total
