"""The visuals surface (SCOPE.md §2.7 v1, §3).

The only unauthenticated routes in Datadesk: /embed/<slug>/ and
/visuals/<slug>/data.json, for published visuals. The full page keeps
the sign-in wall. Drafts are visible only to signed-in users with a
role, so a visual can be previewed before it is published.

The feed serves the pinned snapshot — the embed stability rule — and
?live=1 runs the data source only where the visual explicitly allows it.
"""

import json

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_exempt

from accounts.decorators import editor_required, role_required
from accounts.roles import role_for_user
from audit.models import AuditLogEntry
from visuals.builder import CHART_KINDS, BuilderError, config_from_form, parse_upload
from visuals.models import BIGQUERY, CORPUS, GCS, INLINE, Visual
from visuals.services import (
    DataSourceError,
    fetch_source_data,
    publish,
    record_snapshot,
    refresh_snapshot,
    unpublish,
)

_LIVE_CACHE_SECONDS = 300


def _get_visual(request, slug):
    visual = Visual.objects.filter(slug=slug).first()
    if visual is None:
        raise Http404("No such visual")
    # Drafts: preview for signed-in users with a role, absent otherwise.
    if visual.status != Visual.PUBLISHED and not (
        request.user.is_authenticated and role_for_user(request.user) is not None
    ):
        raise Http404("No such visual")
    return visual


def _feed_payload(request, visual):
    live = request.GET.get("live") == "1"
    if live and visual.allow_live:

        def fetch():
            return fetch_source_data(visual)

        data = cache.get_or_set(
            f"visuals.live.{visual.slug}", fetch, _LIVE_CACHE_SECONDS
        )
        version = None
    else:
        snapshot = visual.pinned_snapshot
        if snapshot is None:
            # A draft with no pin yet previews from the latest snapshot.
            snapshot = visual.snapshots.order_by("-version").first()
        if snapshot is None:
            raise Http404("No data snapshot yet")
        data = snapshot.data
        version = snapshot.version
    return {"slug": visual.slug, "version": version, "data": data}


def data_json(request, slug):
    visual = _get_visual(request, slug)
    try:
        payload = _feed_payload(request, visual)
    except DataSourceError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    response = JsonResponse(payload)
    if visual.status == Visual.PUBLISHED:
        # Nightly-fresh is the contract; an hour of cache is invisible.
        response["Cache-Control"] = "public, max-age=3600"
    return response


def page(request, slug):
    """The full page — inside the sign-in wall (SCOPE.md §3)."""
    if not (request.user.is_authenticated and role_for_user(request.user)):
        raise Http404("No such visual")
    visual = _get_visual(request, slug)
    return render(
        request,
        "visuals/page.html",
        {"visual": visual, "renderer": f"visuals/renderers/{visual.template}.html"},
    )


@xframe_options_exempt
def embed(request, slug):
    """The iframe-safe embed, framed only by the allowlist."""
    visual = _get_visual(request, slug)
    response = render(
        request,
        "visuals/embed.html",
        {"visual": visual, "renderer": f"visuals/renderers/{visual.template}.html"},
    )
    response["Content-Security-Policy"] = f"frame-ancestors {visual.frame_ancestors}"
    return response


@role_required
def index(request):
    """Published visuals (and drafts, marked) for signed-in users."""
    return render(request, "visuals/index.html", {"visuals": Visual.objects.all()})


# --- the form-driven builder (SCOPE.md §2.7 v2) -----------------------------


@editor_required
def builder_new(request):
    """Pick a data source, get a draft visual with a first snapshot."""
    error = None
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        kind = request.POST.get("source_kind", "")
        if not title:
            error = "A title is required."
        elif kind not in (INLINE, BIGQUERY, GCS):
            error = "Pick a data source."
        else:
            slug = base = slugify(title)[:40] or "visual"
            n = 2
            while Visual.objects.filter(slug=slug).exists():
                slug = f"{base}-{n}"
                n += 1
            visual = Visual(
                slug=slug,
                title=title,
                source_kind=kind,
                query=request.POST.get("query", "").strip(),
                bucket_path=request.POST.get("bucket_path", "").strip(),
                template="builder",
                config={"kind": "table"},
                created_by=request.user,
            )
            try:
                visual.full_clean()
                visual.save()
                if kind == INLINE:
                    upload = request.FILES.get("file")
                    if upload is None:
                        raise BuilderError("Upload a CSV.")
                    rows = parse_upload(upload)
                    record_snapshot(
                        visual,
                        request.user,
                        rows,
                        note=f"uploaded {upload.name}",
                    )
                else:
                    refresh_snapshot(visual, request.user)
            except (BuilderError, DataSourceError) as exc:
                if visual.pk:
                    visual.delete()
                error = str(exc)
            except ValidationError as exc:
                error = "; ".join(
                    f"{field}: {' '.join(messages)}"
                    for field, messages in exc.message_dict.items()
                )
            else:
                return redirect("visuals:builder_edit", visual.slug)
    return render(request, "visuals/builder_new.html", {"error": error})


