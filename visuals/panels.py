"""What each builder step reads and writes.

One function per step. Each takes the visual and the POST, writes only the
keys its `Step` declares, and returns the context its panel renders with.
Nothing here renders; nothing here touches another step's keys. That second
rule is what makes going back cost nothing, and `tests/test_chart_types.py`
asserts no two steps claim the same key.

The sentence across the top is assembled from the same state, so what a
panel writes and what the page says can never drift.
"""

from datasets.geo import state_name
from visuals.types import BY_ID, FAMILIES, column_types, gallery

# The runtime's own themes (static/js/datadesk-chart.js). Named here so the
# panel offers what exists rather than a list that goes stale beside it; a
# test holds the two together.
#: The runtime's own themes and the first colours each one uses, so the
#: panel shows the palette rather than its name. Somebody choosing colours
#: is choosing colours; "Mizzou" tells them nothing until they have picked
#: it and looked at the chart.
#:
#: Copied from `static/js/datadesk-chart.js` because Python cannot read it,
#: and held to it by `tests/test_chart_types.py` -- a palette changed there
#: and not here would show the wrong swatch, which is worse than no swatch.
THEMES = (
    (
        "datadesk",
        "Datadesk",
        ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"),
    ),
    (
        "lnic",
        "LNIC",
        ("#00618f", "#eb6834", "#59bbeb", "#eda100", "#e87ba4", "#008300"),
    ),
    (
        "mizzou",
        "Mizzou",
        ("#d9a018", "#a31414", "#2a78d6", "#1baf7a", "#e87ba4", "#008300"),
    ),
    (
        "rji",
        "RJI",
        ("#1c5e90", "#d9a018", "#1baf7a", "#eb6834", "#2a78d6", "#e87ba4"),
    ),
)

THEME_IDS = {t[0] for t in THEMES}


def _rows(visual):
    snapshot = visual.snapshots.order_by("-version").first()
    data = snapshot.data if snapshot else []
    return data.get("points", []) if isinstance(data, dict) else data


# --- step 1: the chart type --------------------------------------------------


def type_panel(visual, post=None):
    rows = _rows(visual)
    available = column_types(rows)
    if post is not None:
        chosen = post.get("kind", "")
        if chosen not in BY_ID:
            raise ValueError("No such chart type")
        # Changing the type is the only step that can invalidate an earlier
        # choice. The rule is to keep the choice and mark it unusable, never
        # to empty the form (ROADMAP item 20).
        return {"config": {"kind": chosen}}
    entries = gallery(available, len(rows))
    grouped = [
        {"family": f, "types": [e for e in entries if e["family"] == f]}
        for f in FAMILIES
    ]
    return {
        "groups": [g for g in grouped if g["types"]],
        "empty_families": [
            f for f in FAMILIES if not any(e["family"] == f for e in entries)
        ],
        "chosen": (visual.config or {}).get("kind", ""),
        "available": sorted(available.items()),
        "row_count": len(rows),
    }


# --- step 2: the colours -----------------------------------------------------


def theme_panel(visual, post=None):
    if post is not None:
        name = post.get("theme", "")
        if name not in THEME_IDS:
            raise ValueError("No such theme")
        config = {"theme": name}
        # The chart's own words. Separate from `visual.title`, which names
        # the record in the console: one is how a reader is introduced to
        # the picture, the other is how an author finds it again. Blank
        # stores blank, so a chart can carry no title at all.
        config["title"] = post.get("title", "").strip()
        config["subtitle"] = post.get("subtitle", "").strip()
        # Light or dark, or neither. A palette has both variants and the
        # name picks neither of them, so until now "I made it light" was
        # not recorded anywhere and the embed asked the reader's laptop.
        mode = post.get("theme_mode", "")
        config["theme_mode"] = mode if mode in ("light", "dark") else ""
        # A fixed taxonomy is what keeps one CIN need the same colour in
        # every chart. It belongs to this step because it is a colour
        # decision, not a data one.
        config["taxonomy"] = "cin" if post.get("taxonomy") else ""
        return {"config": config}
    config = visual.config or {}
    return {
        "themes": [
            {
                "id": i,
                "label": label,
                "colours": colours,
                "on": config.get("theme", "datadesk") == i,
            }
            for i, label, colours in THEMES
        ],
        "taxonomy": config.get("taxonomy") == "cin",
        "theme_mode": config.get("theme_mode", ""),
        # Falls back to the record's name, so a new visual arrives with a
        # sensible title in the box rather than an empty one.
        "title": config.get("title", "") or visual.title,
        "subtitle": config.get("subtitle", ""),
    }


