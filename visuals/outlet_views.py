"""The outlet registry and its history: pages to read it and to add to it.

Reading is for anybody with something to look at. Recording an event is an
edit to the record of Missouri's newsrooms, so it asks for write access
somewhere -- the same line the review queues draw.
"""

import csv

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.access import has_privilege_anywhere
from accounts.decorators import APP, requires
from accounts.privileges import READ, WRITE
from visuals.models import Outlet, OutletEvent
from visuals.outlet_events import FIELDS, EventError, record

#: The list is a window, not an export; the filter narrows it.
LIST_LIMIT = 400


def _may_record(user):
    return has_privilege_anywhere(user, APP, WRITE)


def _events_for(outlet_id):
    return (
        OutletEvent.objects.filter(outlet_id=outlet_id)
        .select_related("recorded_by", "retracts")
        .prefetch_related("retracted_by")
    )


@requires(READ)
def outlet_index(request):
    """Every outlet in the registry, with how much history each has."""
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    rows = Outlet.objects.all()
    if q:
        rows = rows.filter(
            Q(name__icontains=q)
            | Q(owner__icontains=q)
            | Q(city__icontains=q)
            | Q(county__icontains=q)
            | Q(host__icontains=q)
        )
    if status:
        rows = rows.filter(status=status)
    counts = dict(
        OutletEvent.objects.exclude(outlet_id="")
        .values_list("outlet_id")
        .annotate(n=Count("id"))
        .values_list("outlet_id", "n")
    )
    total = rows.count()
    outlets = list(rows.order_by("name")[:LIST_LIMIT])
    for o in outlets:
        o.event_count = counts.get(o.outlet_id, 0)
    statuses = (
        Outlet.objects.exclude(status="")
        .values_list("status", flat=True)
        .distinct()
        .order_by("status")
    )
    return render(
        request,
        "visuals/outlets/index.html",
        {
            "outlets": outlets,
            "total": total,
            "q": q,
            "status": status,
            "statuses": statuses,
            "unlisted": OutletEvent.objects.filter(outlet_id="").count(),
            "may_record": _may_record(request.user),
        },
    )


@requires(READ)
def outlet_detail(request, outlet_id):
    """One outlet: what it is now, what happened to it, and a form to add."""
    outlet = Outlet.objects.filter(outlet_id=outlet_id).first()
    if outlet is None:
        raise Http404("No such outlet")
    context = {
        "outlet": outlet,
        "events": _events_for(outlet_id),
        "may_record": _may_record(request.user),
        "kinds": OutletEvent.EVENTS[:-1],
        "precisions": OutletEvent.PRECISIONS,
        "statuses": OutletEvent.STATUSES,
        "values": {"outlet_name": outlet.name, "from_owner": outlet.owner},
        "errors": [],
    }
    if request.method == "POST":
        if not context["may_record"]:
            raise PermissionDenied("Recording an event needs write access")
        retract = request.POST.get("retract")
        try:
            if retract:
                target = OutletEvent.objects.filter(
                    pk=retract, outlet_id=outlet_id
                ).first()
                if target is None:
                    raise Http404("No such event")
                record(
                    request.user,
                    {"note": request.POST.get("note", "")},
                    retracts=target,
                )
            else:
                data = {k: request.POST.get(k, "") for k in FIELDS}
                data["outlet_id"] = outlet.outlet_id
                data["outlet_name"] = data["outlet_name"] or outlet.name
                record(request.user, data)
        except EventError as e:
            context["errors"] = e.errors
            context["values"] = request.POST
            return render(request, "visuals/outlets/detail.html", context, status=400)
        return redirect(reverse("visuals:outlet_detail", args=[outlet_id]))
    return render(request, "visuals/outlets/detail.html", context)


@requires(READ)
def event_new(request):
    """An event for an outlet the registry does not hold -- a paper that
    closed before the registry was built, or one launched since."""
    may = _may_record(request.user)
    context = {
        "kinds": OutletEvent.EVENTS[:-1],
        "precisions": OutletEvent.PRECISIONS,
        "statuses": OutletEvent.STATUSES,
        "values": {},
        "errors": [],
        "may_record": may,
    }
    if request.method == "POST":
        if not may:
            raise PermissionDenied("Recording an event needs write access")
        data = {k: request.POST.get(k, "") for k in FIELDS}
        data["outlet_id"] = ""
        data["sets_current"] = ""
        try:
            record(request.user, data)
        except EventError as e:
            context["errors"] = e.errors
            context["values"] = request.POST
            return render(
                request, "visuals/outlets/new_event.html", context, status=400
            )
        return redirect(reverse("visuals:outlet_events"))
    return render(request, "visuals/outlets/new_event.html", context)