@editor_required
def builder_edit(request, slug):
    visual = Visual.objects.filter(slug=slug, template="builder").first()
    if visual is None:
        raise Http404("No such builder visual")
    error = None

    if request.method == "POST":
        form = request.POST.get("form")
        try:
            if form == "config":
                config = config_from_form(request.POST)
                visual.config = config
                visual.save(update_fields=["config", "updated_at"])
                AuditLogEntry.objects.create(
                    actor=request.user,
                    action="visual:config",
                    target_table="visuals",
                    target_ids=[visual.slug],
                    after=config,
                    reason=f"builder config for {visual.slug}",
                )
            elif form == "pivot":
                spec = {
                    "shape": request.POST.get("shape") or "",
                    "dimensions": [d for d in request.POST.getlist("dimensions") if d],
                    "measure": request.POST.get("measure") or "articles",
                    "dataset": request.POST.get("f_dataset") or "",
                    "scope": request.POST.get("f_scope") or "",
                    "cin": request.POST.get("f_cin") or "",
                    "from": request.POST.get("f_from") or "",
                    "to": request.POST.get("f_to") or "",
                    "min_articles": request.POST.get("min_articles") or "",
                    "min_publishers": request.POST.get("min_publishers") or "",
                    "enriched_only": bool(request.POST.get("enriched_only")),
                    "news_only": bool(request.POST.get("news_only")),
                }
                if request.POST.get("area_scope"):
                    spec["area_scope"] = request.POST["area_scope"]
                visual.spec = {k: v for k, v in spec.items() if v not in ("", [], None)}
                visual.source_kind = CORPUS
                visual.save(update_fields=["spec", "source_kind", "updated_at"])
                refresh_snapshot(visual, request.user)
            elif form == "refresh":
                refresh_snapshot(visual, request.user)
            elif form == "upload":
                upload = request.FILES.get("file")
                if upload is None:
                    raise BuilderError("Upload a CSV.")
                rows = parse_upload(upload)
                record_snapshot(
                    visual, request.user, rows, note=f"uploaded {upload.name}"
                )
            elif form == "publish":
                publish(visual, request.user)
            elif form == "unpublish":
                unpublish(visual, request.user)
        except (BuilderError, DataSourceError) as exc:
            error = str(exc)
        else:
            return redirect("visuals:builder_edit", visual.slug)

    snapshot = visual.snapshots.order_by("-version").first()
    data = snapshot.data if snapshot else []
    # A story-map payload is layers, not rows; the grid shows its points.
    rows = data.get("points", []) if isinstance(data, dict) else data
    columns = list(rows[0].keys()) if rows else []

    from visuals.corpus import DIMENSIONS, MEASURES

    return render(
        request,
        "visuals/builder_edit.html",
        {
            "visual": visual,
            "snapshot": snapshot,
            "columns": columns,
            "chart_kinds": CHART_KINDS,
            "config_json": json.dumps(visual.config or {}),
            "spec_json": json.dumps(visual.spec or {}),
            "preview_json": json.dumps(rows[:5000]),
            "dimensions": [
                {"key": k, "label": v["label"], "note": v.get("note", "")}
                for k, v in DIMENSIONS.items()
            ],
            "measures": [{"key": k, "label": v["label"]} for k, v in MEASURES.items()],
            "datasets": _dataset_choices(),
            "error": error,
        },
    )


def _dataset_choices():
    from django.db import DatabaseError

    from explorer.models import Dataset

    try:
        return list(Dataset.objects.order_by("label").values("slug", "label"))
    except DatabaseError:
        return []