# --- step 3: the slice -------------------------------------------------------


def _SUBSETS():
    from visuals.corpus import SUBSETS

    return SUBSETS


def _subset_of(spec):
    from visuals.corpus import COMPLETE

    return spec.get("subset") or COMPLETE


#: The kinds that are drawn on a map and so have somewhere to be centred.
_MAP_KINDS = ("storymap", "choropleth", "points")


def _focus_from(post, datasets):
    """The place a map is centred on, resolved from what somebody typed.

    "Boone" is a name; the renderer needs 29019, because that is what the
    boundary file is keyed by. The gazetteer does that here rather than
    asking an author to look up a FIPS code.

    The name is kept beside the code. Without it the box came back showing
    "29019" to somebody who typed "Boone", which reads as the field having
    been misunderstood.
    """
    from visuals.geofocus import AUTO, FocusError, frame, resolve, state_of

    typed = (post.get("focus") or "").strip()
    if not typed:
        # Cleared on purpose: an uncentred map frames itself on its data.
        # Cleared on purpose: back to following the newsrooms.
        return {
            "focus": "",
            "focus_name": "",
            "focus_level": "",
            "extent": AUTO,
            "frame": [],
        }

    default_state = state_of(datasets)
    try:
        geoid, level = resolve(typed, post.get("focus_level", ""), default_state)
        extent = post.get("extent", AUTO) or AUTO
        counties = frame(
            geoid, level, extent, post.get("extent_custom", ""), default_state
        )
    except FocusError as exc:
        raise ValueError(str(exc)) from exc

    return {
        "focus": geoid,
        "focus_name": typed,
        "focus_level": level,
        "extent": extent,
        "frame": counties,
    }


def data_panel(visual, post=None, choices=()):
    """Datasets and a date range. Which fields to draw comes later."""
    if post is not None:
        picked = [s for s in post.getlist("datasets") if s]
        allowed = {d["slug"] for d in choices}
        outside = set(picked) - allowed
        if outside:
            # A slug typed into a form must not reach past the author's
            # grants, the same rule the spec's single-dataset field follows.
            raise ValueError(f"Not yours to draw on: {sorted(outside)}")
        from visuals.corpus import COMPLETE, SUBSETS

        subset = post.get("subset", COMPLETE)
        if subset not in SUBSETS:
            raise ValueError("No such subset")
        written = {
            "spec": {
                "datasets": picked,
                # The singular key is older and `_base_queryset` still
                # filters on it, so leaving it unwritten leaves a filter
                # nothing in the flow shows -- the same shape as
                # `publisher_county`, which ANDed a county nobody could
                # see with a newsroom choice and matched nothing. It
                # follows the plural: one dataset names itself, several
                # name none.
                "dataset": picked[0] if len(picked) == 1 else "",
                "subset": subset,
                "from": post.get("from", "").strip(),
                "to": post.get("to", "").strip(),
            }
        }
        return written
    spec = visual.spec or {}
    picked = spec.get("datasets") or ([spec["dataset"]] if spec.get("dataset") else [])
    return {
        "datasets": [
            {"slug": d["slug"], "label": d["label"], "on": d["slug"] in picked}
            for d in choices
        ],
        "chosen_count": len(picked),
        # The dataset says which newsrooms; the subset says how much of
        # what they published. Two questions, two controls.
        "subsets": [
            {"id": i, "label": label, "note": note, "on": _subset_of(spec) == i}
            for i, (label, note) in _SUBSETS().items()
        ],
        "date_from": spec.get("from", ""),
        "date_to": spec.get("to", ""),
    }


