"""One review queue, in three parts, used by every queue in the console.

There were two queues here and they agreed on nothing. Both showed a
person something the pipeline was unsure of, both took a verb per row,
both submitted a session at once, both wrote a receipt -- and they did it
with two records, two submit paths, two receipt shapes and two templates,
about 750 lines of parallel code that had converged only where somebody
had recently touched both.

A third queue was coming (the source directory), and a fourth (analysis).
Each would have added its own.

The three parts are:

    Verb      what a person can say about a row, as data rather than as
              markup: a name, the words on the button, whether it takes a
              typed value, and what it writes.

    Queue     what a queue is: which subject it decides about, which verbs
              it offers, and how a verb is carried out.

    submit    one path from a posted session to a receipt, for all of them.

A new queue is four declarations -- its subject, its rows, its facts and
its verbs -- and no new machinery.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Which rows are in a queue, and what to show about each one. Those are the
parts that are genuinely different between an article whose classification
is doubted and a publisher record with a contradicted field, and pretending
otherwise would produce a configuration language instead of a queue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field


@dataclass(frozen=True)
class Verb:
    """One thing a person can say about a row.

    The button used to be written into each template, which is why the
    two queues' buttons drifted: `Accept/Update it` on one and
    `Accept/Leave it out` on the other, with the difference in the HTML
    rather than anywhere a reader would look for it.

    name       what is recorded, and what the hidden field carries.
    label      the strong line on the button.
    sublabel   the quiet line under it, which says what will happen.
    past       the receipt's word for it: "3 accepted".
    takes_value  whether the verb writes something typed. A verb that
               takes a value and has none is not a decision yet, and the
               dock says so rather than submitting it as nothing.
    tone       accept | reject | fix. Styling only, and named for what the
               verb means rather than for a colour.
    """

    name: str
    label: str
    sublabel: str = ""
    past: str = ""
    takes_value: bool = False
    tone: str = "accept"

    def __post_init__(self):
        if not self.name:
            raise ValueError("a verb needs a name; it is what gets recorded")
        if not self.past:
            object.__setattr__(self, "past", self.name + "ed")


@dataclass(frozen=True)
class Queue:
    """One queue: a subject, a set of verbs, and how a verb is carried out.

    key           identifies the queue in a decision record and a URL.
    subject_type  what is being decided about -- "article", "source". Half
                  of the decision's identity; the other half is the
                  subject's own id.
    verbs         every verb this queue can offer. Which of them a
                  PARTICULAR row can carry out is a property of the row,
                  not of the queue: reject needs a body to hand back,
                  re-extract needs the archived capture. `verbs_for`
                  answers that per row.
    apply         carry out one decision. Returns a dict describing what
                  was written, which goes into the decision record.
    verbs_for     which verbs a given row can carry out. Defaults to all
                  of them.
    """

    key: str
    subject_type: str
    verbs: tuple[Verb, ...]
    apply: Callable
    verbs_for: Callable | None = None
    #: What a verb name means, resolved once.
    _by_name: dict = dataclass_field(default_factory=dict, repr=False)

    def __post_init__(self):
        if not self.verbs:
            raise ValueError(f"queue {self.key!r} offers no verbs")
        by_name = {}
        for verb in self.verbs:
            if verb.name in by_name:
                raise ValueError(f"queue {self.key!r} declares {verb.name!r} twice")
            by_name[verb.name] = verb
        object.__setattr__(self, "_by_name", by_name)

    def verb(self, name: str) -> Verb | None:
        """The verb by that name, or None. A name this queue does not
        offer is refused rather than guessed at: a posted verb is user
        input, and one that reached `apply` would report success and do
        nothing."""
        return self._by_name.get(name)

    def offered(self, subject) -> tuple[Verb, ...]:
        """The verbs this row can carry out.

        A button that cannot act is worse than no button: it is a promise
        the submit path then refuses, and the person is not told.
        """
        if self.verbs_for is None:
            return self.verbs
        allowed = set(self.verbs_for(subject))
        return tuple(verb for verb in self.verbs if verb.name in allowed)


#: Every queue in the console, by key. Registered rather than imported
#: from a list here, so a queue lives with the code that knows its rows.
_QUEUES: dict[str, Queue] = {}


def register(queue: Queue) -> Queue:
    """Add a queue. Returns it, so a module can register at definition."""
    if queue.key in _QUEUES and _QUEUES[queue.key] is not queue:
        raise ValueError(f"a different queue is already registered as {queue.key!r}")
    _QUEUES[queue.key] = queue
    return queue


def get(key: str) -> Queue:
    """The queue by that key.

    Raises rather than returning None: every caller here has a key from
    its own URL configuration, so a missing one is a wiring mistake and
    not something to degrade around.
    """
    try:
        return _QUEUES[key]
    except KeyError:
        known = ", ".join(sorted(_QUEUES)) or "none registered"
        raise LookupError(f"no queue {key!r}; known queues: {known}") from None


def registered() -> dict[str, Queue]:
    """Every registered queue. For tests and for the To Do list."""
    return dict(_QUEUES)
