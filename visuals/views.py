"""The visuals surface (SCOPE.md §2.7 v1, §3).

The only unauthenticated routes in Datadesk: /embed/<slug>/ and
/visuals/<slug>/data.json, for published visuals. The full page keeps
the sign-in wall. Drafts are visible only to signed-in users with a
role, so a visual can be previewed before it is published.

The feed serves the pinned snapshot — the embed stability rule — and
?live=1 runs the data source only where the visual explicitly allows it.
"""

import json
from urllib.parse import urlencode

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_exempt

from accounts.access import ALL_SCOPES, has_any_grant
from accounts.decorators import APP, requires
from accounts.privileges import DESIGN, READ
from audit.models import AuditLogEntry
from visuals.builder import (
    CHART_KINDS,
    BuilderError,
    config_from_form,
    libs_for,
    parse_upload,
)
from visuals.embed import snippet as embed_snippet
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


def _get_visual(request, slug=None, uuid=None):
    """One visual, found by whichever name the caller was given.

    The console routes by slug because a person reads it. The public host
    routes by uuid because a person pasted it and can never take it back.
    Both reach the same row and the same draft rule.
    """
    if uuid is not None:
        visual = Visual.objects.filter(uuid=uuid).first()
    else:
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


#: The fixed boundary files, by geo level. The per-state ones are chosen
#: from the data itself and cannot be known before it arrives.
_GEO_FILES = {
    "nation": "nation-10m.json",
    "states": "states-10m.json",
    "counties": "counties-10m.json",
}


def _geo_preload(visual):
    """The boundary file a map will want, so the browser can start it now.

    Without this it is the last thing requested and the slowest: the page
    loads, four scripts load, the runtime asks for data.json, and only
    once that resolves does it discover it needs 822KB of county
    outlines. Four round trips deep, on a page inside somebody else's
    article.

    Only the fixed levels. A tract or place map picks its files from the
    GEOIDs in the data, so there is nothing to name until the data lands.
    """
    from django.templatetags.static import static

    name = _GEO_FILES.get((visual.config or {}).get("geo_level"))
    return static(f"geo/{name}") if name else None


def _kind_label(visual):
    """The chosen chart type's own name, for saying what is being drawn."""
    from visuals.types import CHART_TYPES

    kind = (visual.config or {}).get("kind")
    chart = next((c for c in CHART_TYPES if c.id == kind), None)
    return chart.label.lower() if chart else None


def _unmapped_roles(visual):
    """The fields a chart still needs, as a phrase, or None when it has
    them all. `is_complete` answers the whole sentence; this answers only
    the part the preview can do something about."""
    from visuals.types import CHART_TYPES

    kind = (visual.config or {}).get("kind")
    chart = next((c for c in CHART_TYPES if c.id == kind), None)
    if chart is None:
        return None
    picked = (visual.spec or {}).get("roles") or {}
    wanted = [r.label.lower() for r in chart.roles if not picked.get(r.id)]
    if not wanted:
        return None
    if len(wanted) == 1:
        return f"a {wanted[0]} field"
    return ", ".join(wanted[:-1]) + f" and {wanted[-1]} fields"


def _attribution(visual):
    """Who to credit and who to ask, for the datasets this visual draws on.

    Read from the datasets themselves rather than from the visual, because
    the dataset is what the claim is about -- and rather than from the
    grants in `accounts`, which say who may *read* a dataset. Publishing
    those as attribution would put staff account addresses into a feed
    anybody can fetch.

    Datasets with nobody recorded are left out entirely, so a page shows
    real attribution or none, never a row with an empty name in it.
    """
    slugs = visual.datasets or []
    if not slugs:
        return []
    from explorer.models import Dataset

    rows = Dataset.objects.filter(slug__in=slugs).order_by("label")
    out = []
    for dataset in rows:
        if not (dataset.owner_name or dataset.owner_email):
            continue
        out.append(
            {
                "dataset": dataset.label or dataset.slug,
                "owner": dataset.owner_name or "",
                "contact": dataset.owner_email or "",
            }
        )
    return out


