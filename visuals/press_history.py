"""Events the press reported about papers, read by a model and decided by a
person (docs/OUTLET_EVENTS.md, "Events read from the press").

A candidate becomes history only through `accept`, which records an
`outlet_event` through the same `record` as every other event, with the page
as its evidence. An import writes candidates and nothing else.
"""

import calendar
import datetime as dt
import hashlib
import re

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLogEntry
from visuals.models import HistoryEventCandidate, OutletEvent
from visuals.outlet_events import FIELDS, EventError, record

C = HistoryEventCandidate
E = OutletEvent

#: The reading's event types as outlet events. A lease changes who runs a
#: paper, not who owns it, so it is an owner change with the lease in its
#: note. A move has no outlet event; the reviewer says what it is.
KINDS = {
    "ownership_change": E.SOLD,
    "lease": E.OWNER_CHANGED,
    "merger": E.MERGED,
    "founded": E.LAUNCHED,
    "closed": E.CLOSED,
    "renamed": E.RENAMED,
    "moved": "",
}

#: The reading's event types as the queue names them.
LABELS = {
    "ownership_change": "Sale",
    "lease": "Lease",
    "merger": "Merger",
    "founded": "Founding",
    "closed": "Closure",
    "moved": "Move",
    "renamed": "Renaming",
}

#: What the reading flags, for the queue's filter: the start of each flag.
FLAGS = (
    "paper not placed in a town",
    "paper not named in the passage",
    "placed:",
    "title known only in",
    "quote matched through OCR noise",
    "a website, not a paper",
    "is after the issue",
)

_ISSUE = re.compile(r"\d{4}-\d{2}")


def key_of(link, quote):
    """A reading's identity: its first page and its quote, spacing aside."""
    text = f"{link}|{' '.join((quote or '').lower().split())}"
    return hashlib.sha1(
        text.encode()
    ).hexdigest()  # noqa: S324 -- an identity, not a secret


def _row(raw, publication):
    """One CSV row (match_events.py's events.csv) as candidate fields, and
    what is wrong with it."""
    t = {k: str(v or "").strip() for k, v in raw.items()}
    errors = []
    if t.get("event_type") not in KINDS:
        errors.append(f"event_type is one of {', '.join(KINDS)}.")
    if not t.get("link", "").startswith("https://"):
        errors.append("link is the page's https address.")
    if not t.get("quote"):
        errors.append("quote is the words on the page.")
    if not _ISSUE.fullmatch(t.get("first_reported", "")):
        errors.append("first_reported is the issue, YYYY-MM.")
    year = None
    if t.get("year"):
        if not re.fullmatch(r"\d{4}", t["year"]):
            errors.append(f"year is four digits, not {t['year']!r}.")
        else:
            year = int(t["year"])
    fields = {
        "key": key_of(t.get("link"), t.get("quote")),
        "publication": publication,
        "issue": t.get("first_reported", ""),
        "page": t.get("page", ""),
        "link": t.get("link", ""),
        "all_links": t.get("all_links", ""),
        "mentions": int(t["mentions"]) if t.get("mentions", "").isdigit() else 1,
        "last_reported": t.get("last_reported", ""),
        "paper": t.get("paper", ""),
        "town": t.get("town", ""),
        "county": t.get("county", ""),
        "names_in_press": t.get("names_in_press", ""),
        "known_title": t.get("known_title", ""),
        "known_town": t.get("known_town", ""),
        "matched_to": "" if t.get("matched_to") == "none" else t.get("matched_to", ""),
        "match": "" if t.get("match") == "none" else t.get("match", ""),
        "outlet_id": t.get("outlet_id", ""),
        "event_type": t.get("event_type", ""),
        "year": year,
        "year_basis": t.get("year_basis", ""),
        "from_owner": t.get("from", ""),
        "to_owner": t.get("to", ""),
        "quote": t.get("quote", ""),
        "passage": t.get("passage", ""),
        "flags": t.get("flags", ""),
    }
    return fields, errors


