"""The builder's heading, assembled from what has been chosen.

"A chord diagram of CIN primary x CIN alternate, from March in Missouri."
The page is the sentence somebody arrived with (ROADMAP item 20), so it is
built from the same keys the panels write and cannot drift from them.

Parts nobody has chosen are gaps rather than guesses. A gap for the step
being worked on is marked, so the sentence says where you are as well as
what you have.
"""

from visuals.types import BY_ID


def _named(spec, chart):
    """The variables filling this chart's slots, as labels.

    Measures are left out. Somebody says "primary against alternate" and
    takes the count as read; printing "x Articles" is the schema talking.
    """
    from visuals.panels import variables

    by_id = {v["id"]: v for v in variables()}
    picked = spec.get("roles") or {}
    out = []
    for role in chart.roles:
        if not role.needs:
            continue
        chosen = picked.get(role.id)
        if not chosen or by_id.get(chosen, {}).get("measure"):
            continue
        out.append(by_id[chosen]["label"])
    return out


def parts_for(visual, step=""):
    """[(text, kind)] where kind is 'said', 'gap' or 'here'."""
    config, spec = visual.config or {}, visual.spec or {}
    chart = BY_ID.get(config.get("kind", ""))
    out = []

    def gap(text, mine):
        return (text, "here" if step == mine else "gap")

    out.append((chart.label.lower(), "said") if chart else gap("chart", "type"))

    if chart and chart.roles:
        named = _named(spec, chart)
        out.append(
            (" × ".join(named), "said") if named else gap("some fields", "fields")
        )

    when = ""
    if spec.get("from") and spec.get("to"):
        when = f"{spec['from']} to {spec['to']}"
    elif spec.get("from"):
        when = f"since {spec['from']}"
    out.append((when, "said") if when else gap("any date", "data"))

    picked = spec.get("datasets") or ([spec["dataset"]] if spec.get("dataset") else [])
    if len(picked) == 1:
        out.append((picked[0].replace("-", " "), "said"))
    elif picked:
        out.append((f"{len(picked)} datasets", "said"))
    else:
        out.append(gap("every dataset", "data"))

    rooms = spec.get("publishers") or []
    if rooms:
        out.append((f"{len(rooms)} newsrooms", "said"))
    return out


def is_complete(visual):
    """Whether the sentence has no gaps left -- which is when the preview
    can draw rather than wait."""
    return all(kind == "said" for _, kind in parts_for(visual))