def _credit_line(visual):
    """Whose name sits on the chart, and where a reader writes to.

    The consortium publishes what is built here, so that is the default
    and needs no configuration. `credit: "dataset"` names the dataset
    instead -- for a chart built on somebody else's data, where crediting
    the consortium would be taking their work -- and then the name links
    to the contact that dataset publishes.
    """
    if (visual.config or {}).get("credit") != "dataset":
        return None, None
    rows = _attribution(visual)
    if not rows:
        return None, None
    first = rows[0]
    return first["owner"] or first["dataset"], first["contact"]


def _feed_url(visual, by_uuid, version=None, live=False):
    """Where this page's renderer fetches its rows.

    Built here rather than reversed in the template, because the two
    front ends name the same route differently -- the console by slug,
    the public host by uuid -- and the template cannot tell which it is
    rendering under. Guessing is worse than it sounds: a UUID is a valid
    slug, so reversing one against the slug route succeeds and returns a
    URL that 404s.

    The version rides along, or an embed pinned to v3 would frame v3 and
    then fetch whatever is current into it.
    """
    url = reverse("visuals:data", args=[visual.uuid if by_uuid else visual.slug])
    params = {}
    if live:
        params["live"] = "1"
    if version is not None:
        params["v"] = version
    return f"{url}?{urlencode(params)}" if params else url


#: What an embed may pin its colours to. Absent means follow the reader,
#: which is right for a page of our own and wrong for an embed: the person
#: pasting it knows what their article looks like and the reader's laptop
#: does not.
_THEME_STAMPS = ("light", "dark")


def _theme_for(request, visual):
    """Which colours this embed holds still at, in order of who decided.

    The URL wins, because whoever pasted the embed knows what their page
    looks like. Failing that, the visual's own setting, because somebody
    built it light or dark on purpose. Failing that, the reader's device.

    That middle term is the one that was missing. A palette carries both
    a light and a dark variant and its name picks neither, so a visual
    built light had no way to say so and every embed asked the reader --
    which is why one authored light rendered dark.
    """
    asked = request.GET.get("theme", "").strip().lower()
    if asked in _THEME_STAMPS:
        return asked
    chosen = (visual.config or {}).get("theme_mode", "")
    return chosen if chosen in _THEME_STAMPS else None


def _asked_for_version(request):
    """The `?v=` a reader pinned, or None for whatever is current.

    Anything that is not a positive integer is None rather than an error.
    A URL pasted into an article gets mangled -- truncated, appended to,
    passed through a tracker -- and answering with the current version is
    the useful reading of a request nobody meant to malform.
    """
    raw = request.GET.get("v")
    if raw is None:
        return None
    try:
        asked = int(raw)
    except (TypeError, ValueError):
        return None
    return asked if asked > 0 else None


def _feed_payload(request, visual):
    """The rows to serve, and whether the URL that asked can be cached.

    Second return value is the whole point of `?v=`. A version names one
    immutable snapshot, so that response may be cached for a year. A URL
    without one means "current", and caching *that* hard is why a
    republished visual never reached anybody who had already loaded it.
    """
    live = request.GET.get("live") == "1"
    if live and visual.allow_live:

        def fetch():
            return fetch_source_data(visual)

        data = cache.get_or_set(
            f"visuals.live.{visual.slug}", fetch, _LIVE_CACHE_SECONDS
        )
        return {"slug": visual.slug, "version": None, "data": data}, False

    asked = _asked_for_version(request)
    if asked is not None:
        snapshot = visual.snapshots.filter(version=asked).first()
        if snapshot is None:
            # Named a version that does not exist. Not the pinned one
            # instead: a reader asked for v3 and silently getting v7 is
            # the failure `?v=` exists to prevent.
            raise Http404(f"No version {asked} of this visual")
    else:
        snapshot = visual.pinned_snapshot
        if snapshot is None:
            # A draft with no pin yet previews from the latest snapshot.
            snapshot = visual.snapshots.order_by("-version").first()
        if snapshot is None:
            raise Http404("No data snapshot yet")

    payload = {
        "slug": visual.slug,
        "version": snapshot.version,
        # Travels with the rows. Somebody who takes the JSON and republishes
        # it has everything they need to credit it without coming back here.
        "attribution": _attribution(visual),
        "data": snapshot.data,
    }
    return payload, asked is not None


#: A year, for a URL that names one immutable snapshot.
_PINNED = "public, max-age=31536000, immutable"
#: An hour, for one that means "current" and changes when it is republished.
_CURRENT = "public, max-age=3600"