def load(rows, publication):
    """Add candidates from rows. Returns (added, skipped, errors).

    A reading already held is skipped, so the same file can be loaded twice.
    A file with any bad row adds nothing and says which lines.
    """
    publication = (publication or "").strip()
    if not publication:
        return 0, 0, ["Name the publication the events were read from."]
    cleaned, errors = [], []
    for n, raw in enumerate(rows, start=2):
        fields, problems = _row(raw, publication)
        errors += [f"line {n}: {p}" for p in problems]
        cleaned.append(fields)
    if errors:
        return 0, 0, errors
    held = set(C.objects.values_list("key", flat=True))
    new, seen = [], set()
    for f in cleaned:
        if f["key"] in held or f["key"] in seen:
            continue
        seen.add(f["key"])
        new.append(C(**f))
    with transaction.atomic():
        C.objects.bulk_create(new, batch_size=500)
    return len(new), len(cleaned) - len(new), []


#: Words a period follows without ending a sentence.
_ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "st",
    "jr",
    "sr",
    "co",
    "inc",
    "corp",
    "bros",
    "mo",
    "no",
    "vol",
    "jan",
    "feb",
    "aug",
    "sept",
    "oct",
    "nov",
    "dec",
    "gen",
    "col",
    "capt",
    "rev",
    "hon",
    "messrs",
}
_END = re.compile(r"[.!?][\"”']?\s+")


def _sentence_starts(text):
    """Where each sentence of `text` starts. A period after an initial or an
    abbreviation ("E. E. Swain", "Mrs.", "Co.") does not end one."""
    starts = [0]
    for m in _END.finditer(text):
        word = re.search(r"([A-Za-z]+)[.!?][\"”']?\s+$", text[: m.end()])
        if word and (
            len(word.group(1)) == 1 or word.group(1).lower() in _ABBREVIATIONS
        ):
            continue
        starts.append(m.end())
    return starts


def _span(starts, at, text):
    """The (start, end) of the sentence holding position `at`."""
    begin = max(s for s in starts if s <= at)
    later = [s for s in starts if s > at]
    return begin, (later[0] if later else len(text))


def excerpt(c):
    """What the note quotes: the quote's own sentence, and -- where the
    year being recorded is not in it -- every sentence back (or on) to the
    nearest one that states that year, so the note says when as well as
    what: "...before moving to Kirksville in 1909. He and Walter Ridgway
    purchased the Kirksville Daily Express that year". A quote not found in
    its passage is quoted as the model gave it."""
    before, said, _ = in_context(c)
    if not said:
        return c.quote
    text = c.passage
    starts = _sentence_starts(text)
    begin, end = _span(starts, len(before), text)
    end = max(end, _span(starts, len(before) + len(said) - 1, text)[1])
    year = str(c.year) if c.year and c.year_basis == "stated" else ""
    if year and year not in text[begin:end]:
        found = [m.start() for m in re.finditer(rf"\b{year}\b", text)]
        if found:
            at = min(found, key=lambda i: abs(i - len(before)))
            y_begin, y_end = _span(starts, at, text)
            begin, end = min(begin, y_begin), max(end, y_end)
    return " ".join(text[begin:end].split())


def in_context(c):
    """The passage split around its quote: (before, quote, after). The
    quote is found word for word, ignoring case and punctuation; a quote
    matched only through OCR noise is not found, and the passage comes back
    whole."""
    passage = c.passage or ""
    words = re.findall(r"[a-z0-9]+", (c.quote or "").lower())
    if not words:
        return passage, "", ""
    # word for word: the model's quote drops OCR's stray punctuation, and
    # joins a word the page broke across a line ("En- terprise")
    pattern = r"[^a-z0-9]+".join(
        r"(?:-\s*)?".join(re.escape(ch) for ch in w) for w in words
    )
    m = re.search(pattern, passage, re.I)
    if m is None:
        return passage, "", ""
    return passage[: m.start()], m.group(0), passage[m.end() :]


def _the(name):
    return name if name.lower().startswith("the ") else f"the {name}"


def statement(values, c):
    """The event in one sentence, from the values the form starts with:
    "E. E. Swain, Sr. and Walter Ridgway bought the Kirksville Daily Express
    in 1909." It heads the note, so an event says what happened before it
    quotes the page."""
    paper = _the(values["outlet_name"] or "paper")
    if values["date_precision"] == E.YEAR:
        when = f" in {values['effective_date'][:4]}"
    else:
        d = dt.date.fromisoformat(values["effective_date"])
        when = f" by {d:%B %Y}"
    seller = f" from {values['from_owner']}" if values["from_owner"] else ""
    event = values["event"]
    if event == E.SOLD and values["to_owner"]:
        said = f"{values['to_owner']} bought {paper}{seller}{when}."
    elif event == E.OWNER_CHANGED and values["to_owner"]:
        said = f"{values['to_owner']} leased {paper}{seller}{when}."
    elif event == E.MERGED:
        into = f" with {values['merged_into']}" if values["merged_into"] else ""
        said = f"{paper[0].upper()}{paper[1:]} merged{into}{when}."
    elif event == E.LAUNCHED:
        by = f" by {values['to_owner']}" if values["to_owner"] else ""
        said = f"{paper[0].upper()}{paper[1:]} was founded{by}{when}."
    elif event == E.CLOSED:
        said = f"{paper[0].upper()}{paper[1:]} closed{when}."
    elif event == E.RENAMED and values["new_name"]:
        said = f"{paper[0].upper()}{paper[1:]} was renamed {values['new_name']}{when}."
    else:
        said = f"{LABELS.get(c.event_type, c.event_type)}: {paper}{when}."
    return said


