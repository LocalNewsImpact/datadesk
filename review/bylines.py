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

from django.db import transaction
from django.utils import timezone

#: The decisions a reviewer can take on a string.
ACCEPT = "accept"  # the string is already right
FIX = "fix"  # these are the people it names
DROP = "drop"  # it names nobody: a desk, a bot, an address

DECISION_LABELS = {
    ACCEPT: "accepted as it stands",
    FIX: "fixed",
    DROP: "dropped: names nobody",
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


@transaction.atomic(using="crawler")
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
    from explorer.models import BylineNormalization, BylineReviewCandidate

    if decision not in DECISION_LABELS:
        raise ValueError(f"not a decision: {decision!r}")
    canonical = [] if decision == DROP else list(names)

    existing = (
        BylineNormalization.objects.using("crawler")
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
        existing.save(using="crawler")
        row = existing
    else:
        import uuid

        row = BylineNormalization.objects.using("crawler").create(
            id=str(uuid.uuid4()),
            dataset_id=dataset_id,
            raw_byline=raw_byline,
            canonical_names=canonical,
            decision=decision,
            reason=reason or None,
            decided_by=getattr(user, "username", "") or None,
            decided_at=timezone.now(),
        )
    BylineReviewCandidate.objects.using("crawler").filter(
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

    A dropped string names nobody, so it contributes to neither report -- which
    is what dropping it was for. An undecided string is split on the separators
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

#: How many stories to offer per host. Two is enough to tell a stringer from a
#: wire feed, and a row carrying forty links is a row nobody reads.
PER_HOST = 2


def sample_articles(dataset_id, raw_byline, per_host=PER_HOST, statuses=LOCAL_STATUSES):
    """A few of this byline's stories, spread across the hosts it appears on.

    Spread deliberately: the question a cross-owner row asks is whether the
    same person really writes for both papers, and a sample that happens to
    come from one of them cannot answer it.
    """
    from explorer.models import Article

    rows = (
        Article.objects.using("crawler")
        .filter(dataset_id=dataset_id, author=raw_byline, status__in=statuses)
        .order_by("-publish_date")
        .values(
            "id",
            "url",
            "title",
            "publish_date",
            "candidate_link__source__host",
            "candidate_link__source__owner",
        )[:200]
    )
    seen: dict[str, int] = {}
    out = []
    for row in rows:
        host = row["candidate_link__source__host"] or ""
        if seen.get(host, 0) >= per_host:
            continue
        seen[host] = seen.get(host, 0) + 1
        out.append(
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"] or row["url"],
                "publish_date": row["publish_date"],
                "host": host,
                "owner": row["candidate_link__source__owner"] or "",
            }
        )
    out.sort(key=lambda r: (r["host"], r["publish_date"] or ""))
    return out


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


REPLACE = "replace"
DECISION_LABELS[REPLACE] = "replaced on some of its stories"

EXCLUDE = "exclude"
DECISION_LABELS[EXCLUDE] = "excluded, with its stories re-disposed"


@transaction.atomic(using="crawler")
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

    # Every article carrying the string, whatever its status: a byline being
    # excluded is a statement about the byline, and stopping at the local
    # statuses would leave the same wire stories at `labeled` to be enriched
    # next week and come back.
    articles = list(
        Article.objects.using("crawler").filter(
            dataset_id=dataset_id, author=raw_byline
        )
    )
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
    # The crawler's nightly `apply-byline-decisions` writes a decision's names
    # onto `articles.author`, and an exclusion names nobody -- so left unapplied
    # it would blank the byline on every one of these stories. The byline is not
    # wrong: a wire reporter really wrote the wire story, and the answer this
    # decision records is about the STORIES, which the loop above already
    # carried out.
    row.applied_at = timezone.now()
    row.articles_updated = len(articles)
    row.save(using="crawler", update_fields=["applied_at", "articles_updated"])
    return len(articles)


# ---------------------------------------------------------------------------
# The whole spread, and a replacement across the part of it that is wrong.
#
# The sample answers "is this one person"; it cannot answer "which of these 19
# stories are not his". Christopher Replogle has 896 on ky3.com and one on
# unterrifieddemocrat.com whose page reads "By Neal A. Johnson, UD Editor" --
# so the fix is a replacement on the outliers, and the reviewer has to see all
# of them to pick.
# ---------------------------------------------------------------------------


def every_article(dataset_id, raw_byline):
    """Every story carrying the string, outlying hosts first.

    ALL statuses, not the local ones: a wrong byline is wrong on a story nobody
    has enriched yet too, and leaving those behind means the same correction
    comes back the week they are enriched.

    `outlier` marks a host that is not the byline's main one. That is the whole
    question a cross-owner row asks -- which of these does not belong -- so the
    grouping answers it rather than leaving the reviewer to count rows.
    """
    from explorer.models import Article

    rows = list(
        Article.objects.using("crawler")
        .filter(dataset_id=dataset_id, author=raw_byline)
        .order_by("-publish_date")
        .values(
            "id",
            "url",
            "title",
            "status",
            "publish_date",
            "candidate_link__source__host",
            "candidate_link__source__owner",
        )
    )
    counts: dict[str, int] = {}
    for row in rows:
        host = row["candidate_link__source__host"] or ""
        counts[host] = counts.get(host, 0) + 1
    # The main host is where most of the byline's work is. Ties leave both
    # unmarked: two hosts with equal counts is a stringer, not an outlier.
    biggest = max(counts.values()) if counts else 0
    main = {host for host, n in counts.items() if n == biggest}

    groups: dict[str, dict] = {}
    for row in rows:
        host = row["candidate_link__source__host"] or ""
        group = groups.setdefault(
            host,
            {
                "host": host,
                "owner": row["candidate_link__source__owner"] or "",
                "outlier": host not in main,
                "articles": [],
            },
        )
        group["articles"].append(
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"] or row["url"],
                "status": row["status"],
                "publish_date": row["publish_date"],
            }
        )
    out = sorted(
        groups.values(),
        key=lambda g: (not g["outlier"], -len(g["articles"]), g["host"]),
    )
    return out


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

    articles = list(
        Article.objects.using("crawler").filter(
            dataset_id=dataset_id, author=raw_byline, id__in=list(article_ids)
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