def _cache_for(response, visual, versioned):
    """How long this URL may be believed.

    Only a published visual is cached at all: a draft is a preview, and
    the person previewing it is the one editing it.
    """
    if visual.status != Visual.PUBLISHED:
        response["Cache-Control"] = "no-store"
    else:
        response["Cache-Control"] = _PINNED if versioned else _CURRENT
    return response


def data_json(request, slug=None, uuid=None):
    visual = _get_visual(request, slug=slug, uuid=uuid)
    try:
        payload, versioned = _feed_payload(request, visual)
    except DataSourceError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return _cache_for(JsonResponse(payload), visual, versioned)


def tables_in(data):
    """The named row-lists inside a payload, as (name, rows).

    A feed is not always a list of rows: the map kinds carry
    {meta, areas, points}, named lists beside a metadata object. This
    mirrors `tablesIn` in static/js/datadesk-chart.js deliberately -- the
    table view and the download have to agree about what the data is, or
    a reader sees two tables on the page and gets one of them in the file.
    """
    if isinstance(data, list):
        return [("", data)] if data else []
    if not isinstance(data, dict):
        return []
    return [(k, v) for k, v in data.items() if isinstance(v, list) and v]


def _as_csv(rows):
    """One list of rows as CSV text, columns from the first mapping."""
    import csv as csv_module
    import io

    first = next((r for r in rows if isinstance(r, dict)), None)
    buffer = io.StringIO()
    if first is None:
        # A list of bare values still has a column; it just has no name.
        writer = csv_module.writer(buffer)
        writer.writerow(["value"])
        writer.writerows([[r] for r in rows])
        return buffer.getvalue()
    columns = list(first.keys())
    writer = csv_module.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row if isinstance(row, dict) else {columns[0]: row})
    return buffer.getvalue()


