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
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_exempt

from accounts.access import ALL_SCOPES, has_any_grant
from accounts.decorators import APP, requires
from accounts.privileges import DESIGN, READ
from audit.models import AuditLogEntry
from visuals.builder import CHART_KINDS, BuilderError, config_from_form, parse_upload
from visuals.models import BIGQUERY, CORPUS, GCS, INLINE, Visual
from visuals.services import (
    DataSourceError,
    fetch_source_data,
    may_act_on,
    publish,
    record_snapshot,
    refresh_snapshot,
    scopes_of,
    unpublish,
    visible_to,
)

_LIVE_CACHE_SECONDS = 300


def _get_visual(request, slug):
    visual = Visual.objects.filter(slug=slug).first()
    if visual is None:
        raise Http404("No such visual")
    # Drafts: preview for signed-in users with a role, absent otherwise.
    if visual.status != Visual.PUBLISHED and not (
        request.user.is_authenticated and has_any_grant(request.user, APP)
    ):
        raise Http404("No such visual")
    return visual


def _wired_datasets(user, spec):
    """The dataset slugs a corpus visual draws on, frozen at save.

    A spec naming a dataset wires the visual to that one — and only if
    the author may read it, so a slug typed into a form cannot reach past
    their grants. A spec naming none wires it to every dataset the author
    can read, because that is what the aggregate will cover.

    Application-wide access is expanded to actual slugs here rather than
    stored as "everything". A visual is a claim about particular data:
    "all datasets" recorded today would quietly come to mean a dataset
    added next month, which the author never saw.
    """
    from explorer.models import Dataset
    from explorer.scoping import scopes_for

    readable = scopes_for(user, READ)
    if readable is ALL_SCOPES:
        readable = set(Dataset.objects.values_list("slug", flat=True))

    named = (spec or {}).get("dataset")
    if named:
        return [named] if named in readable else []
    return sorted(readable)


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
    if not (request.user.is_authenticated and has_any_grant(request.user, APP)):
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


@requires(READ)
def index(request):
    """Visuals this person may see: theirs, ones wired to a dataset they
    own, and everything if they are an admin.

    Filtered in Python rather than in the query. The rule is a union of
    three conditions, one of which crosses to the crawler's database --
    dataset ownership is a grant here, dataset membership is a slug
    there -- so a single queryset cannot express it. The list is small
    (visuals are authored by hand, not generated) and the alternative is
    a query that looks clever and is wrong at the join.
    """
    visuals = []
    for v in Visual.objects.all():
        if not visible_to(request.user, v):
            continue
        # The edit link follows the visual, not the privilege: a viewer
        # sees every published visual and may act on none of them.
        v.actionable = may_act_on(request.user, v)
        visuals.append(v)
    return render(request, "visuals/index.html", {"visuals": visuals})


# --- the form-driven builder (SCOPE.md §2.7 v2) -----------------------------


@requires(DESIGN)
def builder_new(request):
    """Pick a data source, get a draft visual with a first snapshot."""
    error = None
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        kind = request.POST.get("source_kind", "")
        if not title:
            error = "A title is required."
        elif kind not in (CORPUS, INLINE, BIGQUERY, GCS):
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
                # No kind: step one asks. Seeding "table" answered its
                # question before anybody was asked it, and the gallery
                # then opened with a choice nobody had made.
                config={},
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
                elif kind != CORPUS:
                    refresh_snapshot(visual, request.user)
                # A corpus visual has nothing to snapshot yet: what it draws
                # is decided by the steps, and asking now would fail on a
                # spec nobody has written.
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
                # Into the builder, at its first step. The old form still
                # exists for what the steps do not cover yet, and is linked
                # from every one of them -- but it is not where somebody
                # who has just made a visual should land.
                return redirect("visuals:builder_step", visual.slug, "type")
    return render(request, "visuals/builder_new.html", {"error": error})


