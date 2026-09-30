"""The builder's heading, assembled from what has been chosen.

"A chord diagram of CIN primary x CIN alternate, from March in Missouri."
The page is the sentence somebody arrived with (ROADMAP item 20), so it is
built from the same keys the panels write and cannot drift from them.

Parts nobody has chosen are gaps rather than guesses. A gap for the step
being worked on is marked, so the sentence says where you are as well as
what you have.
"""

from visuals.types import BY_ID


def _named(visual, spec, chart):
    """The variables filling this chart's slots, as labels.

    Measures are left out. Somebody says "primary against alternate" and
    takes the count as read; printing "x Articles" is the schema talking.
    """
    from visuals.panels import variables

    by_id = {v["id"]: v for v in variables(visual)}
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


def _measured(visual, spec, chart):
    """The measures filling this chart's required slots, as labels, but
    only once every one of them is filled.

    The counterpart to `_named`, for the chart whose subject *is* its
    numbers. Partial is nothing: naming one axis of a scatter says the
    fields are chosen when half of them are.
    """
    from visuals.panels import variables

    by_id = {v["id"]: v for v in variables(visual)}
    picked = spec.get("roles") or {}
    out = []
    for role in chart.roles:
        if not role.needs:
            continue
        chosen = picked.get(role.id)
        if not chosen:
            return []
        out.append(by_id.get(chosen, {}).get("label", chosen))
    return out


def _dataset_label(slug):
    """A dataset's name, not its slug. The spec stores "mizzou" and the
    sentence should say "Missouri" -- the slug is a key, and reading one
    back to somebody is the schema talking."""
    from django.db import DatabaseError

    from explorer.models import Dataset

    try:
        found = Dataset.objects.filter(slug=slug).values_list("label", flat=True)
        return found[0] if found else slug
    except (DatabaseError, IndexError):
        return slug


def parts_for(visual, step=""):
    """[(lead, text, kind)] where kind is 'said', 'gap' or 'here'.

    Each part carries the word that joins it to the one before -- "of",
    "from", "in" -- because the template cannot know which is which by
    counting. Left to a loop counter it read "A chart any date every
    dataset", which is not a sentence.
    """
    config, spec = visual.config or {}, visual.spec or {}
    chart = BY_ID.get(config.get("kind", ""))
    out = []

    def gap(lead, text, mine):
        return (lead, text, "here" if step == mine else "gap")

    out.append(("", chart.label.lower(), "said") if chart else gap("", "chart", "type"))

    # AN OUTLET MAP HAS NO DATES AND NO DATASETS: it draws the registry,
    # whole. Asked about them it could never finish its sentence, so it
    # could never draw -- "A outlet map from any date in every dataset".
    # What it has is which kinds, and where.
    if chart and chart.id == "outletmap":
        kinds = [
            k.strip() for k in str(config.get("categories_drawn") or "").split(",")
        ]
        kinds = [k for k in kinds if k]
        out.append(
            ("of", f"{', '.join(kinds)} outlets", "said")
            if kinds
            else ("of", "every outlet in the registry", "said")
        )
        place = config.get("focus_name") or ""
        out.append(("in", place, "said") if place else gap("in", "a place", "theme"))
        if config.get("as_of"):
            import contextlib

            from visuals.outlets import as_of_period

            with contextlib.suppress(ValueError, TypeError):
                out.append(("as of", as_of_period(config["as_of"])[2], "said"))
        return out

    # A LAYERED MAP IS NAMED BY ITS LAYERS, then by the slice like a story
    # map: it shades by them, over stories from somewhere, some dates.
    if chart and chart.id == "layermap":
        from visuals.layermap import layers_of

        n = len(layers_of(config))
        out.append(
            ("shading by", f"{n} Census layer{'s' if n != 1 else ''}", "said")
            if n
            else gap("shading by", "some Census layers", "layers")
        )

    if chart and chart.roles:
        named = _named(visual, spec, chart)
        if not named:
            # Every required slot filled, and every one of them a number:
            # a scatter plots two measures against each other, so the
            # measures are its subject rather than a count taken as read.
            # Reading "filled" off the pretty text meant a scatter could
            # never finish its sentence and so could never be published,
            # whatever anybody chose.
            named = _measured(visual, spec, chart)
            out.append(
                ("of", " against ".join(named), "said")
                if named
                else gap("of", "some fields", "fields")
            )
        else:
            out.append(("of", " × ".join(named), "said"))

    when = ""
    if spec.get("from") and spec.get("to"):
        when = f"{spec['from']} to {spec['to']}"
    elif spec.get("from"):
        when = f"since {spec['from']}"
    out.append(("from", when, "said") if when else gap("from", "any date", "data"))

    picked = spec.get("datasets") or ([spec["dataset"]] if spec.get("dataset") else [])
    if len(picked) == 1:
        out.append(("in", _dataset_label(picked[0]), "said"))
    elif picked:
        out.append(("across", f"{len(picked)} datasets", "said"))
    else:
        out.append(gap("in", "every dataset", "data"))

    rooms = spec.get("publishers") or []
    if rooms:
        out.append(("from", f"{len(rooms)} newsrooms", "said"))

    about = spec.get("about_counties") or []
    if about:
        from datasets.geo import county_label

        names = [county_label(c).rsplit(",", 1)[0] for c in about]
        where = (
            f"{names[0]} County"
            if len(names) == 1
            else (
                f"{len(names)} counties"
                if len(names) > 3
                else f"{', '.join(names[:-1])} and {names[-1]} counties"
            )
        )
        verb = "set in" if spec.get("about_match") == "central" else "about"
        out.append((verb, where, "said"))

    # Which subset, always. A chart of everything and a chart of the
    # exported set look identical and mean different things.
    from visuals.corpus import COMPLETE, SUBSETS

    subset = spec.get("subset") or COMPLETE
    out.append(("", SUBSETS[subset][0].lower(), "said"))
    return out


def article(parts):
    """ "A" or "An", for the word the sentence starts with: "An outlet map",
    not "A outlet map"."""
    first = next((text for _, text, _ in parts if text), "")
    return "An" if first[:1].lower() in "aeiou" and first else "A"


def is_complete(visual):
    """Whether the sentence has no gaps left -- which is when the preview
    can draw rather than wait."""
    return all(kind == "said" for _, _, kind in parts_for(visual))
