"""Review and cleanup views (SCOPE.md §2.2). Editor role throughout."""

from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, editor_required, role_required
from audit.models import AuditLogEntry
from explorer.models import Article, ArticleEnrichment
from explorer.views import _filtered_articles
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


def _get_article(article_id):
    article = Article.objects.filter(id=article_id).first()
    if article is None:
        raise Http404("No such article")
    return article


@editor_required
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


@editor_required
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


@admin_required
def audit_log(request):
    """The audit trail with the revert path (SCOPE.md §2.2: every action
    is reversible from the audit record)."""
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        page_number = 1
    paginator = Paginator(AuditLogEntry.objects.select_related("actor").all(), 50)
    return render(
        request,
        "review/audit_log.html",
        {"page": paginator.get_page(page_number)},
    )


@editor_required
@require_POST
def revert_entry(request, entry_id):
    entry = get_object_or_404(AuditLogEntry, pk=entry_id)
    revert(request.user, entry, reason=request.POST.get("reason", ""))
    return redirect("review:audit_log")


# --- import (SCOPE.md §2.4: diff report first, then explicit apply) ---------


@editor_required
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


@editor_required
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


@editor_required
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


@editor_required
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


@role_required
def export(request):
    """Choose columns for the current filter set; download or save the
    definition for re-running against current data."""
    if request.method == "POST":
        columns = [c for c in request.POST.getlist("columns") if c in EXPORT_COLUMNS]
        if not columns:
            return HttpResponseBadRequest("Pick at least one column")
        params = {
            key: value
            for key, value in request.POST.items()
            if key.startswith("f_") and value
        }
        params = {key[2:]: value for key, value in params.items()}
        if request.POST.get("save_as"):
            ExportDefinition.objects.update_or_create(
                name=request.POST["save_as"],
                defaults={
                    "created_by": request.user,
                    "params": params,
                    "columns": columns,
                },
            )
        queryset = _filtered_articles(params)
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


@role_required
def export_run(request, definition_id):
    """Re-run a saved definition against current data."""
    definition = get_object_or_404(ExportDefinition, pk=definition_id)
    queryset = _filtered_articles(definition.params)
    filename = f"{definition.name}.csv".replace("/", "-")
    return csv_response(queryset, definition.columns, filename)


# --- extraction review queue (SCOPE.md §2.3) --------------------------------
#
# Read-only. Any assigned role may look; Phase 2b adds the three
# dispositions as audited writes behind the editor role, in the
# placeholder the template already marks.


@role_required
def queue(request):
    """Articles automated triage flagged, with what a human needs to judge
    them: captured text length, the reason given, the CIN label, the
    byline (SCOPE.md §2.3)."""
    vocabulary = review_queue.vocab()
    params = request.GET.copy()
    params.pop("page", None)
    # Facet links replace their own dimension rather than appending a
    # second value to it, so each facet builds on the query string with
    # its own key removed.
    case_params = params.copy()
    case_params.pop("case", None)
    band_params = params.copy()
    band_params.pop("band", None)
    context = {
        "crawler_connected": vocabulary is not None,
        "vocab": vocabulary,
        "params": params,
        "case_params": case_params,
        "band_params": band_params,
        "bands": [],
        "cases": [],
    }

    if vocabulary is not None:
        try:
            page_number = int(request.GET.get("page", "1"))
        except ValueError:
            page_number = 1
        paginator = Paginator(review_queue.queued(request.GET), QUEUE_PAGE_SIZE)
        context["page"] = paginator.get_page(page_number)
        context["bands"] = review_queue.band_facets(request.GET)
        context["cases"] = review_queue.case_facets(request.GET)

    template = (
        "review/_queue_results.html"
        if request.headers.get("HX-Request")
        else "review/queue.html"
    )
    return render(request, template, context)


# --- the proposal queue (SCOPE.md §2.2) -------------------------------------


def _one_per_field(qs):
    """One question per field.

    Loading a file twice could leave two pending proposals for the same
    field; asking about both is asking the same question twice, and
    deciding one leaves the other behind to reappear.
    """
    seen, out = set(), []
    for p in qs.order_by("record_label", "field", "-created_at"):
        key = (p.record_id, p.field)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _proposal_groups(proposals):
    """Group proposals by record, because one publisher is one decision."""
    groups = {}
    for p in proposals:
        g = groups.setdefault(
            p.record_id,
            {
                "record_id": p.record_id,
                "label": p.record_label,
                "dataset": p.dataset,
                "origin": p.origin,
                "fields": [],
                "blocked": [],
            },
        )
        (g["fields"] if p.actionable else g["blocked"]).append(p)
    for g in groups.values():
        g["actionable"] = bool(g["fields"])
    return sorted(groups.values(), key=lambda g: (not g["actionable"], g["label"]))


@editor_required
def proposals(request):
    """Review proposed changes, decide, then submit the session at once."""
    from review.proposals import ChangeProposal

    if request.method == "POST":
        return _submit_proposals(request)

    finding = request.GET.get("finding") or ""
    state = request.GET.get("state") or ChangeProposal.PENDING
    qs = ChangeProposal.objects.filter(target="sources")
    # A proposal whose value already matches the record is not a
    # question; showing it produces "writing X over X".
    qs = qs.exclude(proposed_value=F("current_value"))
    if state != "all":
        qs = qs.filter(state=state)
    if finding:
        qs = qs.filter(finding=finding)

    counts = {
        key: ChangeProposal.objects.filter(
            target="sources", state=ChangeProposal.PENDING, finding=key
        )
        .exclude(proposed_value=F("current_value"))
        .count()
        for key, _label in ChangeProposal.FINDINGS
    }
    return render(
        request,
        "review/proposals.html",
        {
            "groups": _proposal_groups(_one_per_field(qs)),
            "findings": [
                (k, label, counts.get(k, 0)) for k, label in ChangeProposal.FINDINGS
            ],
            "finding": finding,
            "state": state,
            "pending_total": ChangeProposal.objects.filter(
                target="sources", state=ChangeProposal.PENDING
            )
            .exclude(proposed_value=F("current_value"))
            .count(),
            "receipt": request.session.pop("proposal_receipt", None),
        },
    )


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
        return redirect("review:proposals")

    proposals_by_id = ChangeProposal.objects.in_bulk(list(decisions))
    writes = {}  # record pk -> {field: value}
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
        writes.setdefault(p.record_id, {})[p.field] = value
        p.final_value = value
        (fixed if verb == "fix" else accepted).append(p)

    entry = None
    if writes:
        entry = audited_update_rows(
            request.user,
            Source,
            writes,
            action="proposal:apply",
            reason=f"{len(accepted) + len(fixed)} reviewed changes",
        )

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
        "entry": entry.pk if entry else None,
    }
    return redirect("review:proposals")