# --- step 4: the newsrooms ---------------------------------------------------


#: What a missing value is called at each rung. Named per level -- "NA
#: State" over "NA County" says which field the record is short of, where
#: one word repeated says only that something is.
UNRECORDED = "\u0000unrecorded"
NA_STATE = "NA State"
NA_COUNTY = "NA County"


def _frame_from_newsrooms(publishers, visual):
    """The counties a map should paint, taken from the newsrooms chosen.

    Their own counties, not the places their stories mention: the question
    the newsroom step asks is whose coverage this is, and the answer to
    "where is this map about" is where those newsrooms are. Stories
    reaching further still plot as points on top.

    Empty selection means every newsroom in the datasets, which is a
    frame nobody drew deliberately -- so it frames itself on its data
    instead, which is what an empty focus already means.
    """
    from django.db import DatabaseError

    from explorer.models import Source
    from visuals.geofocus import COUNTY, FocusError, resolve, state_of

    if not publishers:
        return {"focus": "", "focus_name": "", "focus_level": "", "frame": []}

    state = state_of(visual.datasets or [])
    try:
        names = Source.objects.filter(id__in=publishers).values_list(
            "county", flat=True
        )
        counties = set()
        for name in names:
            if not (name or "").strip():
                continue
            try:
                geoid, _ = resolve(name, COUNTY, state)
            except FocusError:
                # A county the gazetteer does not know is left out of the
                # frame rather than taking the frame down with it.
                continue
            if geoid:
                counties.add(geoid)
        counties = sorted(counties)
    except DatabaseError:
        # A frame that cannot be worked out is not a reason to lose the
        # newsroom selection, which is what the author actually asked for.
        return {}

    return {
        "focus": "",
        "focus_name": "",
        "focus_level": "",
        "extent": "selected" if counties else "auto",
        "frame": counties,
    }


def _newsroom_count(tree):
    """How many newsrooms the picker is offering."""
    return sum(len(rooms) for state in tree.values() for rooms in state.values())