@requires(DESIGN)
def builder_edit(request, slug):
    visual = Visual.objects.filter(slug=slug, template="builder").first()
    if visual is None:
        raise Http404("No such builder visual")
    if not may_act_on(request.user, visual):
        # Holding `design` says they build visuals; it does not say they
        # build *this* one. A published visual is visible to anyone signed
        # in, so the edit page has to check the visual and not only the
        # privilege -- otherwise seeing it would be enough to change it.
        raise PermissionDenied("Not yours to edit")
    error = None

    if request.method == "POST":
        form = request.POST.get("form")
        try:
            if form == "config":
                from visuals.geofocus import state_of

                config = config_from_form(request.POST, state_of(visual.datasets))
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
                    "publisher_county": request.POST.get("f_publisher_county") or "",
                    "publisher_city": request.POST.get("f_publisher_city") or "",
                    "from": request.POST.get("f_from") or "",
                    "to": request.POST.get("f_to") or "",
                    "min_articles": request.POST.get("min_articles") or "",
                    "min_publishers": request.POST.get("min_publishers") or "",
                    "enriched_only": bool(request.POST.get("enriched_only")),
                    "news_only": bool(request.POST.get("news_only")),
                    "labeled_only": bool(request.POST.get("labeled_only")),
                }
                if request.POST.get("area_scope"):
                    spec["area_scope"] = request.POST["area_scope"]
                visual.spec = {k: v for k, v in spec.items() if v not in ("", [], None)}
                visual.source_kind = CORPUS
                # Freeze what this visual is wired to, now, from what its
                # author may read (ROADMAP item 1). A named dataset wires
                # it to that one; no name wires it to everything the
                # author could see, which is what the query will actually
                # aggregate. Recomputed on every pivot save, because
                # changing the spec changes the wiring.
                visual.datasets = _wired_datasets(request.user, visual.spec)
                visual.save(
                    update_fields=["spec", "source_kind", "datasets", "updated_at"]
                )
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


def _readable_datasets(user):
    """The datasets this person may draw on, as the picker's options.

    Offering one they cannot read invites a refusal on the next screen;
    `datasets_for` is the same helper every other scoped picker uses.
    """
    from django.db import DatabaseError

    from explorer.scoping import datasets_for

    try:
        return list(datasets_for(user, READ).order_by("label").values("slug", "label"))
    except DatabaseError:
        return []


def _dataset_choices():
    from django.db import DatabaseError

    from explorer.models import Dataset

    try:
        return list(Dataset.objects.order_by("label").values("slug", "label"))
    except DatabaseError:
        return []


@requires(DESIGN)
def builder_type(request, slug):
    """Step one: pick a visualization type.

    The gallery, grouped by the question each family answers, with types
    that cannot be built from the data on hand greyed and carrying the
    reason. A type is never hidden — knowing a dot map exists and needs
    coordinates is worth more than not knowing it exists.
    """
    from visuals.types import BY_ID, FAMILIES, column_types, gallery

    visual = _get_visual(request, slug)
    if not may_act_on(request.user, visual):
        raise PermissionDenied("This visual is not yours to change.")

    snapshot = visual.snapshots.order_by("-version").first()
    data = snapshot.data if snapshot else []
    rows = data.get("points", []) if isinstance(data, dict) else data
    available = column_types(rows)

    if request.method == "POST":
        chosen = request.POST.get("kind", "")
        if chosen not in BY_ID:
            raise Http404("No such chart type")
        # Keep everything else. Changing the type is the only step that can
        # invalidate an earlier choice, and the rule is to keep the choice
        # and mark it unusable rather than empty the form (ROADMAP item 20).
        config = dict(visual.config or {})
        config["kind"] = chosen
        visual.config = config
        visual.save(update_fields=["config", "updated_at"])
        AuditLogEntry.objects.create(
            actor=request.user,
            action="visual:type",
            target_table="visuals",
            target_ids=[visual.slug],
            after={"kind": chosen},
            reason=f"chart type for {visual.slug}",
        )
        return redirect("visuals:builder_edit", visual.slug)

    entries = gallery(available, len(rows))
    chosen = (visual.config or {}).get("kind", "")
    grouped = [
        {
            "family": family,
            "types": [e for e in entries if e["family"] == family],
        }
        for family in FAMILIES
    ]
    return render(
        request,
        "visuals/builder_type.html",
        {
            "visual": visual,
            "groups": [g for g in grouped if g["types"]],
            "empty_families": [g["family"] for g in grouped if not g["types"]],
            "chosen": chosen,
            "available": sorted(available.items()),
            "has_data": bool(rows),
            "row_count": len(rows),
        },
    )


# --- the builder, one step at a time -----------------------------------------


#: Stands in for a state or county a publisher record does not carry. One
#: sentinel so the grouping has a key; the label says which field is
#: missing, because "Not recorded" under "Not recorded" tells a reader that
#: something is absent without saying what.
UNRECORDED = "\u0000unrecorded"