def proposed(c):
    """The outlet event a candidate proposes, as the accept form's values.

    A year the page states is that year; a year known only from the issue
    that reported it is "on or before" the end of that issue's month.
    """
    if c.year_basis == "stated" and c.year:
        when, precision = dt.date(c.year, 1, 1), E.YEAR
    else:
        y, m = int(c.issue[:4]), int(c.issue[5:7])
        when, precision = dt.date(y, m, calendar.monthrange(y, m)[1]), E.BY
    event = KINDS.get(c.event_type, "")
    name = c.known_title if c.matched_to == "registry" else (c.paper or c.known_title)
    where = ", ".join(x for x in (c.issue, c.page) if x)
    values = {
        "outlet_id": c.outlet_id if c.matched_to == "registry" else "",
        "outlet_name": name,
        "event": event,
        "effective_date": when.isoformat(),
        "date_precision": precision,
        "from_owner": c.from_owner,
        "to_owner": c.to_owner,
        "merged_into": "",
        "new_name": "",
        "new_status": "",
        "evidence_url": c.link,
        "note": "",
        "sets_current": "",
    }
    # The reading puts what a paper became in `to`: for a merger that is
    # what absorbed it, for a renaming its new name -- not an owner.
    if event == E.MERGED:
        values["merged_into"], values["to_owner"] = c.to_owner, ""
    elif event == E.RENAMED:
        values["new_name"], values["to_owner"] = c.to_owner, ""
    values["note"] = f'{statement(values, c)} {c.publication}, {where}: "{excerpt(c)}"'
    return values


def _decide(actor, c, status, reason="", event=None):
    before = {"status": c.status}
    c.status = status
    c.reason = reason
    c.outlet_event = event
    c.reviewed_by = actor
    c.reviewed_at = timezone.now()
    c.save(
        update_fields=["status", "reason", "outlet_event", "reviewed_by", "reviewed_at"]
    )
    AuditLogEntry.objects.create(
        actor=actor,
        action=f"history_candidate:{'accept' if status == C.ACCEPTED else 'reject'}",
        target_table=C._meta.db_table,
        target_ids=[str(c.pk)],
        before=before,
        after={"status": status, "reason": reason, "outlet_event": event and event.pk},
        reason=reason or f"accepted as outlet event {event.pk}",
    )


def _pending(pk):
    """The candidate, locked, if it is still waiting; else EventError."""
    c = C.objects.select_for_update().filter(pk=pk).first()
    if c is None:
        raise EventError(["No such candidate."])
    if c.status != C.PENDING:
        raise EventError([f"This candidate was already {c.status}."])
    return c


def accept(actor, pk, data):
    """Record the candidate's event with the reviewer's values, and mark it
    accepted. The page stays the evidence and a candidate never sets how an
    outlet stands now: it is history the registry already reflects or never
    held. Raises EventError, and records nothing, if the event is not one.
    """
    with transaction.atomic():
        c = _pending(pk)
        values = proposed(c)
        values.update({k: data[k] for k in FIELDS if k in data})
        values["evidence_url"] = c.link
        values["sets_current"] = ""
        if not values.get("event"):
            raise EventError(["Say what happened: a move has no event of its own."])
        event = record(actor, values, origin=f"{c.publication} review")
        _decide(actor, c, C.ACCEPTED, event=event)
    return event


def reject(actor, pk, reason):
    """Mark the candidate rejected, with why. Nothing else is written."""
    reason = (reason or "").strip()
    if not reason:
        raise EventError(["Say why it is rejected."])
    with transaction.atomic():
        c = _pending(pk)
        _decide(actor, c, C.REJECTED, reason=reason)
    return c