def newsrooms_panel(visual, post=None, tree=None):
    """State, then county, then newsroom.

    An empty list means every one of them. Storing exclusions instead would
    make "all" a list that goes stale the moment a publisher is added.
    """
    if post is not None:
        spec_now = visual.spec or {}
        picked = [p for p in post.getlist("publishers") if p]
        # Everything ticked is not a filter. Storing all of them made the
        # selection a snapshot that goes stale the moment a newsroom is
        # added -- and one such spec holds 942 publishers across four
        # states, which is every newsroom that existed on the day the box
        # was ticked and no statement about what the visual is for.
        if tree is not None and len(picked) >= _newsroom_count(tree):
            picked = []
        written = {
            "spec": {
                "publishers": picked,
                # Two older keys naming the same thing, from before this
                # step existed. Nothing in the flow shows them, so nobody
                # could see that a map filtered to Jackson's newsrooms
                # also carried `publisher_county: Boone` -- and the two
                # AND together to nothing. Choosing newsrooms is the one
                # way to choose newsrooms.
                "publisher_county": "",
                "publisher_city": "",
            }
        }
        # Choosing newsrooms is choosing where the map is about. Asking
        # again, in a different step, was a second way to say the same
        # thing -- and the two could disagree, which is how a copy of the
        # Boone map retargeted at Jackson ended up framed on Adair and
        # drawing nothing.
        #
        # Skipped where somebody has typed a place on purpose: an override
        # that the next newsroom change silently undid would be worse than
        # no override.
        config = visual.config or {}
        if config.get("kind") in _MAP_KINDS:
            # The frame follows whichever of the two the author just
            # changed. Both are on this page and either may decide it, so
            # what settles it is which one was touched -- not which is
            # non-empty, because the focus box is rendered holding the
            # focus already stored and therefore posts one back on every
            # save. Reading that as somebody typing meant a map that had
            # ever been focused could never be re-framed by choosing
            # different newsrooms again: a copy of the Boone map, pointed
            # at St. Louis, kept `focus_name: jackson` from the county
            # before that -- and the box responsible sits inside a fold
            # that is only open when it is already set.
            #
            # Typing is saying something the box did not already say,
            # which is how the fields step tells a facet somebody opened
            # from one they never touched.
            #
            # Keeping a typed place across a newsroom change is still
            # sayable, and takes saying: the change clears it, and typing
            # it again then differs from what is stored.
            typed = (post.get("focus") or "").strip()
            before = (config.get("focus_name") or config.get("focus") or "").strip()
            moved = sorted(picked) != sorted(spec_now.get("publishers") or [])
            if typed == before and moved:
                typed = ""
            written["config"] = (
                # `visual.datasets` -- the frozen list -- not
                # `spec["datasets"]`. The two are different: the spec's is
                # what the data step last wrote, and this one is what the
                # visual is actually wired to. Reading the wrong one left
                # the state empty, so "Jackson" could not be resolved and
                # the step refused a save it should have made.
                _focus_from(post, visual.datasets or [])
                if typed
                else _frame_from_newsrooms(picked, visual)
            )
        return written
    spec = visual.spec or {}
    kept = set(spec.get("publishers") or ())
    states = []
    # The sentinel sorts before every real name; a row for what is
    # missing belongs after the places that are not.
    for code in sorted(tree or {}, key=lambda c: (c == UNRECORDED, c)):
        counties = []
        for county in sorted(tree[code], key=lambda c: (c == UNRECORDED, c)):
            county_label = NA_COUNTY if county == UNRECORDED else county
            rooms = tree[code][county]
            counties.append(
                {
                    "name": county_label,
                    # No counts. Article totals per newsroom are an
                    # aggregate over the corpus and arrive after the step
                    # has drawn, filled in by the page and rolled up into
                    # the county there -- so nothing here adds them, and
                    # nothing here waits for them.
                    "rooms": [
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "on": not kept or r["id"] in kept,
                        }
                        for r in rooms
                    ],
                }
            )
        states.append(
            {
                "code": code,
                # MO is stored; Missouri is read. A record carrying no state
                # says which field it is short of, rather than one word that
                # says only that something is absent.
                "label": (
                    NA_STATE if code == UNRECORDED else (state_name(code) or code)
                ),
                "unrecorded": code == UNRECORDED,
                "counties": counties,
                "rooms": sum(len(c["rooms"]) for c in counties),
            }
        )
    total = sum(s["rooms"] for s in states)
    config = visual.config or {}
    return {
        "states": states,
        "kept": len(kept) or total,
        "total": total,
        # The override, shown only where there is a map to frame.
        "is_map": config.get("kind") in _MAP_KINDS,
        "focus": config.get("focus_name") or config.get("focus", ""),
        "focus_level": config.get("focus_level", ""),
        "extent": config.get("extent", "auto"),
    }


# --- step 5: the fields ------------------------------------------------------
#
# This is where the pivot is decided, which the first prototype assumed had
# already happened. A role offers the variables whose type fits it, and once
# one is chosen its values can be narrowed.


#: Every slot any chart kind names a column with. The fields step writes
#: all of them on every save, because a slot left alone is one the last
#: chart type filled in and this one does not draw.
_EVERY_SLOT = tuple(
    sorted({role.id for chart in BY_ID.values() for role in chart.roles})
)


def unmapped_fields(visual):
    """What a chart still needs before it can draw, as a phrase, or None.

    Required roles only. An optional role reported as missing says the
    preview is waiting for something it is not waiting for -- a bar draws
    perfectly well without a series -- and once publishing asks this
    question too, saying so would refuse a chart that is finished.

    A table has no roles and needs columns instead, which the sentence at
    the top of the page never mentions: it says what a visual is *of*,
    and columns are not one of those things. Nothing else would notice an
    empty one, so an embed of it would render a table of nothing.
    """
    config, spec = visual.config or {}, visual.spec or {}
    chart = BY_ID.get(config.get("kind", ""))
    if chart is None:
        return None
    if _picks_columns(chart):
        return None if (spec.get("dimensions") or []) else "columns to show"
    picked = spec.get("roles") or {}
    wanted = [r.label.lower() for r in chart.roles if r.needs and not picked.get(r.id)]
    if not wanted:
        return None
    if len(wanted) == 1:
        return f"a {wanted[0]} field"
    return ", ".join(wanted[:-1]) + f" and {wanted[-1]} fields"