def data_csv(request, slug=None, uuid=None):
    """The same rows as the JSON feed, as a spreadsheet.

    `?table=` names one of the lists for a payload that has several. A
    payload with one list ignores it, and a name that is not there is a
    404 rather than the wrong file -- somebody who asked for the point
    layer and silently received county totals has no way to tell.
    """
    visual = _get_visual(request, slug=slug, uuid=uuid)
    try:
        payload, versioned = _feed_payload(request, visual)
    except DataSourceError as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    groups = tables_in(payload["data"])
    if not groups:
        raise Http404("This visual has no tabular data")

    wanted = request.GET.get("table")
    if wanted:
        groups = [g for g in groups if g[0] == wanted]
        if not groups:
            raise Http404(f"No table called {wanted!r} in this visual")
    name, rows = groups[0]

    stem = f"{visual.slug}-{name}" if name else visual.slug
    version = payload.get("version")
    if version:
        stem = f"{stem}-v{version}"
    response = HttpResponse(_as_csv(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{stem}.csv"'
    return _cache_for(response, visual, versioned)


def _downloads(visual, by_uuid, version=None):
    """Every file a reader can take away: the whole payload as JSON, and
    each row-list as CSV.

    One CSV per list rather than one for the visual. A map carries county
    totals and a point layer, and somebody who wants the totals in a
    spreadsheet should not have to take the points to get them -- the same
    split the table view makes on the page.
    """
    ident = visual.uuid if by_uuid else visual.slug
    base = {"v": version} if version is not None else {}

    def url(route, **extra):
        params = {**base, **extra}
        path = reverse(route, args=[ident])
        return f"{path}?{urlencode(params)}" if params else path

    files = [{"label": "JSON", "note": "the whole payload", "url": url("visuals:data")}]
    snapshot = (
        visual.snapshots.filter(version=version).first()
        if version is not None
        else visual.pinned_snapshot
    )
    for name, rows in tables_in(snapshot.data if snapshot else None):
        files.append(
            {
                "label": f"CSV — {name}" if name else "CSV",
                "note": f"{len(rows):,} rows",
                "url": url("visuals:data_csv", **({"table": name} if name else {})),
            }
        )
    return files


def page(request, slug):
    """The full page — inside the sign-in wall (SCOPE.md §3)."""
    if not (request.user.is_authenticated and has_any_grant(request.user, APP)):
        raise Http404("No such visual")
    visual = _get_visual(request, slug)
    return render(
        request,
        "visuals/page.html",
        {
            "visual": visual,
            "renderer": f"visuals/renderers/{visual.template}.html",
            "feed": _feed_url(visual, by_uuid=False),
            "libs": libs_for((visual.config or {}).get("kind")),
            "credit_name": _credit_line(visual)[0],
            "credit_email": _credit_line(visual)[1],
        },
    )


def public_page(request, slug=None, uuid=None):
    """Where an embed's fallback link lands (ROADMAP item 24).

    `page` above cannot serve this. It keeps the sign-in wall and renders
    inside the console's chrome, and the reader arriving here came from
    somebody else's article with no account and no session -- the data
    front end has no sign-in to offer them.

    Every published snippet points at this URL, and until now it was a 404
    on the host the snippet names. The snippet is shown on the page too:
    a newsroom that finds a visual this way is exactly who needs it.
    """
    visual = _get_visual(request, slug=slug, uuid=uuid)
    asked = _asked_for_version(request)
    shown = visual.snapshots.filter(version=asked).first() if asked else None
    if asked is not None and shown is None:
        raise Http404(f"No version {asked} of this visual")
    response = render(
        request,
        "visuals/public.html",
        {
            "visual": visual,
            "renderer": f"visuals/renderers/{visual.template}.html",
            "feed": _feed_url(visual, by_uuid=uuid is not None, version=asked),
            "downloads": _downloads(visual, by_uuid=uuid is not None, version=asked),
            "attribution": _attribution(visual),
            "libs": libs_for((visual.config or {}).get("kind")),
            "credit_name": _credit_line(visual)[0],
            "credit_email": _credit_line(visual)[1],
            # What the reader is looking at, whether they pinned it or
            # took the current one.
            "shown": shown or visual.pinned_snapshot,
            "pinned_by_url": shown is not None,
        },
    )
    return _cache_for(response, visual, shown is not None)


@xframe_options_exempt
def embed(request, slug=None, uuid=None):
    """The iframe-safe embed, framed only by the allowlist."""
    visual = _get_visual(request, slug=slug, uuid=uuid)
    asked = _asked_for_version(request)
    if asked is not None and not visual.snapshots.filter(version=asked).exists():
        raise Http404(f"No version {asked} of this visual")
    response = render(
        request,
        "visuals/embed.html",
        {
            "visual": visual,
            "renderer": f"visuals/renderers/{visual.template}.html",
            "feed": _feed_url(visual, by_uuid=uuid is not None, version=asked),
            "theme_stamp": _theme_for(request, visual),
            "geo_preload": _geo_preload(visual),
            "libs": libs_for((visual.config or {}).get("kind")),
            "credit_name": _credit_line(visual)[0],
            "credit_email": _credit_line(visual)[1],
        },
    )
    response["Content-Security-Policy"] = f"frame-ancestors {visual.frame_ancestors}"
    return _cache_for(response, visual, asked is not None)


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
            "embed_snippet": embed_snippet(visual),
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


def newsroom_counts_for(scopes):
    """Articles per newsroom for a set of dataset scopes.

    Its own function rather than the body of the view, so `warm_caches`
    can fill the same entries the step will read. A warmer that recomputes
    something adjacent warms nothing.
    """
    from django.core.cache import cache
    from django.db.models import Count

    from explorer.models import Article, DatasetSource
    from visuals.corpus import CORPUS_CACHE_SECONDS, _cache_key

    key = _cache_key("visuals.newsroom_counts", sorted(scopes) if scopes else [])
    counts = cache.get(key)
    if counts is not None:
        return counts
    members = DatasetSource.objects.all()
    if scopes:
        members = members.filter(dataset__slug__in=scopes)
    ids = set(members.values_list("source_id", flat=True))
    counts = {
        str(k): v
        for k, v in Article.objects.filter(candidate_link__source_id__in=ids)
        .values_list("candidate_link__source_id")
        .annotate(n=Count("id"))
        .values_list("candidate_link__source_id", "n")
    }
    cache.set(key, counts, CORPUS_CACHE_SECONDS)
    return counts


@requires(DESIGN)
def newsroom_counts(request, slug):
    """Articles per newsroom, fetched after the step has drawn.

    This is the expensive half of the newsroom step and none of its
    structure: an aggregate over every article in every dataset the visual
    is wired to, where listing the newsrooms themselves is a few hundred
    rows. Counting it inline took the step to 24 seconds and drew nothing
    until it finished.
    """

    visual = _get_visual(request, slug=slug)
    return JsonResponse({"counts": newsroom_counts_for(scopes_of(visual))})


@requires(DESIGN)
def role_values(request, slug, role):
    """Every value of the variable in a role, counted, on demand.

    One aggregate over the corpus per call. It used to run once per role
    while the step rendered -- three for a chord, before anything drew --
    to fill a disclosure that starts closed. Now it runs when somebody
    opens one.
    """
    from visuals.corpus import CorpusSpecError, values_of

    visual = _get_visual(request, slug=slug)
    if not may_act_on(request.user, visual):
        raise PermissionDenied
    chosen = ((visual.spec or {}).get("roles") or {}).get(role, "")
    if not chosen:
        return JsonResponse({"values": []})

    kept = set(((visual.spec or {}).get("only") or {}).get(chosen) or [])
    scopes = scopes_of(visual)
    if not scopes:
        from accounts.privileges import READ
        from explorer.scoping import scopes_for

        scopes = scopes_for(request.user, READ)
    try:
        rows = values_of(chosen, visual.spec or {}, scopes)
    except (CorpusSpecError, DataSourceError) as exc:
        # A facet that cannot be counted leaves the picker working rather
        # than taking the step down with it.
        return JsonResponse({"values": [], "error": str(exc)})
    return JsonResponse(
        {
            "values": [
                {"value": value, "n": n, "kept": not kept or value in kept}
                for value, n in rows
            ]
        }
    )


def _newsroom_tree(visual):
    """State -> county -> newsrooms, for the datasets this visual draws on.

    Built from the sources themselves rather than a stored shape, so a
    publisher added yesterday appears without anything being rebuilt --
    but held for ten minutes once built, because building it counts every
    article in every dataset the visual is wired to, and walking back and
    forth through the builder should not pay that each time.
    """
    from django.core.cache import cache

    from explorer.models import DatasetSource, Source
    from visuals.corpus import CORPUS_CACHE_SECONDS, _cache_key

    scopes = scopes_of(visual)
    # 13 to 24 seconds without this: a count of articles per source across
    # every dataset the visual is wired to, rebuilt on every visit to the
    # step. Keyed on the scopes, because those decide which sources are in
    # it and a key without them would show one author another's newsrooms.
    key = _cache_key("visuals.newsroom_tree", sorted(scopes) if scopes else [])
    hit = cache.get(key)
    if hit is not None:
        return hit

    members = DatasetSource.objects.all()
    if scopes:
        members = members.filter(dataset__slug__in=scopes)
    ids = set(members.values_list("source_id", flat=True))
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
                # Filled in after the page paints, by newsroom_counts
                # below. None rather than 0, so the template can tell
                # "not counted yet" from "counted, and none".
                "count": None,
            }
        )
    for state in tree:
        for county in tree[state]:
            tree[state][county].sort(key=lambda r: r["name"].lower())
    cache.set(key, tree, CORPUS_CACHE_SECONDS)
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
    elif step == "publish":
        extra["actor"] = request.user
    elif step == "fields":
        # Until the data step saves, the visual is wired to nothing and a
        # facet would count over an empty queryset. The author's own scopes
        # stand in while they are still building it.
        extra["user"] = request.user

    panel = getattr(panels, "field_panel" if step == "fields" else f"{step}_panel")
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
            # The preview is a renderer like any other and needs what one
            # needs. Without `feed` the template rendered an empty string,
            # so `fetch("")` re-fetched the builder page itself and the
            # runtime tried to parse HTML: "unexpected character at line 1
            # column 1". Without `libs` no library loaded at all, because
            # an undefined name resolves to "" and "d3" in "" is false.
            "feed": _feed_url(visual, by_uuid=False, live=True),
            "libs": libs_for((visual.config or {}).get("kind")),
            "credit_name": _credit_line(visual)[0],
            "credit_email": _credit_line(visual)[1],
            # What the preview is still waiting for, so it can say so
            # rather than drawing an empty chart that looks like a
            # finished one.
            "missing": _unmapped_roles(visual),
            "kind_label": _kind_label(visual),
            "live": True,
            "error": error,
        }
    )
    return render(request, f"visuals/steps/{step}.html", context)