def _newsroom_tree(visual):
    """State -> county -> newsrooms, for the datasets this visual draws on.

    Built from the sources themselves rather than a cached shape: a
    publisher added yesterday should appear without anything being rebuilt.
    """
    from django.db.models import Count

    from explorer.models import Article, DatasetSource, Source

    scopes = scopes_of(visual)
    members = DatasetSource.objects.all()
    if scopes:
        members = members.filter(dataset__slug__in=scopes)
    ids = set(members.values_list("source_id", flat=True))
    counts = dict(
        Article.objects.filter(candidate_link__source_id__in=ids)
        .values_list("candidate_link__source_id")
        .annotate(n=Count("id"))
        .values_list("candidate_link__source_id", "n")
    )
    tree = {}
    for source in Source.objects.filter(id__in=ids):
        # A publisher record needs a state and a county. One missing is not
        # a place called "?" -- it is a record the scan already flags, and
        # saying so is more use than a punctuation mark nobody can act on.
        state = ((source.meta or {}).get("state") or "").strip() or UNRECORDED
        county = (source.county or "").strip() or UNRECORDED
        tree.setdefault(state, {}).setdefault(county, []).append(
            {
                "id": source.id,
                "name": source.canonical_name or source.host,
                "count": counts.get(source.id, 0),
            }
        )
    for state in tree:
        for county in tree[state]:
            tree[state][county].sort(key=lambda r: -r["count"])
    return tree


@requires(DESIGN)
def builder_step(request, slug, step):
    """One step of the builder.

    Every step renders the same shell -- the sentence, the rail, the
    preview -- and swaps the panel. A step writes only the keys it owns, so
    coming back to an earlier one changes that choice and leaves the rest
    (ROADMAP item 20).
    """
    from visuals import panels
    from visuals.sentence import is_complete, parts_for
    from visuals.steps import BY_SLUG, STEPS, next_after, reached

    if step not in BY_SLUG:
        raise Http404("No such step")
    visual = _get_visual(request, slug)
    if not may_act_on(request.user, visual):
        raise PermissionDenied("This visual is not yours to change.")

    here = BY_SLUG[step]
    extra = {}
    if step == "data":
        extra["choices"] = _readable_datasets(request.user)
    elif step == "newsrooms":
        extra["tree"] = _newsroom_tree(visual)
    elif step == "fields":
        # Until the data step saves, the visual is wired to nothing and a
        # facet would count over an empty queryset. The author's own scopes
        # stand in while they are still building it.
        extra["user"] = request.user

    panel = getattr(panels, f"{step}_panel" if step != "fields" else "field_panel")
    error = ""

    if request.method == "POST":
        try:
            written = panel(visual, request.POST, **extra)
        except ValueError as exc:
            error = str(exc)
        else:
            fields = []
            for holder, values in written.items():
                current = dict(getattr(visual, holder) or {})
                current.update(values)
                setattr(visual, holder, current)
                fields.append(holder)
            if "spec" in written:
                visual.source_kind = CORPUS
                visual.datasets = _wired_datasets(request.user, visual.spec)
                fields.append("datasets")
            visual.save(update_fields=[*fields, "updated_at"])
            AuditLogEntry.objects.create(
                actor=request.user,
                action=f"visual:{step}",
                target_table="visuals",
                target_ids=[visual.slug],
                after=dict(*written.values()),
                reason=f"{here.label.lower()} for {visual.slug}",
            )
            if request.POST.get("stay"):
                return redirect("visuals:builder_step", visual.slug, step)
            onward = next_after(step)
            return redirect("visuals:builder_step", visual.slug, onward or step)

    context = panel(visual, **extra)
    done = reached(visual)
    context.update(
        {
            "visual": visual,
            "step": here,
            "steps": [
                {
                    "slug": s.slug,
                    "label": s.label,
                    "on": s.slug == step,
                    "done": s.slug in done,
                    # Nothing after the type can be decided until there is
                    # one, so those are shown and refused rather than hidden.
                    "ready": s.slug == "type" or "type" in done,
                }
                for s in STEPS
            ],
            "sentence": parts_for(visual, step),
            "complete": is_complete(visual),
            "renderer": f"visuals/renderers/{visual.template}.html",
            "live": True,
            "error": error,
        }
    )
    return render(request, f"visuals/steps/{step}.html", context)