def _picks_columns(chart):
    """Whether this kind groups by whatever is ticked.

    A chart with roles fills them; a story map has neither because its
    two layers come out of the enrichment whole. What is left is a table,
    which is the rows themselves and so groups by anything -- and had no
    way to say so at all, which meant the pivot refused it for having no
    dimensions and a table could not be built.
    """
    from visuals.services import STORY_MAP_KIND

    return not chart.roles and chart.id != STORY_MAP_KIND


def _variables():
    """Every dimension and measure a pivot can use, with what it holds."""
    from visuals.corpus import DIMENSIONS, MEASURES

    kinds = {
        "month": "date",
        "year": "date",
        "geo_state": "geo",
        "geo_county": "geo",
        "geo_place": "geo",
    }
    out = [
        {"id": k, "label": v["label"], "kind": kinds.get(k, "text"), "measure": False}
        for k, v in DIMENSIONS.items()
    ]
    out += [
        {"id": k, "label": v["label"], "kind": "number", "measure": True}
        for k, v in MEASURES.items()
    ]
    return out


VARIABLES = None


def variables():
    global VARIABLES
    if VARIABLES is None:
        VARIABLES = _variables()
    return VARIABLES


def _plainly(role):
    """What a slot takes, in the words the gallery uses."""
    from visuals.types import _PLAIN

    words = [_PLAIN.get(a, a) for a in role.accepts]
    if len(words) == 1:
        return words[0]
    return " or ".join([", ".join(words[:-1]), words[-1]])


def _values_for(visual, dim_key, kept, user=None):
    """[(value, count, kept)] for a chosen dimension, or [].

    Narrowed by the spec already built, so what is offered is present in
    the author's slice: offering a county with no articles invites a filter
    that empties the chart. Failures are swallowed -- a facet that cannot
    be counted should leave the picker working, not take the page down.

    A visual is wired to its datasets when the data step saves, and until
    then `scopes_of` is empty, which narrows to nothing. An author part way
    through building sees the values they may read rather than none at all;
    what the visual is finally wired to still decides what it draws.
    """
    if not dim_key or any(v["id"] == dim_key and v["measure"] for v in variables()):
        return []
    from django.db import DatabaseError

    from visuals.corpus import CorpusSpecError, values_of
    from visuals.services import scopes_of

    scopes = scopes_of(visual)
    if not scopes and user is not None:
        from accounts.privileges import READ
        from explorer.scoping import scopes_for

        scopes = scopes_for(user, READ)

    try:
        rows = values_of(dim_key, visual.spec or {}, scopes)
    except (CorpusSpecError, DatabaseError):
        return []
    return [
        {"value": value, "count": count, "on": not kept or value in kept}
        for value, count in rows
    ]


