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
    }


# --- step 3: the slice -------------------------------------------------------


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
        return {
            "spec": {
                "datasets": picked,
                "from": post.get("from", "").strip(),
                "to": post.get("to", "").strip(),
            }
        }
    spec = visual.spec or {}
    picked = spec.get("datasets") or ([spec["dataset"]] if spec.get("dataset") else [])
    return {
        "datasets": [
            {"slug": d["slug"], "label": d["label"], "on": d["slug"] in picked}
            for d in choices
        ],
        "chosen_count": len(picked),
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


def newsrooms_panel(visual, post=None, tree=None):
    """State, then county, then newsroom.

    An empty list means every one of them. Storing exclusions instead would
    make "all" a list that goes stale the moment a publisher is added.
    """
    if post is not None:
        return {"spec": {"publishers": [p for p in post.getlist("publishers") if p]}}
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
                    "rooms": [
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "count": r["count"],
                            "on": not kept or r["id"] in kept,
                        }
                        for r in rooms
                    ],
                    "count": sum(r["count"] for r in rooms),
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
    return {"states": states, "kept": len(kept) or total, "total": total}


# --- step 5: the fields ------------------------------------------------------
#
# This is where the pivot is decided, which the first prototype assumed had
# already happened. A role offers the variables whose type fits it, and once
# one is chosen its values can be narrowed.


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
            if kept and len(kept) < len(_values_for(visual, dim, [], user)):
                only[dim] = kept
        return {
            "spec": {
                "roles": roles,
                "dimensions": dimensions,
                "measure": measure or "articles",
                "only": only,
            }
        }

    if chart is None:
        return {"chart": None, "roles": []}
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
                # The values on offer, counted, and only for a dimension --
                # a measure has no values to narrow, it has a range.
                "values": _values_for(visual, chosen, kept, user),
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
        "pairs": chart.pairs,
        "pair_note": pair_note,
    }
