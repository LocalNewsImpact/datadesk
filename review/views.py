"""Review and cleanup views (SCOPE.md §2.2). Editor role throughout."""

import io
from datetime import UTC, datetime

from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import DatabaseError, connections
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from accounts.decorators import APP, requires, requires_admin, requires_import
from accounts.privileges import CLASSIFY, EXPORT_PRIVILEGE, WRITE
from audit.models import AuditLogEntry
from explorer.models import Article, ArticleEnrichment
from explorer.scoping import scopes_for
from explorer.views import _filtered_articles
from review import audit_entries
from review import kernel as _kernel
from review import queue as review_queue
from review.exports import EXPORT_COLUMNS, csv_response
from review.imports import (
    TARGETS,
    ImportError_,
    compute_diff,
    guess_key_column,
    guess_target,
    importable_fields,
    parse_csv,
)
from review.models import ExportDefinition, ImportBatch
from review.services import (
    BoundaryViolation,
    audited_update,
    audited_update_rows,
    repair_text,
    revert,
)

# The inline-editable cleaned-text columns (SCOPE.md §1: they change only
# through explicit, audited human actions — this is that path).
TEXT_FIELDS = ("author", "title", "content")

# The queue is browsed, not paged through: a smaller page keeps the text
# lengths and reasons on one screen.
QUEUE_PAGE_SIZE = 50

#: The extraction queue's filters, kept between visits. Everything the
#: queue reads from the query string except `page`: coming back to page
#: seven of a queue that has been worked since is not where anybody was.
QUEUE_FILTER_KEYS = (
    "days",
    # The bounds of a custom range. Without them `days=custom` is
    # remembered on its own, and `_between_two_dates` reads a range with
    # no bounds as the whole corpus -- so returning to the queue silently
    # dropped the date filter. That was survivable while the flagged set
    # was small; it is 60,408 rows now that the machine exclusions have
    # cases, and the page stops answering.
    "since",
    "until",
    "dataset",
    "case",
    "band",
    "skip",
    "service",
    "method",
    "label",
    "byline",
    "publisher",
    "state",
    "all",
)

QUEUE_FILTERS = "queue_filters"


def _get_article(article_id):
    article = Article.objects.filter(id=article_id).first()
    if article is None:
        raise Http404("No such article")
    return article


@requires(WRITE)
def edit_field(request, article_id, field):
    """Inline edit with mojibake preview: the form shows ftfy's repair of
    the stored value before anything is applied (SCOPE.md §2.2)."""
    if field not in TEXT_FIELDS:
        raise Http404("Not an editable field")
    article = _get_article(article_id)

    if request.method == "POST":
        if request.POST.get("use_repaired"):
            value = repair_text(getattr(article, field) or "")
        else:
            value = request.POST.get("value", "")
        reason = request.POST.get("reason", "")
        audited_update(
            request.user,
            [article],
            {field: value},
            action=f"edit:{field}",
            reason=reason,
        )
        return redirect("explorer:article_detail", article.id)

    current = getattr(article, field) or ""
    repaired = repair_text(current)
    return render(
        request,
        "review/edit_field.html",
        {
            "article": article,
            "field": field,
            "current": current,
            "repaired": repaired if repaired != current else None,
        },
    )


@requires(WRITE)
@require_POST
def bulk_disposition(request):
    """Bulk dispositions with recorded reasons (SCOPE.md §2.2), mirroring
    the enrichment status machine — the form offers only statuses the
    data already knows."""
    ids = request.POST.getlist("ids")
    if not ids:
        return HttpResponseBadRequest("No articles selected")
    disposition = request.POST.get("disposition")
    reason = request.POST.get("reason", "").strip()
    articles = list(Article.objects.filter(id__in=ids))
    if not articles:
        return HttpResponseBadRequest("No matching articles")

    if disposition == "out_of_scope":
        if not reason:
            return HttpResponseBadRequest("A reason is required")
        audited_update(
            request.user,
            articles,
            {"status": "out_of_scope"},
            action="disposition:out_of_scope",
            reason=reason,
        )
        enrichment = list(ArticleEnrichment.objects.filter(article_id__in=ids))
        if enrichment:
            audited_update(
                request.user,
                enrichment,
                {"skip_reason": reason},
                action="disposition:skip_reason",
                reason=reason,
            )
    elif disposition == "wire":
        wire_status = request.POST.get("wire_status", "").strip()
        if not wire_status:
            return HttpResponseBadRequest("A wire status is required")
        audited_update(
            request.user,
            articles,
            {"wire_check_status": wire_status},
            action="disposition:wire_override",
            reason=reason,
        )
    else:
        return HttpResponseBadRequest("Unknown disposition")

    back = request.POST.get("next") or "/explorer/articles/"
    if not back.startswith("/"):
        back = "/explorer/articles/"
    return redirect(back)


@requires_admin
def audit_log(request):
    """The audit trail with the revert path (SCOPE.md §2.2: every action
    is reversible from the audit record)."""
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        page_number = 1
    entries = AuditLogEntry.objects.select_related("actor").prefetch_related(
        "reverted_by"
    )
    paginator = Paginator(entries, 50)
    return render(
        request,
        "review/audit_log.html",
        {"page": paginator.get_page(page_number)},
    )


@requires_admin
def audit_entry(request, entry_id):
    """One entry, with what it changed and what reverting it would do.

    Revert used to be a button on a list row showing an action name and
    sixty characters of reason. Nothing there says whether the rows still
    hold what the entry wrote -- and where they do not, a revert discards
    whatever moved them. review/audit_entries.py reads the rows; this
    renders the comparison.
    """
    entry = get_object_or_404(
        AuditLogEntry.objects.select_related("actor", "reverts"), pk=entry_id
    )
    return render(request, "review/audit_entry.html", audit_entries.detail(entry))


@requires(WRITE)
@require_POST
def revert_entry(request, entry_id):
    """Write an entry's recorded values back, as a new entry.

    Returns to the entry rather than to the log: what a revert did is
    read on the page that proposed it, and a failure that the write
    boundary refuses has to be said rather than raised as a 500 --
    the log offered this button on tables `revert()` cannot write.
    """
    entry = get_object_or_404(AuditLogEntry, pk=entry_id)
    try:
        compensating = revert(
            request.user, entry, reason=request.POST.get("reason", "")
        )
    except BoundaryViolation as exc:
        messages.error(request, str(exc))
        return redirect("review:audit_entry", entry_id=entry.pk)
    messages.success(
        request,
        f"Reverted. Entry {compensating.pk} records what was written back.",
    )
    return redirect("review:audit_entry", entry_id=entry.pk)


# --- import (SCOPE.md §2.4: diff report first, then explicit apply) ---------