def field_panel(visual, post=None, user=None):
    config, spec = visual.config or {}, visual.spec or {}
    chart = BY_ID.get(config.get("kind", ""))
    if post is not None:
        if chart is None:
            raise ValueError("Pick a chart type first")
        known = {v["id"] for v in variables()}
        roles, dimensions, measure = {}, [], ""
        if _picks_columns(chart):
            # A table is the rows themselves, so it groups by whatever
            # somebody ticks rather than by filling slots with meanings a
            # table does not have. The pivot takes a list of dimensions,
            # which is what this is.
            columns = {v["id"] for v in variables() if not v["measure"]}
            dimensions = [c for c in post.getlist("columns") if c in columns]
            if not dimensions:
                raise ValueError("Pick at least one column to group by")
            measure = post.get("measure", "").strip()
            numbers = {v["id"] for v in variables() if v["measure"]}
            if measure and measure not in numbers:
                raise ValueError(f"No such count: {measure}")
        for role in chart.roles:
            picked = post.get(f"role-{role.id}", "").strip()
            if not picked:
                continue
            if picked not in known:
                raise ValueError(f"No such variable: {picked}")
            roles[role.id] = picked
            if any(v["id"] == picked and v["measure"] for v in variables()):
                measure = picked
            elif picked not in dimensions:
                dimensions.append(picked)
        only = {}
        for key, values in post.lists():
            if not key.startswith("only-"):
                continue
            kept = [v for v in values if v]
            dim = key[5:]
            # Every value ticked is not a filter. Storing them all would go
            # stale the moment one is added, which is why the newsroom step
            # stores nothing for "all" either.
            #
            # Counting how many there are is the expensive part, so it runs
            # only when the answer can differ from what is already stored.
            # A save that never opened this facet posts the stored set back
            # verbatim, and re-deciding it would mean re-counting the
            # corpus to reach the same conclusion.
            # `or` short-circuits, so an unchanged set never reaches the
            # count.
            stored = (spec.get("only") or {}).get(dim) or []
            if kept and (
                sorted(kept) == sorted(stored)
                or len(kept) < len(_values_for(visual, dim, [], user))
            ):
                only[dim] = kept
        # What the renderer draws. `roles` name variables by id; the
        # pivot emits its columns under their display labels, and the
        # renderer is given column names. Writing only the roles left
        # every chart built here with its fields chosen and no idea which
        # columns they were, so nothing drew -- and the charts that do
        # draw in production got these keys from the settings page,
        # before the walk existed.
        #
        # A role left empty clears its column, or removing a series would
        # leave the renderer drawing one that is no longer in the rows.
        by_id = {v["id"]: v for v in variables()}
        # Every column any kind can name, not just this one's: changing
        # from a chord to a bar has to leave no `from` behind, or the
        # renderer draws a column the rows no longer carry.
        columns = dict.fromkeys(_EVERY_SLOT, "")
        for role in chart.roles:
            columns[role.id] = by_id.get(roles.get(role.id), {}).get("label", "")
        return {
            "spec": {
                "roles": roles,
                "dimensions": dimensions,
                "measure": measure or "articles",
                "only": only,
            },
            "config": columns,
        }

    if chart is None:
        return {"chart": None, "roles": [], "columns": []}
    if _picks_columns(chart):
        chosen = spec.get("dimensions") or []
        return {
            "chart": chart,
            "roles": [],
            # Every dimension, with what is ticked already ticked. No
            # counts beside them: that is one aggregate over the corpus
            # per row, for a list somebody is reading rather than
            # narrowing, and it is what took this step to 65 seconds.
            "columns": [
                dict(v, on=v["id"] in chosen) for v in variables() if not v["measure"]
            ],
            "measures": [
                dict(v, on=v["id"] == (spec.get("measure") or "articles"))
                for v in variables()
                if v["measure"]
            ],
        }
    picked = spec.get("roles") or {}
    only = spec.get("only") or {}
    slots = []
    for role in chart.roles:
        fits = [v for v in variables() if v["kind"] in role.accepts]
        chosen = picked.get(role.id, "")
        kept = only.get(chosen, [])
        slots.append(
            {
                "id": role.id,
                "label": role.label,
                # "a category or a date", not "text, date". The gallery
                # already says it this way; the panel said it the other.
                "accepts": _plainly(role),
                "optional": not role.needs,
                "options": [dict(v, on=v["id"] == chosen) for v in fits],
                "chosen": chosen,
                "kept": kept,
                # Not fetched here. Listing every value of a dimension with
                # its count is an aggregate over the whole corpus, one per
                # role, and it took this step to 65 seconds -- to fill a
                # disclosure that starts closed. The step draws with what
                # the spec already knows, and the list arrives when
                # somebody opens it.
                #
                # `kept` is what survives without it: those are stored on
                # the visual, so a save that never opened the facet posts
                # them back unchanged.
                "values": [],
            }
        )
    # Named, not lettered: "From and To" is what the slots above are
    # called, where "from and to" reads as a fragment of the code.
    pair_note = ""
    if chart.pairs:
        first, second = (
            next(r.label for r in chart.roles if r.id == p) for p in chart.pairs
        )
        pair_note = (
            f"{first} and {second} must come from the same set of values \u2014 "
            f"a {chart.label.lower()} compares a vocabulary with itself."
        )
    return {
        "chart": chart,
        "roles": slots,
        "columns": [],
        "pairs": chart.pairs,
        "pair_note": pair_note,
    }


