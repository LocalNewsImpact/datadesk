"""The visuals surface (SCOPE.md §2.6 v1, §3).

The only unauthenticated routes in Datadesk: /embed/<slug>/ and
/visuals/<slug>/data.json, for published visuals. The full page keeps
the sign-in wall. Drafts are visible only to signed-in users with a
role, so a visual can be previewed before it is published.

The feed serves the pinned snapshot — the embed stability rule — and
?live=1 runs the data source only where the visual explicitly allows it.
"""

from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt

from accounts.decorators import role_required
from accounts.roles import role_for_user
from visuals.models import Visual
from visuals.services import DataSourceError, fetch_source_data

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