@requires_import
def import_batches(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            return HttpResponseBadRequest("No file uploaded")
        try:
            columns, rows = parse_csv(upload, upload.name)
        except ImportError_ as exc:
            return render(
                request,
                "review/import_batches.html",
                {"batches": ImportBatch.objects.all(), "error": str(exc)},
                status=400,
            )
        target = request.POST.get("target") or guess_target(columns)
        batch = ImportBatch.objects.create(
            created_by=request.user,
            filename=upload.name,
            target=target,
            columns=columns,
            rows=rows,
            key_column=guess_key_column(columns, target),
        )
        return redirect("review:import_map", batch.pk)
    return render(
        request,
        "review/import_batches.html",
        {"batches": ImportBatch.objects.all()},
    )


@requires_import
def import_map(request, batch_id):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    if batch.status == ImportBatch.APPLIED:
        return redirect("review:import_diff", batch.pk)

    fields = importable_fields(batch.target)
    if request.method == "POST":
        key_column = request.POST.get("key_column", "")
        if key_column not in batch.columns:
            return HttpResponseBadRequest(
                f"Pick the {TARGETS[batch.target]['key_label']} column"
            )
        column_map = {}
        for column in batch.columns:
            field = request.POST.get(f"map_{column}", "")
            if field:
                if field not in fields:
                    return HttpResponseBadRequest(f"{field} is not importable")
                column_map[column] = field
        if not column_map:
            return HttpResponseBadRequest("Map at least one column")
        batch.key_column = key_column
        batch.column_map = column_map
        batch.status = ImportBatch.MAPPED
        batch.save(update_fields=["key_column", "column_map", "status"])
        return redirect("review:import_diff", batch.pk)

    return render(
        request,
        "review/import_map.html",
        {
            "batch": batch,
            "fields": fields,
            "target": TARGETS[batch.target],
            "column_rows": [
                (column, batch.column_map.get(column, "")) for column in batch.columns
            ],
        },
    )


@requires_import
def import_diff(request, batch_id):
    """The diff report — and, on POST, the explicit apply."""
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    if not batch.column_map:
        return redirect("review:import_map", batch.pk)
    diff = compute_diff(batch)

    if request.method == "POST":
        if batch.status == ImportBatch.APPLIED:
            return HttpResponseBadRequest("Batch already applied")
        if not diff["changes"]:
            return HttpResponseBadRequest("Nothing to apply")
        entry = audited_update_rows(
            request.user,
            TARGETS[batch.target]["model"],
            diff["changes"],
            action="import:apply",
            reason=f"import batch {batch.pk}: {batch.filename}",
        )
        batch.status = ImportBatch.APPLIED
        batch.applied_at = timezone.now()
        batch.audit_entry = entry
        batch.save(update_fields=["status", "applied_at", "audit_entry"])
        return redirect("review:import_diff", batch.pk)

    return render(
        request,
        "review/import_diff.html",
        {"batch": batch, "diff": diff},
    )


@requires_import
@require_POST
def import_revert(request, batch_id):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    if batch.status != ImportBatch.APPLIED or batch.audit_entry is None:
        return HttpResponseBadRequest("Batch is not applied")
    revert(
        request.user,
        batch.audit_entry,
        reason=f"revert of import batch {batch.pk}: {batch.filename}",
    )
    batch.status = ImportBatch.REVERTED
    batch.save(update_fields=["status"])
    return redirect("review:import_diff", batch.pk)


# --- export (SCOPE.md §2.4: BOM CSVs, saved definitions) --------------------


@requires(EXPORT_PRIVILEGE)
def export(request):
    """Choose columns for the current filter set; download or save the
    definition for re-running against current data."""
    if request.method == "POST":
        columns = [c for c in request.POST.getlist("columns") if c in EXPORT_COLUMNS]
        if not columns:
            return HttpResponseBadRequest("Pick at least one column")
        # `.lists()`, not `.items()`: a facet chosen more than once arrives
        # as one key with many values, and `.items()` would keep the last.
        # `_filtered_articles` takes a list for those.
        params = {}
        for key, values in request.POST.lists():
            if not key.startswith("f_"):
                continue
            kept = [value for value in values if value]
            if kept:
                params[key[2:]] = kept if len(kept) > 1 else kept[0]
        if request.POST.get("save_as"):
            ExportDefinition.objects.update_or_create(
                name=request.POST["save_as"],
                defaults={
                    "created_by": request.user,
                    "params": params,
                    "columns": columns,
                },
            )
        # Scoped like the grid it mirrors: an export that returned rows
        # from a dataset the reader cannot open would be the worst
        # version of a missing filter, because it leaves the building.
        queryset = _filtered_articles(params, request.user)
        return csv_response(queryset, columns, "datadesk-export.csv")

    return render(
        request,
        "review/export.html",
        {
            "columns": EXPORT_COLUMNS,
            "params": request.GET,
            "definitions": ExportDefinition.objects.all(),
        },
    )


@requires(EXPORT_PRIVILEGE)
def export_run(request, definition_id):
    """Re-run a saved definition against current data."""
    definition = get_object_or_404(ExportDefinition, pk=definition_id)
    # A saved definition is re-run as whoever runs it, not as whoever
    # saved it: the rows follow the reader's grants.
    queryset = _filtered_articles(definition.params, request.user)
    filename = f"{definition.name}.csv".replace("/", "-")
    return csv_response(queryset, definition.columns, filename)


# --- extraction review queue (SCOPE.md §2.3) --------------------------------
#
# Read-only. Any assigned role may look; Phase 2b adds the three
# dispositions as audited writes behind the editor role, in the
# placeholder the template already marks.


def _by_publisher(page):
    """The page's articles grouped by publisher, in the page's own order.

    Order is preserved rather than sorted by name: the queue is ordered
    longest-capture-first for a reason, and regrouping must not quietly
    reorder what a reviewer is being shown.
    """
    groups = []
    index = {}
    for article in page:
        source = getattr(article.candidate_link, "source", None)
        key = str(getattr(source, "id", "")) or "—"
        if key not in index:
            index[key] = {
                "key": key,
                "source": source,
                "label": str(source) if source else "Unknown publisher",
                "articles": [],
            }
            groups.append(index[key])
        index[key]["articles"].append(article)
    return groups


def _receipt_counts(receipt):
    """A receipt with its counts in the order the queue declares its verbs.

    The template renders whatever is here, so a queue that adds a verb
    gets it reported without a template change.
    """
    if not receipt or receipt.get("nothing"):
        return receipt
    from review import kernel

    try:
        queue = kernel.get(receipt.get("queue", ""))
    except LookupError:
        return receipt
    receipt = dict(receipt)
    receipt["counts"] = [
        {"past": verb.past, "n": receipt.get(verb.past, 0)} for verb in queue.verbs
    ]
    return receipt


@requires(WRITE)
def extraction_problems(request):
    """Publishers whose extraction is producing garbage.

    A reviewer saying "this is an article and the body is unusable" is
    answering about one story and reporting about a site: ROT47 that
    never decoded, JavaScript captured instead of prose, a list of
    counties where the story should be. Those come from a parser meeting
    a page shape it does not handle, and the shape belongs to the site.

    Acting on the row is not enough. Without this the only trace is a
    status that says nothing about what was wrong or where it came from.
    """
    from review import extraction_problems as report

    return render(
        request,
        "review/extraction_problems.html",
        {
            "publishers": report.reported(),
            "total": report.total(),
            # What the corpus shows without anybody reporting it.
            "repeated": report.boilerplate_patterns(),
        },
    )


def _without_an_unbounded_range(remembered):
    """Drop `days=custom` when the bounds that give it meaning are absent.

    A custom range with neither bound reads as the whole corpus -- the
    honest reading of a range somebody chose and did not fill in, and a
    disaster to restore somebody INTO. Production measured one such
    restored visit at 254 seconds.

    `since` and `until` are remembered now, so this state is no longer
    created. It is still restored: every session saved before that fix
    holds `days=custom` alone, and its owner is redirected into the hang
    on every visit until something overwrites it. Sessions outlive
    deploys; that is what they are for.

    Dropping `days` falls back to the default window rather than the
    corpus, which is what a reader wants from a range that no longer says
    anything.
    """
    if not remembered:
        return remembered
    if remembered.get("days") != review_queue.CUSTOM:
        return remembered
    if remembered.get("since") or remembered.get("until"):
        return remembered
    return {key: value for key, value in remembered.items() if key != "days"} or None


def _queue_as_it_was_left(request):
    """Keep the queue's filters, and return to them.

    A reviewer working one dataset, one window and one case left the page
    to look something up and came back to the unfiltered queue, having to
    choose all three again. The filters are the working position, so they
    are kept on the session and a bare visit is sent back to them.

    Clearing stays possible because clearing is an explicit act: "Clear
    all", and taking the last facet off, are htmx requests, so an htmx
    request carrying no filters means somebody has just removed them and
    the memory goes with them. A full page load carrying none is arriving
    from somewhere else in the console, which is the case this is for.

    Returns a redirect when there is a remembered position to restore,
    and None when the view should render what was asked for.
    """
    chosen = {
        key: value
        for key, value in request.GET.items()
        if key in QUEUE_FILTER_KEYS and value
    }
    if chosen:
        request.session[QUEUE_FILTERS] = chosen
        return None
    if request.headers.get("HX-Request"):
        request.session.pop(QUEUE_FILTERS, None)
        return None
    remembered = _without_an_unbounded_range(request.session.get(QUEUE_FILTERS))
    if not remembered:
        return None
    return redirect(f"{reverse('review:queue')}?{urlencode(remembered)}")


@requires(WRITE)
def queue(request):
    """Articles automated triage flagged, with what a human needs to judge
    them: captured text length, the reason given, the CIN label, the
    byline (SCOPE.md §2.3)."""
    if request.method == "POST":
        return _submit_queue_decisions(request)

    restored = _queue_as_it_was_left(request)
    if restored is not None:
        return restored

    vocabulary = review_queue.vocab(request.user, request.GET)
    params = request.GET.copy()
    params.pop("page", None)
    # Facet links replace their own dimension rather than appending a
    # second value to it, so each facet builds on the query string with
    # its own key removed.
    case_params = params.copy()
    case_params.pop("case", None)
    band_params = params.copy()
    band_params.pop("band", None)
    state_params = params.copy()
    state_params.pop("state", None)
    context = {
        "crawler_connected": vocabulary is not None,
        "vocab": vocabulary,
        "params": params,
        "case_params": case_params,
        "band_params": band_params,
        "state_params": state_params,
        "state": params.get("state", ""),
        # The window, and what it is when nobody has chosen. A default
        # that narrows the page has to be on the page.
        "day_windows": review_queue.DAY_WINDOWS,
        "default_days": str(review_queue.DEFAULT_DAYS),
        "bands": [],
        "cases": [],
        # What the last submission did. Written to the session by
        # `_submit_queue_decisions` since it was built and never read, so a
        # reviewer submitted a session of decisions and got back a page
        # that looked exactly as it had -- with the rows gone, which is
        # also what a submission that silently did nothing looks like.
        "receipt": _receipt_counts(request.session.pop("queue_receipt", None)),
        # The dock's tallies, one per verb the queue declares. Written by
        # hand it went stale the moment a verb was added, and because the
        # script counted the dock rather than the rows, the stale dock
        # disabled Submit.
        "queue_verbs": _kernel.get("extraction").verbs,
    }

    if vocabulary is not None:
        try:
            page_number = int(request.GET.get("page", "1"))
        except ValueError:
            page_number = 1
        with review_queue.hash_joins_for_the_queue():
            paginator = Paginator(
                review_queue.queued(request.GET, request.user), QUEUE_PAGE_SIZE
            )
            page = paginator.get_page(page_number)
            # Pulled inside the pin: the paginator's count and the page
            # rows are what walk the population.
            rows = list(page.object_list)
            page.object_list = rows
        # Which verbs each row can actually carry out, decided per row
        # rather than in the template: a button that cannot act is worse
        # than no button.
        from review import dispositions, kernel

        extraction_queue = kernel.get("extraction")

        for article in page:
            stage = dispositions.stage_of(
                article,
                enrichment=getattr(article, "enr_present", None),
                labels_updated_at=getattr(article, "labels_updated_at", None),
            )
            article.stage = stage
            article.verbs = dispositions.verbs_for(article, stage)
            article.rewind_to = dispositions.rewind_target(stage) or ""
            # What the template renders: the queue's declared verbs,
            # narrowed to the ones this row can carry out.
            article.offered_verbs = extraction_queue.offered(article)
            # An article held for review whose note cannot be read is
            # stranded: there is nothing to restore it to. Shown as a
            # defect on the row rather than treated as never held, which
            # is how it would go missing without anybody seeing it.
            article.note_problem = dispositions.unreadable_note_reason(article)
            # What this row was flagged AS -- the recorded reason, not the
            # status it ended up with. "Exported unenriched" is where a
            # paywalled stub went; `paywall_stub` is why.
            article.flag, article.flag_hint = review_queue.flag_of(article)
        # Decisions already made, for the rows shown under `state=all`.
        # Read in one query for the page rather than per row.
        decided = dispositions.decisions_for([a.id for a in page])
        for article in page:
            article.decision = decided.get(str(article.id))
        # Clustered by publisher, the way the proposals queue clusters by
        # record: one header, then the rows under it. Fifty separate cards
        # each repeating the same publisher is fifty headings to read past,
        # and the publisher is exactly the thing a reviewer uses to judge
        # a run of them at once.
        context["page"] = page
        context["publishers"] = _by_publisher(page)
        context["exported_statuses"] = dispositions.EXPORTED_STATUSES
        with review_queue.hash_joins_for_the_queue():
            context["bands"] = review_queue.band_facets(request.GET, request.user)
            context["cases"] = review_queue.case_facets(request.GET, request.user)

    # The shared header's shapes. `cases` keeps its own name because the
    # results partial reads it; this is the same list under the name the
    # partial every queue includes expects.
    context["facets"] = [
        dict(
            case,
            href=(
                "?"
                + case_params.urlencode()
                + ("" if case.get("selected") else f"&case={case['key']}")
            ),
        )
        for case in context.get("cases", [])
    ]
    context["facet_label"] = "What was flagged"
    context["datasets"] = (vocabulary or {}).get("datasets", [])
    context["decisions"] = _kernel.get("extraction").verbs

    template = (
        "review/_queue_results.html"
        if request.headers.get("HX-Request")
        else "review/queue.html"
    )
    return render(request, template, context)


def _submit_queue_decisions(request):
    """Apply a session of extraction-queue decisions.

    The rules stay in review/dispositions.py; what runs the session is
    review/submit.py, shared with every other queue. There were two submit
    paths answering the same questions differently -- what counts as a
    decision, what happens to a verb a row can no longer carry out, what a
    person is told afterwards -- and the shorter one was shorter mostly by
    not doing what the longer one had learned to do.
    """
    from review import dispositions, kernel
    from review import submit as review_submit

    queue = kernel.get("extraction")
    decisions = review_submit.posted(request.POST)

    # Narrowed to what this person may act on. The narrowing IS the access
    # check; the submit path counts what falls outside it rather than
    # repeating it.
    reachable = review_queue.base_queryset(request.user)
    subjects = {str(a.id): a for a in reachable.filter(id__in=list(decisions))}

    receipt = review_submit.submit(
        queue,
        decisions,
        subjects,
        request.user,
        stage_of=dispositions.stage_of,
        claim_of=lambda article: getattr(article, "status", ""),
    )
    request.session["queue_receipt"] = receipt
    return redirect(f"{reverse('review:queue')}?{request.GET.urlencode()}")


# --- the proposal queue (SCOPE.md §2.2) -------------------------------------


def _one_per_field(qs):
    """One question per field.

    Loading a file twice could leave two pending proposals for the same
    field; asking about both is asking the same question twice, and
    deciding one leaves the other behind to reappear.

    Keyed on the group, not the record. Two reported publishers both have
    an empty record_id, so keying on that made the second one's fields
    dedupe away the first's -- and which survived depended on alphabetical
    order of the names.
    """
    seen, out = set(), []
    for p in qs.order_by("record_label", "field", "-created_at"):
        key = (p.group_key, p.flag, p.field)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _within_reach(qs, user):
    """Proposals on datasets this person may write.

    A proposal with no dataset is one nothing could place -- a scan of the
    corpus, an import that matched no membership. Those stay visible to
    anyone who reviews, because hiding them would leave them decided by
    nobody.
    """
    from accounts.access import ALL_SCOPES
    from accounts.privileges import WRITE
    from explorer.scoping import scopes_for

    scopes = scopes_for(user, WRITE)
    if scopes is ALL_SCOPES:
        return qs
    return qs.filter(Q(dataset__in=scopes) | Q(dataset=""))


def _proposal_groups(proposals):
    """Group proposals by record, because one publisher is one decision."""
    groups = {}
    for p in proposals:
        g = groups.setdefault(
            p.group_key,
            {
                "record_id": p.record_id,
                "creates_a_record": p.creates_a_record,
                "label": p.record_label,
                "dataset": p.dataset,
                "origin": p.origin,
                # A person's report is weighed differently from a scan's: the
                # reviewer is deciding on somebody's word, so the name and the
                # evidence belong on the record rather than in a column.
                "reported_by": (
                    (p.proposed_by.get_full_name() or p.proposed_by.email)
                    if p.proposed_by_id
                    else ""
                ),
                "citation": p.citation,
                "fields": [],
            },
        )
        g["fields"].append(p)
    return sorted(groups.values(), key=lambda g: g["label"])


@requires(WRITE)
def proposals(request):
    """Publisher records with something wrong, for review (REVIEW.md)."""
    from review.flags import ALL_FLAGS
    from review.proposals import ChangeProposal, ScanRun

    if request.method == "POST":
        return _submit_proposals(request)

    flag = request.GET.get("flag") or ""
    # `decision` is the shared control every queue now carries; `state` is
    # the name this one used before it was shared, kept working because
    # bookmarks and the old "Including decided" chip still send it.
    #
    # The values line up without translation: a proposal's state IS what
    # was decided about it.
    wanted = request.GET.get("decision")
    if wanted == "any":
        state = "all"
    elif wanted:
        state = wanted
    else:
        state = request.GET.get("state") or ChangeProposal.PENDING
    # Which directory to work in. Scanning every dataset is what makes
    # the queue complete and what makes it long: 894 Vermont publishers
    # with no owner recorded would otherwise sit on top of the twelve
    # Missouri questions somebody came here to answer.
    dataset = request.GET.get("dataset") or ""
    # A proposal is reviewed by whoever may write the dataset it belongs
    # to. Without this every reviewer sees every dataset's queue, and the
    # first person through decides other people's records.
    qs = _within_reach(ChangeProposal.objects.filter(target="sources"), request.user)
    qs = qs.exclude(proposed_value=F("current_value"), flag="value_disputed")
    if state != "all":
        qs = qs.filter(state=state)
    if flag:
        qs = qs.filter(flag=flag)
    # Before the dataset filter: the chips have to count what the rest of
    # this view holds, or selecting one contradicts the number on it.
    in_view = qs
    if dataset:
        qs = qs.filter(dataset=dataset)

    pending = _within_reach(
        ChangeProposal.objects.filter(target="sources", state=ChangeProposal.PENDING),
        request.user,
    )
    # Flag counts are for the directory being worked in, or every one
    # of them. A count that ignores the dataset filter promises rows the
    # filter then hides.
    scoped = pending.filter(dataset=dataset) if dataset else pending
    counts = dict(
        scoped.values_list("flag").annotate(n=Count("id")).values_list("flag", "n")
    )
    # Every directory this reviewer may write, including the ones with
    # nothing left in them.
    #
    # Built from `pending` alone, a directory left the page the moment its
    # last question was answered: Missouri's queue was worked to zero on
    # 28 August and Missouri disappeared, taking its 139 accepted and 140
    # fixed proposals with it -- unreachable, because the only way to
    # filter to them was a chip that was no longer drawn. With one
    # directory left the template then hid the row entirely, so the
    # control did not shrink, it vanished.
    #
    # Counted against the current view rather than against pending, so a
    # chip promises what selecting it delivers.
    counted = dict(
        in_view.values_list("dataset")
        .annotate(n=Count("id"))
        .values_list("dataset", "n")
    )
    from explorer.scoping import datasets_for as _datasets_for

    reachable_datasets = list(
        _datasets_for(request.user, WRITE).values_list("slug", "label")
    )
    by_dataset = [
        (slug, label or slug, counted.get(slug, 0))
        for slug, label in reachable_datasets
    ]
    # A proposal on a record in no dataset is still somebody's to answer,
    # so that bucket appears when it holds anything.
    if counted.get("", 0):
        by_dataset.append(("", "In no dataset", counted[""]))
    # Work first, then the finished ones in a stable order.
    by_dataset.sort(key=lambda row: (-row[2], row[1].lower()))
    return render(
        request,
        "review/proposals.html",
        {
            "groups": _proposal_groups(_one_per_field(qs)),
            "flags": [
                (f.key, f.label, f.defect, counts.get(f.key, 0))
                for f in ALL_FLAGS
                if counts.get(f.key, 0)
            ],
            "flag": flag,
            "state": state,
            "dataset": dataset,
            # The shared header's shapes. The chips keep their own names
            # for this template's other uses; these are the same lists
            # under the names the partial every queue includes expects.
            "facets": [
                {
                    "key": "",
                    "label": "Review all",
                    "count": scoped.count(),
                    "selected": not flag,
                    "href": f"?dataset={dataset}&state={state}",
                }
            ]
            + [
                {
                    "key": f.key,
                    "label": f.label,
                    "note": f.defect,
                    "count": counts.get(f.key, 0),
                    "selected": flag == f.key,
                    "href": f"?dataset={dataset}&flag={f.key}&state={state}",
                }
                for f in ALL_FLAGS
                if counts.get(f.key, 0)
            ],
            "facet_label": "What is wrong",
            "decisions": [
                {"name": ChangeProposal.ACCEPTED, "past": "accepted"},
                {"name": ChangeProposal.REJECTED, "past": "rejected"},
                {"name": ChangeProposal.FIXED, "past": "fixed"},
            ],
            # The count travels in the label. The directory used to be a
            # row of chips, each carrying its own number, and a bare
            # select would have dropped the one thing that says where the
            # work is -- including the zero that says a directory is
            # finished rather than unscanned.
            "datasets_for_filter": [
                {"slug": slug, "label": f"{label} ({n})"}
                for slug, label, n in by_dataset
            ],
            "params": request.GET,
            # Every directory with something pending in it, biggest
            # first, and what "" means said in words: a proposal on a
            # record in no dataset is still somebody's to answer.
            "datasets": by_dataset,
            "pending_total": pending.count(),
            "pending_here": scoped.count(),
            # An empty queue means nothing wrong or nothing looked, and a
            # reviewer cannot tell which without this.
            "last_scan": ScanRun.objects.filter(state=ScanRun.DONE).first(),
            "scan_running": ScanRun.running(),
            "receipt": request.session.pop("proposal_receipt", None),
        },
    )


def _create_proposed_sources(user, creates, proposals_by_id):
    """Make the publishers a reviewer accepted that did not exist.

    An ordinary proposal names a record and changes a field on it. This
    names none, because the proposal *is* that the record should exist. The
    reviewer decides field by field as everywhere else, so a rejected city
    simply does not reach the new row.

    The host is the exception. It is the record's only unique column, and a
    source without one cannot be crawled, so rejecting the host rejects the
    publisher and nothing is created.

    Returns (created, refused) for the receipt. A refusal is not an error a
    reviewer can act on twice -- if the host is already taken, the record
    they wanted exists, and the change belongs on it.
    """
    import uuid

    from explorer.models import Dataset, DatasetSource, Source
    from review.services import audited_create

    made, refused = 0, 0
    for submission, fields in creates.items():
        host = (fields.get("host") or "").strip().lower()
        if not host:
            refused += 1
            continue
        # Not a name match -- the schema's own uniqueness. A second row for
        # one host cannot be written, and the reviewer wanting this
        # publisher already has it.
        if Source.objects.filter(host_norm=host).exists():
            refused += 1
            continue

        # From the schema rather than field by field. This named five
        # columns and one key, so a reported publisher arrived without the
        # address, ZIP or telephone number the person reporting it had
        # taken the trouble to give -- accepted on the page, and dropped
        # between the page and the row.
        from datasets.schema import FIELDS as SCHEMA_FIELDS

        columns, meta = {}, {}
        for field in SCHEMA_FIELDS:
            if field.key == "host":
                continue
            # A proposal names the field the schema does, and the older
            # ones name the key inside `meta` on its own.
            inner = field.key.partition(".")[2]
            value = (
                fields.get(field.key) or (inner and fields.get(inner)) or ""
            ).strip()
            if field.key == "meta.state":
                value = value.upper()
            if not value:
                continue
            if field.in_meta:
                meta[inner] = value
            else:
                columns[field.key] = value
        source = Source(
            id=str(uuid.uuid4()),
            host=host,
            host_norm=host,
            meta=meta,
            **columns,
        )
        audited_create(
            user,
            [source],
            action="proposal:create_source",
            reason=f"accepted a reported publisher: {host}",
        )

        # Into the dataset whose queue it was reviewed in, so an accepted
        # publisher is a member of something rather than an orphan row.
        slug = next(
            (
                proposals_by_id[pid].dataset
                for pid in proposals_by_id
                if proposals_by_id[pid].submission == submission
                and proposals_by_id[pid].dataset
            ),
            "",
        )
        if slug and (dataset := Dataset.objects.filter(slug=slug).first()):
            audited_create(
                user,
                [DatasetSource(id=str(uuid.uuid4()), dataset=dataset, source=source)],
                action="dataset:add_source",
                reason=f"{host} into {slug} on accepting a report",
            )
        made += 1
    return made, refused


def _back_to(request, route, keys):
    """The page as it was being worked, not the whole of it.

    Saving redirected to the bare page, so somebody working one directory
    was returned to every dataset -- with the record they had just
    answered somewhere in it. That reads as a save that did not take, and
    it means re-choosing the filter after every row.
    """
    keep = {key: value for key, value in request.GET.items() if key in keys and value}
    url = reverse(route)
    return redirect(f"{url}?{urlencode(keep)}" if keep else url)


def _back_to_queue(request):
    """The review queue, still filtered to what was being worked."""
    return _back_to(request, "review:proposals", ("dataset", "flag", "state"))


def _back_to_paywalls(request):
    """The paywalls page, still filtered to what was being worked."""
    return _back_to(request, "review:paywalls", ("dataset", "sign_in"))


def _submit_proposals(request):
    """Apply a session of decisions as one audited batch per record set."""
    from django.utils import timezone

    from explorer.models import Source
    from review.proposals import ChangeProposal

    decisions = {}
    for key, value in request.POST.items():
        if not key.startswith("d-") or not value:
            continue
        decisions[int(key[2:])] = value
    if not decisions:
        # Said, not swallowed. A submission carrying nothing redirected in
        # silence, so "the page lost my decisions" and "it worked" looked
        # exactly the same -- and the queue coming back with the same
        # questions was the only evidence either way.
        request.session["proposal_receipt"] = {"nothing": True}
        return _back_to_queue(request)

    proposals_by_id = ChangeProposal.objects.in_bulk(list(decisions))
    writes = {}  # record pk -> {field: value}
    creates = {}  # submission -> {field: value}, for publishers not yet known
    accepted, rejected, fixed = [], [], []
    incomplete = 0
    for pid, verb in decisions.items():
        p = proposals_by_id.get(pid)
        if p is None or p.state != ChangeProposal.PENDING:
            continue
        if verb == "reject":
            rejected.append(p)
            continue
        value = (
            request.POST.get(f"v-{pid}", "").strip()
            if verb == "fix"
            else p.proposed_value
        )
        if verb == "fix" and not value:
            # A fix with nothing typed is not a decision. It stays in the
            # queue; the decisions around it still go through.
            incomplete += 1
            continue
        # A field with a controlled vocabulary takes one of its words. The
        # page offers them as a menu, so this refuses what no menu could
        # have produced -- a stale page, a second tab, a posted form --
        # rather than writing the defect the queue exists to clear.
        if verb == "fix":
            allowed = p.vocabulary_words
            if allowed and value not in allowed:
                incomplete += 1
                continue
        if not p.field:
            # A report rather than a change. `value_malformed` names
            # several fields at once -- a ZIP that is not a ZIP, a host
            # that is not a host -- so it carries no single field to
            # write, and the value to put right is on the record itself.
            #
            # Accepted as read: the question is answered and nothing is
            # written. Adding it to `writes` put an empty field name in
            # the batch, which the write boundary refuses -- and refusing
            # is all-or-nothing, so one of these in a submission threw
            # away every decision beside it. Seven of them sit in one
            # dataset's queue, which is why answering anything there
            # appeared to do nothing at all.
            accepted.append(p)
            continue
        if p.creates_a_record:
            creates.setdefault(p.submission, {})[p.field] = value
        else:
            writes.setdefault(p.record_id, {})[p.field] = value
        p.final_value = value
        (fixed if verb == "fix" else accepted).append(p)

    entry = None
    if writes:
        try:
            entry = audited_update_rows(
                request.user,
                Source,
                writes,
                action="proposal:apply",
                reason=f"{len(accepted) + len(fixed)} reviewed changes",
            )
        except BoundaryViolation as exc:
            # A proposal the queue can raise and cannot apply. The flag
            # vocabulary and the write boundary are kept apart, so a check
            # can name a field outside it -- `frequency_spelling` did, and
            # submitting a filtered queue of thirty-one answered with a
            # server error and no clue which of them caused it.
            #
            # Nothing is decided here. The proposals stay pending,
            # including the rejections in the same submission: a
            # half-applied batch is worse than one that did not go
            # through, because what was refused is the part nobody sees.
            request.session["proposal_receipt"] = {"refused_write": str(exc)}
            return redirect("review:proposals")

    made, refused = _create_proposed_sources(request.user, creates, proposals_by_id)

    now = timezone.now()
    for group, state in (
        (accepted, ChangeProposal.ACCEPTED),
        (fixed, ChangeProposal.FIXED),
        (rejected, ChangeProposal.REJECTED),
    ):
        for p in group:
            p.state = state
            p.decided_by = request.user
            p.decided_at = now
            if state != ChangeProposal.REJECTED:
                p.audit_entry = entry
        ChangeProposal.objects.bulk_update(
            group,
            ["state", "decided_by", "decided_at", "final_value", "audit_entry"],
        )

    request.session["proposal_receipt"] = {
        "accepted": len(accepted),
        "fixed": len(fixed),
        "rejected": len(rejected),
        "incomplete": incomplete,
        "created": made,
        "refused": refused,
        "entry": entry.pk if entry else None,
        # What was submitted and did not become a decision: a proposal
        # somebody else had already answered, or one the page was showing
        # from before it was. Counted, because a submission that lands as
        # nothing is otherwise indistinguishable from one that worked.
        "stale": len(decisions)
        - len(accepted)
        - len(fixed)
        - len(rejected)
        - incomplete,
    }
    return _back_to_queue(request)


@requires(WRITE)
def rescan_sources(request):
    """Run the publisher scan from the queue.

    The scan is what puts questions here, and the only way to run it was a
    command somebody had to remember. A reviewer looking at an empty queue
    could not tell whether nothing was wrong or nothing had looked.

    Guarded against a second run while one is in flight: two scans would
    each sweep rows the other had just made, and the queue would end up
    holding whichever finished last.
    """
    from django.core.management import call_command
    from django.utils import timezone

    from review.proposals import ScanRun

    if request.method != "POST":
        return redirect("review:proposals")

    if ScanRun.running():
        request.session["proposal_receipt"] = {
            "scan": "A scan is already running. Wait for it to finish."
        }
        return redirect("review:proposals")

    dataset = (request.POST.get("dataset") or "").strip()
    run = ScanRun.objects.create(dataset=dataset, started_by=request.user)
    try:
        # Inline rather than dispatched: the scan reads a few hundred
        # publisher rows and takes seconds. A job would need somewhere to
        # report back to, which is what this row already is.
        out = io.StringIO()
        # "" rather than None: with no dataset named the command scans
        # every one, and None is not a string the option can hold.
        call_command("scan_sources", dataset=dataset or "", stdout=out)
        summary = out.getvalue().strip()
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        run.state = ScanRun.FAILED
        run.note = str(exc)[:500]
        run.finished_at = timezone.now()
        run.save(update_fields=["state", "note", "finished_at"])
        request.session["proposal_receipt"] = {"scan": f"The scan failed: {exc}"}
        return redirect("review:proposals")

    run.state = ScanRun.DONE
    run.note = summary[:500]
    run.finished_at = timezone.now()
    # Summed, not taken from the last line. A run over every dataset
    # writes one of these per dataset, and reading only the last would
    # report the smallest directory's numbers as the whole scan's.
    import re as _re

    run.scanned = run.queued = run.withdrawn = 0
    for line in summary.splitlines():
        counted = _re.search(
            r"(\d+) publishers scanned; (?:would queue|queued) (\d+)", line
        )
        if counted:
            run.scanned += int(counted.group(1))
            run.queued += int(counted.group(2))
        elif "withdrew" in line:
            run.withdrawn += int(line.split()[1])
    run.save(
        update_fields=["state", "note", "finished_at", "scanned", "queued", "withdrawn"]
    )
    request.session["proposal_receipt"] = {"scan": summary}
    return redirect("review:proposals")


@requires_admin
def schema(request):
    """What a publisher record is, and the words its fields accept.

    The schema itself is a declaration in `datasets/schema.py` and is read
    here rather than edited: which fields are required, and what a value
    has to look like, are decisions that belong in a change somebody
    reviews.

    The vocabularies are not. A new kind of publication is a Tuesday, and
    making somebody ship a deploy for one word means the word waits for a
    deploy -- so the words are rows, and this is where they are added.

    Admin, because it decides what the whole console treats as correct: a
    word added here stops the queue asking about every record that uses
    it, and one retired starts it asking again.
    """
    from datasets.models import VocabularyTerm
    from datasets.publishers import fold_value
    from datasets.schema import ALIASES, FIELDS, VOCABULARY
    from datasets.terms import forget
    from review import syndicators
    from review import vocabulary as review_vocabulary

    notice = ""
    if request.method == "POST":
        vocabulary = (request.POST.get("vocabulary") or "").strip()
        names = {f.vocabulary for f in FIELDS if f.vocabulary}
        # The extraction review's words are kept the same way. What a type
        # or a flag is CALLED is a word; which status a type writes is not,
        # and is not editable here (review/vocabulary.py says why).
        names |= {review_vocabulary.TYPE_WORDS, review_vocabulary.FLAG_WORDS}
        # Syndicator spellings are the same kind of list: what the corpus
        # writes for one wire service, folded to one label. Editable here
        # for the same reason the publisher kinds are -- a new spelling
        # arrives on a Tuesday and should not wait for a deploy.
        names |= {syndicators.SYNDICATOR_WORDS}
        if vocabulary not in names:
            raise Http404("No such vocabulary")
        retire = (request.POST.get("retire") or "").strip()
        if retire:
            # Retired, never deleted. A word no longer offered is still on
            # the records written while it was, and deleting it turns a
            # filter that matched them into one that matches nothing.
            changed = VocabularyTerm.objects.filter(
                vocabulary=vocabulary, value=retire
            ).update(retired=True)
            notice = f"{retire} is no longer offered." if changed else ""
        else:
            # Folded for a publisher vocabulary, where the value is a word
            # somebody typed on a record and "Digital Native" and
            # "digital native" are the same word. NOT folded for the
            # review's, where the value is a key the pipeline writes:
            # folding turns `paywall_stub` into `paywall stub`, which
            # matches no flag and no type, so the rename saved a row
            # nothing would ever read.
            raw = request.POST.get("value") or ""
            value = (
                raw.strip()
                if vocabulary
                in (review_vocabulary.TYPE_WORDS, review_vocabulary.FLAG_WORDS)
                else fold_value(raw)
            )
            if not value:
                raise ValueError("Type the word to add")
            # A word is added to a kind, so it carries that kind's name and
            # the spelling the kind is written as. Adding a kind is the
            # same form with a new name typed into it.
            label = (request.POST.get("label") or "").strip()
            spelling = (request.POST.get("spelling") or "").strip()
            term, made = VocabularyTerm.objects.get_or_create(
                vocabulary=vocabulary,
                value=value,
                defaults={
                    "label": label,
                    "spelling": spelling,
                    "added_by": request.user,
                },
            )
            # A word being RE-said is a revision, not a duplicate. The
            # review vocabularies post an existing value with new words
            # in it -- that is what the rename form is -- and
            # get_or_create alone would find the row and leave it as it
            # was, so the form would report success and change nothing.
            if not made and (
                (label and term.label != label)
                or (spelling and term.spelling != spelling)
            ):
                term.label = label or term.label
                term.spelling = spelling or term.spelling
                term.retired = False
                term.save(update_fields=["label", "spelling", "retired"])
                notice = f"{value} is now {term.label}."
            elif not made and term.retired:
                # Adding a word that was retired brings it back rather
                # than refusing it as already there, which is what
                # somebody typing it again means.
                term.retired = False
                term.save(update_fields=["retired"])
                notice = f"{value} is offered again."
            else:
                notice = f"{value} added." if made else f"{value} was already there."
            AuditLogEntry.objects.create(
                actor=request.user,
                action="schema:term",
                target_table="datasets_vocabularyterm",
                target_ids=[f"{vocabulary}:{value}"],
                after={"value": value, "label": label, "spelling": spelling},
                reason=f"added {value} to {vocabulary}",
            )
        forget(vocabulary)
        # The review's words are cached separately, and a revision nobody
        # can see until the cache expires reads as a form that did
        # nothing.
        review_vocabulary.forget()
        request.session["schema_notice"] = notice
        return redirect("review:schema")

    # Grouped by what a word means, not listed as words.
    #
    # A flat list read "digital counts as Digital written digital native"
    # -- three values in a row with nothing saying which was which. What
    # a reader needs to know is the other way round: these are the kinds a
    # publication can be, and these are the words that mean each one.
    held = {}
    for term in VocabularyTerm.objects.all():
        by_vocabulary = held.setdefault(term.vocabulary, {})
        # The label is the kind; the spelling is what the kind is written
        # as on a record. A kind with no one spelling -- "broadcast",
        # which does not say television or radio -- groups under its own
        # name and offers none.
        kind = by_vocabulary.setdefault(
            term.label or term.value,
            {"kind": term.label or term.value, "spelling": term.spelling, "words": []},
        )
        if term.spelling and not kind["spelling"]:
            kind["spelling"] = term.spelling
        kind["words"].append(term)
    rows = []
    for field in FIELDS:
        kinds = sorted(held.get(field.vocabulary, {}).values(), key=lambda k: k["kind"])
        for kind in kinds:
            kind["words"].sort(key=lambda t: (t.retired, t.value))
        rows.append(
            {
                "key": field.key,
                "label": field.label,
                "need": field.need,
                "required": field.required,
                "asked": field.asked,
                "rule": field.rule,
                "rule_name": field.rule_name,
                "vocabulary": field.vocabulary,
                "note": field.note,
                "kinds": kinds,
                "aliases": sorted(k for k, v in ALIASES.items() if v == field.key),
            }
        )
    return render(
        request,
        "review/schema.html",
        {
            "fields": rows,
            "notice": request.session.pop("schema_notice", ""),
            "vocabulary_rule": VOCABULARY,
            "review": _extraction_review_schema(),
            "syndicators": _syndicator_groups(),
        },
    )


def _syndicator_groups():
    """The syndicator labels and the spellings each covers.

    Seeded in `review/syndicators.py` and overridden by rows, so this
    shows both: the groups that ship and the ones somebody added. A seed
    group with no row still appears, or the page would say the corpus
    folds nothing while it is folding five names into two.
    """
    from datasets.models import VocabularyTerm
    from review.syndicators import SEED, SYNDICATOR_WORDS

    held = {}
    for label, spellings in SEED:
        held[label] = {"label": label, "words": list(spellings), "seeded": True}
    for term in VocabularyTerm.objects.filter(
        vocabulary=SYNDICATOR_WORDS, retired=False
    ):
        label = term.label or term.value
        group = held.setdefault(label, {"label": label, "words": [], "seeded": False})
        if term.value not in group["words"]:
            group["words"].append(term.value)
    return sorted(held.values(), key=lambda g: g["label"])


def _extraction_review_schema():
    """What an extraction review is: the verbs, the types, the flags.

    The half that is declared is shown as declared, with what it writes;
    the half that is words is shown with a form beside it. Read from the
    modules that define them rather than restated here, so a verb or a
    flag added there appears without a second edit -- restating a
    vocabulary on the page that documents it is the drift this page
    exists to make visible.
    """
    from review import dispositions, kernel
    from review import vocabulary as review_vocabulary

    queue = kernel.get("extraction")
    types = []
    for entry in review_vocabulary.content_types():
        value = entry["value"]
        writes = dispositions.TYPE_BECOMES.get(value, "")
        types.append(
            {
                "value": value,
                "label": entry["label"],
                "writes": (
                    "back to where the stage rewinds to"
                    if writes == dispositions.REWIND
                    else writes
                ),
            }
        )
    return {
        "verbs": [
            {"name": verb.name, "label": verb.label, "sublabel": verb.sublabel}
            for verb in queue.verbs
        ],
        "types": types,
        "type_vocabulary": review_vocabulary.TYPE_WORDS,
        "flags": [
            {
                "value": flag,
                "label": review_vocabulary.flag_words(flag, flag, hint)[0],
                "hint": review_vocabulary.flag_words(flag, flag, hint)[1],
            }
            for flag, hint in review_vocabulary.declared_flags()
        ],
        "flag_vocabulary": review_vocabulary.FLAG_WORDS,
    }


PAYWALL_COUNTS_CACHE_KEY = "review.paywall_corpus_counts"
# An hour, because `warm_caches` runs every 45 minutes and a cache that
# expires before the next warm is not warmed at all. At 300s this entry
# was expired for 40 of every 45 minutes, so nearly every visit paid the
# uncached cost -- 104 seconds on 2026-08-29, against a warm 1.7s. The
# other warmed targets (costs, dashboard, crawler counts) were already
# 3600 and did not have the symptom. Keep this above the warm interval.
PAYWALL_COUNTS_CACHE_SECONDS = 3600


def paywall_corpus_counts():
    """What the pipeline could not read past a paywall, and what it did read.

    One query rather than one per publisher, and cached rather than run on
    every load: it aggregates every enrichment row through articles and
    candidate links with no bound, which measured 13 seconds cold and 0.8
    warm against production. The page is a working surface -- somebody
    saves a price and comes straight back to it -- so it was paying that
    on every return.

    An hour, matching the other warmed caches and outliving the
    45-minute `warm_caches` cycle. These numbers move when the pipeline
    runs, which is not while somebody is editing a record.

    The cost is not the 13s the first version measured. The production
    database is db-g1-small on PD_HDD, whose sustained random reads are
    about 0.75 IOPS per GB -- roughly 11 IOPS at 15GB -- against a join
    over `articles` (1.4GB) and `candidate_links` (446MB) with 128MB of
    shared buffers and a 54% buffer hit ratio. Cold, this query has been
    measured at 104 seconds.

    The second half is the evidence for "verified": a sign-in that is
    configured is not a sign-in that works. Seven publishers carry
    credentials and six of them have never produced an article.
    """

    def fetch():
        lost, read = {}, {}
        with connections["crawler"].cursor() as cur:
            cur.execute("""
                SELECT cl.source_id,
                       count(*) FILTER (WHERE e.skip_reason LIKE 'paywall%%'),
                       count(*) FILTER (WHERE e.skip_reason IS NULL)
                FROM article_enrichment e
                JOIN articles a ON a.id = e.article_id
                JOIN candidate_links cl ON cl.id = a.candidate_link_id
                GROUP BY 1
                """)
            for source_id, blocked, extracted in cur.fetchall():
                if blocked:
                    lost[source_id] = blocked
                if extracted:
                    read[source_id] = extracted
        return lost, read

    try:
        return cache.get_or_set(
            PAYWALL_COUNTS_CACHE_KEY, fetch, PAYWALL_COUNTS_CACHE_SECONDS
        )
    except DatabaseError:
        # A cold cache is slow, not broken; an unreachable one must not
        # take the page down with it.
        return fetch()


@requires(WRITE)
def paywalls(request):
    """Publishers we cannot read, and what it would take to read them.

    The pipeline already knows: an article it could not extract because a
    paywall stood in the way is skipped with a paywall reason, and 57
    publishers have those. None of them was marked as paywalled, because
    until now there was nowhere to mark it and nothing showing who to
    mark. So this ranks them by how many articles are being lost.

    Whoever reviews a dataset can work its paywalls: which publishers are
    behind one, what a subscription costs and where to sign in are the
    same kind of judgement as the rest of the queue, made about the
    datasets that person already reviews.

    Storing a credential is not. It writes a secret into the crawler's
    project, where the extractor reads it, so it stays with the
    administrators -- and the page shows the rest to everybody else
    rather than hiding a list somebody can act on.
    """

    from accounts.access import is_application_admin
    from datasets.paywall import PERIODS
    from explorer.models import DatasetSource, Source
    from explorer.scoping import datasets_for, narrow
    from review.credentials import PROJECT, CredentialError, secret_name_for, store

    reachable = narrow(Source.objects.all(), request.user, WRITE, source_path="id")

    if request.method == "POST":
        source = reachable.filter(id=request.POST.get("source_id", "")).first()
        if source is None:
            raise Http404("No such publisher")
        notice = ""
        # The same validator the record page uses, so the two cannot
        # disagree about what an amount is -- and the price and the
        # sign-in page are recorded here because this is where somebody
        # decides them: they are looking at the site to answer whether it
        # has a paywall at all.
        from datasets.paywall import paywall_from_form

        errors = []
        changes = paywall_from_form(request.POST, errors)
        if errors:
            request.session["paywall_notice"] = " ".join(errors)
            return _back_to_paywalls(request)

        # The box says "no paywall", so saving with it ticked is somebody
        # ruling the publisher out and saving without it is confirming
        # what the page already says: these are publishers the extractor
        # could not read past a paywall.
        from review.models import PaywallDismissal

        ruled_out = bool(request.POST.get("no_paywall"))
        changes["has_paywall"] = not ruled_out
        if ruled_out:
            # Kept here rather than on the record: `has_paywall` is false
            # on every publisher nobody has looked at, so it cannot tell
            # "decided against" from "not yet decided" and the page would
            # ask again for ever.
            PaywallDismissal.objects.update_or_create(
                source_id=source.id,
                defaults={
                    "source_label": source.canonical_name or source.host,
                    "decided_by": request.user,
                },
            )
        else:
            PaywallDismissal.objects.filter(source_id=source.id).delete()
        username = (request.POST.get("username") or "").strip()
        password = (request.POST.get("password") or "").strip()
        if (username or password) and not is_application_admin(request.user, APP):
            # Refused rather than ignored: a credential somebody typed and
            # believes is stored is worse than one they were told to hand
            # to an administrator.
            #
            # Kept in `notice` rather than written to the session here:
            # the save below writes its own, and a message written twice
            # is the second one.
            notice = (
                "Credentials are stored by an administrator. The rest of "
                "this row was saved."
            )
            username = password = ""
        if username or password:
            try:
                name = store(
                    source.host_norm or source.host,
                    {"username": username, "password": password},
                )
            except CredentialError as exc:
                request.session["paywall_notice"] = str(exc)
                return _back_to_paywalls(request)
            changes["auth_secret_name"] = name
            notice = f"Credentials stored as {name}."
        audited_update(
            request.user,
            [source],
            changes,
            action="source:paywall",
            reason=request.POST.get("reason", "") or "reviewed the paywall",
        )
        request.session["paywall_notice"] = notice or "Saved."
        return _back_to_paywalls(request)

    lost, read = paywall_corpus_counts()

    # One directory at a time. Fifty-seven publishers across four states
    # is a list nobody works end to end, and whose paywalls are worth
    # paying for is a question somebody asks about one directory.
    #
    # Offered from the datasets this person may write, because that is
    # what the page acts on -- and `requires` has already refused a
    # dataset in the query string they have no grant for.
    choices = datasets_for(request.user, WRITE)
    chosen = (request.GET.get("dataset") or "").strip()
    # What somebody has already ruled out, so the page does not ask again.
    # A record that says it is paywalled is on the page whatever was
    # ruled here: the record is the stronger statement, and ticking the
    # box on it is how a publisher comes back.
    from review.models import PaywallDismissal

    ruled_out = set(PaywallDismissal.objects.values_list("source_id", flat=True))
    candidates = reachable.filter(
        Q(id__in=list(lost)) | Q(has_paywall=True) | Q(requires_login=True)
    ).exclude(Q(id__in=ruled_out) & Q(has_paywall=False))
    if chosen and choices.filter(slug=chosen).exists():
        candidates = candidates.filter(
            id__in=DatasetSource.objects.filter(dataset__slug=chosen).values_list(
                "source_id", flat=True
            )
        )
    else:
        chosen = ""

    # Whether the extractor already signs in. The two halves are
    # different work: one needs a subscription bought and a credential
    # stored, the other is being read today and is on the page because it
    # was once not.
    # Three states, because credentials alone are not enough:
    #
    #   credentialed  a subscription exists and its secret is stored
    #   configured    the extractor is set to sign in with it
    #   verified      it worked -- articles have been read since
    #
    # "Automated" was all three at once and true of seven publishers, six
    # of which have never produced an article.
    sign_in = (request.GET.get("sign_in") or "").strip()
    if sign_in == "none":
        candidates = candidates.filter(auth_secret_name__isnull=True)
    elif sign_in == "credentialed":
        candidates = candidates.filter(auth_secret_name__isnull=False)
    elif sign_in == "configured":
        candidates = candidates.filter(
            auth_secret_name__isnull=False, requires_login=True
        )
    elif sign_in == "verified":
        candidates = candidates.filter(
            auth_secret_name__isnull=False, requires_login=True, id__in=list(read)
        )
    else:
        sign_in = ""

    # The last thing somebody wrote about each publisher, so the comment
    # box is not written into a void. `reason` already reaches the audit
    # entry -- the form posts it and `record` stores it -- it just had
    # nowhere to be typed and nowhere to be read.
    from audit.models import AuditLogEntry

    last_comment = {}
    for entry in (
        AuditLogEntry.objects.filter(target_table="sources")
        .exclude(reason="")
        .exclude(reason="reviewed the paywall")
        .order_by("-timestamp")[:400]
    ):
        for target in entry.target_ids or []:
            last_comment.setdefault(
                str(target), (entry.reason, entry.timestamp, entry.actor)
            )

    rows = []
    for source in candidates:
        rows.append(
            {
                "id": source.id,
                "name": source.canonical_name or source.host,
                "host": source.host_norm or source.host,
                "owner": (source.owner or "").strip(),
                "lost": lost.get(source.id, 0),
                "read": read.get(source.id, 0),
                # What stage this publisher's sign-in has reached. Named
                # rather than counted, because each is a different piece
                # of work: buy a subscription, configure the extractor,
                # then prove it reads.
                "stage": (
                    "verified"
                    if source.auth_secret_name
                    and source.requires_login
                    and read.get(source.id)
                    else (
                        "configured"
                        if source.auth_secret_name and source.requires_login
                        else "credentialed" if source.auth_secret_name else ""
                    )
                ),
                "has_paywall": source.has_paywall,
                "requires_login": source.requires_login,
                "auth_type": source.auth_type or "",
                # Whether one exists, never what is in it.
                "secret": source.auth_secret_name or "",
                "expected_secret": secret_name_for(source.host_norm or source.host),
                "cost": source.subscription_cost,
                "period": source.subscription_period or "",
                "login_url": source.login_url
                or (source.auth_config or {}).get("login_url", ""),
                "last_comment": last_comment.get(str(source.id), ("", None, None)),
            }
        )
    # The most articles first: that is the size of the hole each one
    # leaves, and the order somebody would work them in.
    rows.sort(key=lambda r: (-r["lost"], r["name"].lower()))

    if request.GET.get("format") == "csv":
        # The same list, in the same order, filtered the same way: a
        # report of one directory's paywalls is what somebody takes to
        # the person who decides what to subscribe to.
        import csv as csv_module
        import io

        buffer = io.StringIO()
        writer = csv_module.writer(buffer)
        writer.writerow(
            [
                "publisher",
                "url",
                "login page",
                "subscription cost",
                "per",
                "articles lost",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["name"],
                    f"https://{row['host']}",
                    row["login_url"],
                    "" if row["cost"] is None else row["cost"],
                    # The period travels with the amount. A cost without
                    # it is a number nobody can read: $12 a month and $12
                    # a year are different subscriptions.
                    row["period"],
                    row["lost"],
                ]
            )
        stem = "-".join(part for part in ("paywalls", chosen, sign_in) if part)
        response = HttpResponse(
            buffer.getvalue(), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{stem}.csv"'
        return response

    return render(
        request,
        "review/paywalls.html",
        {
            "rows": rows,
            "total_lost": sum(r["lost"] for r in rows),
            "datasets": choices,
            "dataset": chosen,
            "periods": PERIODS,
            "sign_in": sign_in,
            # Who may store one. The rest of the page is the same for
            # everybody who reviews these datasets.
            "may_store": is_application_admin(request.user, APP),
            "project": PROJECT,
            "notice": request.session.pop("paywall_notice", ""),
        },
    )


# --------------------------------------------------------------------
# The discovery queue
# --------------------------------------------------------------------

#: The cohort the queue opens on. March is where the reviewed corpus is,
#: and a queue that opens on 236,160 rows opens on nothing anybody can
#: act on. Timezone-aware, because `discovered_at` is compared against
#: these and a naive bound is read in whatever zone the process happens
#: to be in.
DISCOVERY_COHORT = (
    datetime(2026, 3, 1, tzinfo=UTC),
    datetime(2026, 4, 1, tzinfo=UTC),
)


def _discovery_datasets():
    """The datasets that have verifications, for the Dataset filter.

    From `datasets` rather than distinct values on the links: a dataset
    with nothing left to review should still be offerable, or a reviewer
    cannot ask why it is empty.
    """
    from explorer.models import Dataset

    try:
        return [
            {"slug": d.id, "label": d.label or d.slug or d.id}
            for d in Dataset.objects.all().order_by("label")
        ]
    except DatabaseError:
        return []


def _subject_of(verification):
    """What a decision about this row is keyed on."""
    return str(verification.candidate_link_id or verification.id)


def _decided_candidate_links(rows):
    """{subject id: verb} for the links already answered, in one query.

    The verb and not just the fact, because the Decision filter offers
    "restored" and "confirmed" separately -- a reviewer asking what they
    marked last week is asking which way.
    """
    from review.models import ReviewDecision

    ids = {_subject_of(row) for row in rows}
    if not ids:
        return {}
    return dict(
        ReviewDecision.objects.filter(
            subject_type="candidate_link", subject_id__in=list(ids)
        ).values_list("subject_id", "verb")
    )


def _discovery_rows(stratum, start, end, dataset=""):
    """One stratum's rows, drawn and annotated with how they were drawn."""
    from explorer.models import UrlVerification
    from review import discovery

    base = UrlVerification.objects.select_related(
        "candidate_link", "candidate_link__source"
    ).filter(discovery.predicate(stratum))
    base = discovery.in_cohort(base, start, end)
    if dataset:
        base = base.filter(candidate_link__dataset_id=dataset)
    population = base.count()
    rows = list(discovery.drawn(base, stratum))
    probability = discovery.inclusion_probability(stratum, population)
    for row in rows:
        row._stratum = stratum
        row._probability = probability
    return rows, population, probability


@requires(WRITE)
def discovery_queue(request):
    """URLs the pipeline judged before any body was fetched.

    The subject is a candidate link: a URL, its publisher, and the
    verdict verification recorded. There is nothing to read, so the live
    URL is the record's primary affordance and opens in its own tab --
    a reviewer cannot answer "is this a story" without looking at it.
    """
    from review import discovery

    if request.method == "POST":
        return _submit_discovery_decisions(request)

    start, end = discovery.window_for(request.GET)
    chosen = request.GET.get("stratum", discovery.DOUBTFUL)
    if chosen not in discovery.STRATUM_LABELS:
        chosen = discovery.DOUBTFUL

    params = request.GET.copy()
    params.pop("page", None)
    stratum_params = params.copy()
    stratum_params.pop("stratum", None)

    queue_def = _kernel.get(discovery.DISCOVERY_QUEUE_KEY)
    # What has already been said about a row. "Including decided" was a
    # link that could only be on or off; this is the same control the
    # other two queues now carry, named by the verb's own past tense.
    wanted = request.GET.get("decision", "")
    dataset = request.GET.get("dataset", "")

    strata, rows, population, probability = [], [], 0, 1.0
    connected = True
    try:
        drawn = {}
        for key, _label, _description in discovery.STRATA:
            drawn[key] = _discovery_rows(key, start, end, dataset)

        # Every stratum's rows, resolved against the decisions in one
        # query. The counts on the chips used to be `len(drawn)`, taken
        # before the decided rows were removed, so a chip said 746 and
        # went on saying 746 however many of them had been answered --
        # the one number a reviewer works down.
        answered = _decided_candidate_links(
            row for rows_, _size, _p in drawn.values() for row in rows_
        )

        def _left(rows_):
            if wanted == "any":
                return list(rows_)
            if wanted:
                # Answered with this verb, which is the question a
                # reviewer asks afterwards: what did I mark, and was I
                # right.
                return [r for r in rows_ if answered.get(_subject_of(r)) == wanted]
            return [r for r in rows_ if _subject_of(r) not in answered]

        for key, label, description in discovery.STRATA:
            drawn_rows, size, _p = drawn[key]
            remaining = _left(drawn_rows)
            strata.append(
                {
                    "key": key,
                    "label": label,
                    "description": description,
                    "count": len(remaining),
                    "drawn": len(drawn_rows),
                    "decided": len(drawn_rows) - len(remaining),
                    "population": size,
                    "selected": key == chosen,
                }
            )
            if key == chosen:
                rows, population, probability = remaining, size, _p
    except DatabaseError:
        connected = False
        answered = {}

    # The margin is a log-odds score whose scale means nothing to a
    # reader. Its percentile within the cohort does.
    cuts = discovery.margin_cuts(start, end) if rows else []
    for row in rows:
        row.offered_verbs = queue_def.offered(row)
        row.percentile = discovery.percentile(row.verification_confidence, cuts)
        # What the model concluded, which is not the verdict: `guess()`
        # applies a whitelist and a blacklist afterwards, so a URL can
        # score +477 and still come back False.
        row.model_said = discovery.model_said(row.verification_confidence)
        # One plain phrase, because the cell used to print the verdict,
        # then a marker repeating it, then a raw score and an ordinal:
        #
        #     story scores a story now; it was not kept rescored 25, 4th percentile
        #
        # No reviewer can act on that. The score is log-odds, whose scale
        # means nothing without the cohort, and the percentile it needed
        # to be read against was sitting right next to it unexplained.
        # Both now travel inside the phrase, or in the tooltip.
        row.reading = discovery.how_the_model_read_it(
            row.verification_confidence, row.percentile
        )
        row.disagrees = discovery.disagrees_with_outcome(
            row.verification_confidence, row.new_status
        )

    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "review/discovery.html",
        {
            "crawler_connected": connected,
            "strata": strata,
            "facets": [
                dict(
                    stratum,
                    href=(
                        "?"
                        + stratum_params.urlencode()
                        + ("" if stratum["selected"] else f"&stratum={stratum['key']}")
                    ),
                    note=stratum["description"],
                )
                for stratum in strata
            ],
            "facet_label": "Which rows",
            "day_windows": review_queue.DAY_WINDOWS,
            "default_days": "custom",
            "datasets": _discovery_datasets(),
            "decisions": queue_def.verbs,
            "stratum": chosen,
            "stratum_label": discovery.STRATUM_LABELS.get(chosen, ""),
            "population": population,
            "probability": probability,
            "params": params,
            "stratum_params": stratum_params,
            "state": request.GET.get("state", ""),
            "cohort_start": start,
            "cohort_end": end,
            "page": page,
            "rows": page.object_list,
            "queue_verbs": queue_def.verbs,
            "decided": sum(s["decided"] for s in strata),
            "receipt": _receipt_counts(request.session.pop("discovery_receipt", None)),
        },
    )


@require_POST
@requires(WRITE)
def _submit_discovery_decisions(request):
    """Apply a session of discovery decisions.

    The same submit path every other queue uses, so the receipt, the
    audit entry and the already-answered filter are the ones that already
    exist rather than a second implementation of them.
    """
    from explorer.models import UrlVerification
    from review import discovery
    from review import submit as review_submit

    queue_def = _kernel.get(discovery.DISCOVERY_QUEUE_KEY)
    decisions = review_submit.posted(request.POST)

    subjects = {}
    for row in UrlVerification.objects.select_related("candidate_link").filter(
        candidate_link_id__in=list(decisions)
    ):
        key = str(row.candidate_link_id or row.id)
        # The stratum travels on the form: sample membership follows from
        # nothing about the row, so it cannot be recomputed here.
        row._stratum = request.POST.get(f"stratum-{key}", "")
        row._probability = float(request.POST.get(f"probability-{key}", "1") or 1)
        subjects[key] = row

    receipt = review_submit.submit(queue_def, decisions, subjects, request.user)
    request.session["discovery_receipt"] = dict(receipt, queue=queue_def.key)
    return redirect(f"{reverse('review:discovery')}?{urlencode(request.GET)}")


# --------------------------------------------------------------------
# The classification queue
# --------------------------------------------------------------------

#: How much of the body a coder reads. The model sees title + body
#: truncated at 512 BERT tokens, roughly 350-400 words, and the
#: historical cohorts showed a median article of 255 words. A window
#: much larger than the model's input means some disagreement measures
#: the truncation rather than the model.
READING_WORDS = 250


def _reading_window(article):
    """The first `READING_WORDS` of the body, and whether there is more.

    `text` is the cleaned body and `content` the raw capture, in that
    order: classifying the raw capture means classifying navigation
    menus and cookie notices, which is how 3% of stored articles got a
    CIN label derived from a list of section names.
    """
    body = ""
    for field in ("text", "content"):
        value = getattr(article, field, None)
        if isinstance(value, str) and value.strip():
            body = value.strip()
            break
    words = body.split()
    return " ".join(words[:READING_WORDS]), len(words) > READING_WORDS


@requires(CLASSIFY)
def classification_queue(request):
    """One assigned story at a time: headline, URL, body, and a judgement.

    No filters. Not by dataset, not by date, not by publisher — choosing
    what to look at is how a sample stops being one, and the coder is
    shown what the draw assigned them.

    Nothing about the pipeline's own opinion appears: no CIN label, no
    confidence, no status, no wire flag. A label produced by somebody who
    has seen the model's answer cannot be used to score the model.
    """
    from explorer.models import Article
    from review.classification import (
        CIN_LABELS,
        ClassificationAssignment,
        ClassificationCohort,
        ClassificationDecision,
    )

    if request.method == "POST":
        return _submit_classification(request)

    cohorts = ClassificationCohort.objects.filter(
        slug__in=scopes_for(request.user, CLASSIFY) or []
    )
    outstanding = (
        ClassificationAssignment.objects.filter(
            assigned_to=request.user, completed_at__isnull=True, cohort__in=cohorts
        )
        .select_related("cohort")
        .order_by("assigned_at")
    )

    assignment = outstanding.first()
    article = None
    if assignment is not None:
        article = (
            Article.objects.using("crawler").filter(id=assignment.article_id).first()
        )

    body, truncated = ("", False)
    if article is not None:
        body, truncated = _reading_window(article)

    done = ClassificationDecision.objects.filter(decided_by=request.user).count()
    return render(
        request,
        "review/classification.html",
        {
            "assignment": assignment,
            "article": article,
            "body": body,
            "truncated": truncated,
            "labels": CIN_LABELS,
            "rejections": ClassificationDecision.REJECTIONS,
            "remaining": outstanding.count(),
            "done": done,
            "reading_words": READING_WORDS,
        },
    )


@require_POST
@requires(CLASSIFY)
def _submit_classification(request):
    """Record one coder's judgement and close their assignment."""
    from django.utils import timezone

    from review.classification import (
        ClassificationAssignment,
        ClassificationDecision,
        ClassificationSample,
    )

    assignment = ClassificationAssignment.objects.filter(
        pk=request.POST.get("assignment"),
        assigned_to=request.user,
        completed_at__isnull=True,
    ).first()
    if assignment is None:
        # Somebody else's assignment, or one already answered. Not an
        # error worth a page: the queue simply moves on.
        return redirect("review:classification")

    primary = (request.POST.get("primary") or "").strip()
    secondary = (request.POST.get("secondary") or "").strip()
    reject = (request.POST.get("reject") or "").strip()
    if not primary and not reject:
        messages.error(request, "Choose a category, or say why you cannot.")
        return redirect("review:classification")

    # A rejection is not a label. Storing one as a category is what made
    # the historical primary column unusable without filtering.
    if reject:
        primary, secondary = "", ""

    sample = ClassificationSample.objects.filter(
        cohort=assignment.cohort, article_id=assignment.article_id
    ).first()

    ClassificationDecision.objects.create(
        cohort=assignment.cohort,
        article_id=assignment.article_id,
        decided_by=request.user,
        primary_label=primary,
        secondary_label=secondary,
        reject_reason=reject,
        stratum=getattr(sample, "stratum", ""),
        inclusion_probability=getattr(sample, "inclusion_probability", 1.0),
        dataset_id=getattr(sample, "dataset_id", ""),
        seconds_spent=_seconds_spent(request.POST.get("opened_at")),
    )
    assignment.completed_at = timezone.now()
    assignment.save(update_fields=["completed_at"])
    return redirect("review:classification")


def _seconds_spent(opened_at):
    """How long the coder had the row open, or None.

    The cheapest defence against somebody clicking through, short of the
    seeded check records, and it costs nothing to record.
    """
    try:
        return max(0, int(float(opened_at)))
    except (TypeError, ValueError):
        return None
