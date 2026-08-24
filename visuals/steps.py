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
    #: Config or spec keys this step owns. Listed so that a step can be
    #: re-entered and its own choices cleared without touching another's.
    owns: tuple


STEPS = (
    Step(
        "type",
        "Chart",
        "What kind of chart?",
        "",  # the panel says what it is checking against, or that it cannot
        ("kind",),
    ),
    Step(
        "theme",
        "Colour",
        "Which colours?",
        "Enough distinct steps for the categories in play.",
        ("theme", "taxonomy"),
    ),
    Step(
        "data",
        "Data",
        "Which articles?",
        "The slice of the corpus. Which fields to draw comes later.",
        ("dataset", "datasets", "from", "to"),
    ),
    Step(
        "newsrooms",
        "Newsrooms",
        "Which newsrooms?",
        "All of them unless you narrow it. Spans every dataset chosen.",
        ("publishers",),
    ),
    Step(
        "fields",
        "Fields",
        "Which fields?",
        "",  # the panel says it, and says it about this chart
        ("dimensions", "measure", "roles", "only"),
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