#: The columns of the events export, in the order a timeline reads them.
EXPORT = (
    "effective_date",
    "date_precision",
    "outlet_id",
    "outlet_name",
    "event",
    "from_owner",
    "to_owner",
    "merged_into",
    "new_name",
    "new_status",
    "evidence_url",
    "note",
    "retracted",
    "recorded_by",
    "recorded_at",
    "origin",
)


@requires(READ)
def event_list(request):
    """Every event, oldest first: the history, and its CSV for a timeline."""
    events = OutletEvent.objects.select_related("recorded_by").prefetch_related(
        "retracted_by"
    )
    kind = (request.GET.get("event") or "").strip()
    if kind:
        events = events.filter(event=kind)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="outlet-events.csv"'
        out = csv.writer(response)
        out.writerow(EXPORT)
        for e in events:
            out.writerow(
                [
                    e.effective_date.isoformat() if e.effective_date else "",
                    e.date_precision,
                    e.outlet_id,
                    e.outlet_name,
                    e.event,
                    e.from_owner,
                    e.to_owner,
                    e.merged_into,
                    e.new_name,
                    e.evidence_url,
                    " ".join(e.note.split()),
                    "yes" if e.retracted_by.exists() else "",
                    e.recorded_by.email,
                    e.recorded_at.isoformat(timespec="seconds"),
                    e.origin,
                ]
            )
        return response
    return render(
        request,
        "visuals/outlets/events.html",
        {
            "events": events,
            "kind": kind,
            "kinds": OutletEvent.EVENTS,
            "may_record": _may_record(request.user),
        },
    )


# --- the stories list --------------------------------------------------------


@requires(READ)
def story_list(request):
    """The stories list, newest first, with the way to add one."""
    from visuals.models import OutletStory

    q = (request.GET.get("q") or "").strip()
    stories = OutletStory.objects.all()
    if q:
        stories = stories.filter(
            Q(headline__icontains=q)
            | Q(publications__icontains=q)
            | Q(owners__icontains=q)
            | Q(text__icontains=q)
            | Q(source__icontains=q)
        )
    return render(
        request,
        "visuals/outlets/stories.html",
        {"stories": stories, "q": q, "may_record": _may_record(request.user)},
    )


@requires(READ)
def story_edit(request, pk=None):
    """Add a story, or change or remove one. One form, every field on it."""
    from visuals.models import OutletStory
    from visuals.outlet_stories import SOURCES, TEXT_LIMIT, StoryError, delete, save

    story = None
    if pk is not None:
        story = OutletStory.objects.filter(pk=pk).first()
        if story is None:
            raise Http404("No such story")
    if not _may_record(request.user):
        raise PermissionDenied("Adding a story needs write access")
    values = (
        {
            "published": story.published.isoformat(),
            "source": story.source,
            "headline": story.headline,
            "url": story.url,
            "type": story.type,
            "publications": story.publications,
            "owners": story.owners,
            "text": story.text,
        }
        if story
        else {"source": SOURCES[0], "type": OutletStory.OWNERSHIP}
    )
    context = {
        "story": story,
        "values": values,
        "errors": [],
        "sources": SOURCES,
        "types": OutletStory.TYPES,
        "limit": TEXT_LIMIT,
        "outlet_names": Outlet.objects.order_by("name").values_list("name", flat=True),
    }
    if request.method == "POST":
        if story is not None and request.POST.get("delete"):
            delete(request.user, story)
            return redirect(reverse("visuals:outlet_stories"))
        try:
            save(request.user, request.POST, story)
        except StoryError as e:
            context["errors"] = e.errors
            context["values"] = request.POST
            return render(
                request, "visuals/outlets/story_form.html", context, status=400
            )
        return redirect(reverse("visuals:outlet_stories"))
    return render(request, "visuals/outlets/story_form.html", context)


