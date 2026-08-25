"""The builder's five steps, and what each one is for.

The prototype (ROADMAP item 20) settled the order: a chart type, a colour
theme, the slice of the corpus, the newsrooms, then the fields. Every step
writes its own keys and none clears another's, so going back changes one
choice and keeps the rest -- which is the difference between a tool people
explore with and a form they fill in once.

The panel a step renders is a template; what it decides is a small set of
keys on `Visual.config` or `Visual.spec`. Nothing here holds state of its
own, so a half-finished visual is just a visual with some keys unset and
survives a closed tab without a draft table.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    slug: str
    label: str
    #: The question it answers, in the words the page uses.
    heading: str
    note: str
    #: Keys this step owns, each named by the store it lives in --
    #: "config:kind", "spec:datasets". Listed so that a step can be
    #: re-entered and its own choices cleared without touching another's.
    #:
    #: Qualified, because the two stores have keys of the same name that
    #: mean different things: `spec:from` is the first date and
    #: `config:from` is the column a chord draws its left-hand side from.
    #: Read as one namespace they collide, and one of them would have to
    #: be renamed to a worse name to keep the other.
    owns: tuple


STEPS = (
    Step(
        "type",
        "Chart",
        "What kind of chart?",
        "",  # the panel says what it is checking against, or that it cannot
        ("config:kind",),
    ),
    Step(
        "theme",
        "Look",
        "How should it read?",
        "A title, and enough distinct colours for the categories in play.",
        (
            "config:title",
            "config:subtitle",
            "config:theme",
            "config:theme_mode",
            "config:taxonomy",
        ),
    ),
    Step(
        "data",
        "Data",
        "Which articles?",
        "The slice of the corpus. Which fields to draw comes later.",
        ("spec:dataset", "spec:datasets", "spec:from", "spec:to"),
    ),
    Step(
        "newsrooms",
        "Newsrooms",
        "Which newsrooms?",
        "All of them unless you narrow it. Spans every dataset chosen.",
        # The newsroom choice frames the map too: choosing whose coverage
        # this is answers where it is about, and asking again in another
        # step was a second way to say the same thing that could disagree
        # with the first.
        (
            "spec:publishers",
            "spec:publisher_county",
            "spec:publisher_city",
            "config:focus",
            "config:focus_name",
            "config:focus_level",
            "config:extent",
            "config:frame",
        ),
    ),
    Step(
        "fields",
        "Fields",
        "Which fields?",
        "",  # the panel says it, and says it about this chart
        # The spec's `roles` name variables; the renderer draws columns,
        # and the pivot's columns are named by their display labels. Both
        # are written here, from the same choice, or the chart has fields
        # chosen and no idea which columns they are.
        (
            "spec:dimensions",
            "spec:measure",
            "spec:roles",
            "spec:only",
            "config:x",
            "config:y",
            "config:series",
            "config:size",
            "config:label",
            "config:from",
            "config:to",
            "config:value",
            "config:geo_join",
            "config:geo_value",
            "config:place",
            "config:lat",
            "config:lon",
        ),
    ),
    Step(
        "publish",
        "Publish",
        "Ready to publish?",
        "Publishing pins the current data. The embed serves that until "
        "you publish again.",
        ("visual:status",),
    ),
)

BY_SLUG = {s.slug: s for s in STEPS}


def reached(visual):
    """How far this visual has got, as a set of step slugs.

    A step counts as done when it has decided something, not when somebody
    has looked at it: opening the colour panel and choosing nothing leaves
    the default, which is a decision nobody made.
    """
    config, spec = visual.config or {}, visual.spec or {}
    done = set()
    if config.get("kind"):
        done.add("type")
    if config.get("theme"):
        done.add("theme")
    if spec.get("dataset") or spec.get("datasets") or spec.get("from"):
        done.add("data")
    if spec.get("publishers"):
        done.add("newsrooms")
    if spec.get("dimensions"):
        done.add("fields")
    return done


def next_after(slug):
    """The step to land on after finishing this one."""
    order = [s.slug for s in STEPS]
    i = order.index(slug)
    return order[i + 1] if i + 1 < len(order) else None