# --- step 6: publishing, and the code somebody pastes ------------------------


def publish_panel(visual, post=None, actor=None):
    """Publish or withdraw, and hand over the embed.

    The snippet lived on the advanced-settings page, which is not where
    anybody looks for it: that page is where a visual's plumbing is
    changed, and this is where its work is finished.
    """
    from visuals.embed import snippet
    from visuals.models import Visual
    from visuals.sentence import is_complete

    if post is not None:
        from visuals.services import publish, unpublish

        wanted = post.get("do", "")
        if wanted == "publish":
            # Snapshot what the visual says now, then pin that. A version
            # is a snapshot of the decisions taken up to the moment of
            # publishing, and `publish` on its own pins whatever snapshot
            # already existed -- so the second publish re-pinned the
            # first one and every change made after a visual went live
            # stayed invisible to everybody reading it.
            #
            # Not for an upload, where the rows are the snapshot and
            # there is no source to run.
            from visuals.models import INLINE
            from visuals.services import refresh_snapshot

            if visual.source_kind != INLINE:
                refresh_snapshot(visual, actor)
            publish(visual, actor)
        elif wanted == "unpublish":
            unpublish(visual, actor)
        else:
            raise ValueError("Publish or unpublish")
        # `publish` writes the visual itself, so nothing is returned for
        # the step machinery to write on top of it.
        return {}

    snapshot = visual.snapshots.order_by("-version").first()
    pinned_version = (
        visual.pinned_snapshot.version if visual.pinned_snapshot_id else None
    )
    return {
        "published": visual.status == Visual.PUBLISHED,
        "snippet": snippet(visual),
        # One per choice, so picking a theme is not a round trip. The
        # embed follows the reader unless it is told not to, and inside
        # somebody else's article it usually should be told: a light page
        # on a reader's dark laptop got a dark chart in the middle of it.
        # Which version an embed asks for, and therefore whether it
        # moves. Without `?v=` it serves whatever is published, so
        # publishing again changes the chart in somebody else's article.
        # With one it serves that snapshot for good. Both are wanted --
        # a live dashboard and a chart cited in a piece are different
        # promises -- and only the first was reachable, because nothing
        # offered to write the version into the snippet.
        "pinned_version": pinned_version,
        "snippets": {
            # "auto" means follow the reader, so it is the one variant
            # that has to override the visual's own setting rather than
            # inherit it.
            "auto|latest": snippet(visual, theme=""),
            "light|latest": snippet(visual, theme="light"),
            "dark|latest": snippet(visual, theme="dark"),
            "auto|pinned": snippet(visual, theme="", version=pinned_version),
            "light|pinned": snippet(visual, theme="light", version=pinned_version),
            "dark|pinned": snippet(visual, theme="dark", version=pinned_version),
        },
        "theme_mode": (visual.config or {}).get("theme_mode", "") or "auto",
        "pinned": visual.pinned_snapshot,
        "latest": snapshot,
        # Publishing an empty visual produces an embed that renders
        # nothing, which is worse on somebody's page than not existing.
        # But having no snapshot is not that: it is what everything is
        # until the first publish, and `publish` takes one when there is
        # none. Reading it as empty meant this step could only publish
        # what had been published already, and the visuals that got past
        # it did so through the refresh on the settings page, from before
        # publishing lived here.
        #
        # What is being asked is whether the visual can draw, which is
        # the same question the sentence at the top of the page answers
        # and the preview beside it has already acted on.
        "ready": snapshot is not None
        or (is_complete(visual) and not unmapped_fields(visual)),
    }