# --- events read from the press ----------------------------------------------

#: A page of the queue: enough to work through, few enough to read.
PAGE = 50


@requires(READ)
def press_queue(request):
    """Events the press reported, waiting for a person: each with its quote,
    its page, and the event it would record."""
    from django.core.paginator import Paginator
    from django.db.models import Case, IntegerField, Value, When

    from visuals.models import HistoryEventCandidate as C
    from visuals.press_history import FLAGS, LABELS, in_context, proposed

    f = {
        k: (request.GET.get(k) or "").strip()
        for k in ("status", "event_type", "flag", "matched", "county", "q")
    }
    f["status"] = f["status"] or C.PENDING
    # Papers placed in a county first: the unplaced are the hardest to
    # decide and should not be the first thing a reviewer meets.
    rows = (
        C.objects.select_related("reviewed_by", "outlet_event")
        .annotate(
            unplaced=Case(
                When(county="", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("unplaced", "county", "town", "paper", "year", "id")
    )
    if f["status"] != "all":
        rows = rows.filter(status=f["status"])
    if f["event_type"]:
        rows = rows.filter(event_type=f["event_type"])
    if f["flag"] == "none":
        rows = rows.filter(flags="")
    elif f["flag"]:
        rows = rows.filter(flags__icontains=f["flag"])
    if f["matched"] == "none":
        rows = rows.filter(matched_to="")
    elif f["matched"]:
        rows = rows.filter(matched_to=f["matched"])
    if f["county"]:
        rows = rows.filter(county__iexact=f["county"])
    if f["q"]:
        rows = rows.filter(
            Q(paper__icontains=f["q"])
            | Q(known_title__icontains=f["q"])
            | Q(town__icontains=f["q"])
            | Q(from_owner__icontains=f["q"])
            | Q(to_owner__icontains=f["q"])
            | Q(quote__icontains=f["q"])
        )
    page = Paginator(rows, PAGE).get_page(request.GET.get("page"))
    for c in page:
        c.proposed = proposed(c)
        c.label = LABELS.get(c.event_type, c.event_type)
        c.before, c.said, c.after = in_context(c)
        c.other_links = [u for u in c.all_links.split() if u != c.link]
    counts = dict(
        C.objects.values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "visuals/outlets/press.html",
        {
            "page": page,
            "f": f,
            "query": query.urlencode(),
            "tallies": [(k, counts.get(k, 0)) for k, _ in C.STATES],
            "states": C.STATES,
            "event_types": LABELS.items(),
            "flags": FLAGS,
            "counties": C.objects.exclude(county="")
            .values_list("county", flat=True)
            .distinct()
            .order_by("county"),
            "kinds": OutletEvent.EVENTS[:-1],
            "precisions": OutletEvent.PRECISIONS,
            "may_record": _may_record(request.user),
        },
    )


@requires(READ)
def press_decide(request, pk):
    """Accept a candidate -- recording its event -- or reject it, with why."""
    from visuals.models import HistoryEventCandidate as C
    from visuals.press_history import LABELS, accept, in_context, proposed, reject

    if request.method != "POST":
        return redirect(reverse("visuals:press_queue"))
    if not _may_record(request.user):
        raise PermissionDenied("Deciding a candidate needs write access")
    back = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse("visuals:press_queue")
    try:
        if request.POST.get("decision") == "reject":
            reject(request.user, pk, request.POST.get("reason", ""))
        else:
            accept(request.user, pk, {k: request.POST.get(k, "") for k in FIELDS})
    except EventError as e:
        c = C.objects.filter(pk=pk).first()
        if c is None:
            raise Http404("No such candidate") from e
        c.proposed = {**proposed(c), **{k: request.POST.get(k, "") for k in FIELDS}}
        c.label = LABELS.get(c.event_type, c.event_type)
        c.before, c.said, c.after = in_context(c)
        return render(
            request,
            "visuals/outlets/press_decide.html",
            {
                "c": c,
                "errors": e.errors,
                "back": back,
                "kinds": OutletEvent.EVENTS[:-1],
                "precisions": OutletEvent.PRECISIONS,
            },
            status=400,
        )
    return redirect(back)
