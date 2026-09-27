"""The outlet stories list: news about a sale, a closure, a new owner.

Kept by hand -- one form per story, on the Outlets pages -- and drawn by any
visual whose source is the list. Saving a story refreshes every published
visual that reads it, so the table a reader sees is the list as it stands.
"""

import csv
import datetime as dt
import io
import re

from django.db import transaction

from audit.models import AuditLogEntry
from visuals.models import STORIES, OutletStory, Visual

#: The relevant paragraph, not the story. A reader follows the headline for
#: the rest; "One leader, four Leaders" went in whole at 11,113 characters
#: and was a page of text in one cell.
TEXT_LIMIT = 800

FIELDS = (
    "published",
    "source",
    "headline",
    "url",
    "type",
    "publications",
    "owners",
    "text",
)

#: Offered on the form; any other source can be typed.
SOURCES = ("Missouri Press Association", "Local News Initiative")


class StoryError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def _names(value):
    """A list typed any reasonable way -- "; ", ", " between capitals is
    too risky ("Kenny and Valerie Schulz, LLC"), so only ";" and new lines
    separate -- stored the way the table reads it."""
    parts = re.split(r"[;\n]+", value or "")
    return "; ".join(p.strip() for p in parts if p.strip())


def clean(data):
    """The fields of one story, typed and trimmed, and what is wrong."""
    text = {k: str(data.get(k) or "").strip() for k in FIELDS}
    errors = []
    when = None
    try:
        when = dt.date.fromisoformat(text["published"])
    except ValueError:
        errors.append("The date is required, as YYYY-MM-DD.")
    if when and when > dt.date.today():
        errors.append("The story's date cannot be in the future.")
    if not text["headline"]:
        errors.append("The headline is required.")
    if not re.match(r"^https?://\S+$", text["url"]):
        errors.append("The link is required, starting https://.")
    if not text["source"]:
        errors.append("Say who published it.")
    if text["type"] not in dict(OutletStory.TYPES):
        errors.append("Pick a type.")
    if len(text["text"]) > TEXT_LIMIT:
        errors.append(
            f"Keep the text to the paragraph that matters: {TEXT_LIMIT} characters "
            f"or fewer ({len(text['text'])} now)."
        )
    fields = {
        **text,
        "published": when,
        "publications": _names(text["publications"]),
        "owners": _names(text["owners"]),
    }
    return fields, errors


def save(actor, data, story=None):
    """Add a story, or change one. Raises StoryError."""
    fields, errors = clean(data)
    if errors:
        raise StoryError(errors)
    before = None
    with transaction.atomic():
        if story is None:
            story = OutletStory.objects.create(**fields, added_by=actor)
            action = "outlet_story:add"
        else:
            before = {k: str(getattr(story, k)) for k in FIELDS}
            for k, v in fields.items():
                setattr(story, k, v)
            story.save()
            action = "outlet_story:edit"
        AuditLogEntry.objects.create(
            actor=actor,
            action=action,
            target_table=OutletStory._meta.db_table,
            target_ids=[str(story.pk)],
            before=before,
            after={k: str(v) for k, v in fields.items()},
            reason=story.headline[:200],
        )
    refresh_published(actor)
    return story


def delete(actor, story):
    with transaction.atomic():
        AuditLogEntry.objects.create(
            actor=actor,
            action="outlet_story:delete",
            target_table=OutletStory._meta.db_table,
            target_ids=[str(story.pk)],
            before={k: str(getattr(story, k)) for k in FIELDS},
            after=None,
            reason=story.headline[:200],
        )
        story.delete()
    refresh_published(actor)


def rows():
    """The list as a visual draws it: the headline linked, lists as lists."""
    return [
        {
            "Date": s.published.isoformat(),
            # "[" and "]" in a headline would end the link early.
            "Headline": "[{}]({})".format(
                s.headline.replace("[", "(").replace("]", ")"), s.url
            ),
            "Source": s.source,
            "Type": s.type,
            "Publications": s.publications or None,
            "Owners": s.owners or None,
            "Paragraph": s.text or None,
        }
        for s in OutletStory.objects.order_by("-published", "headline")
    ]


def refresh_published(actor):
    """Every published visual of the list, re-captured and re-pinned.

    The list is kept on purpose, a story at a time, and a table that went on
    showing last month's list after a story was added would be the list
    nobody can see. A draft is left alone: it refreshes when it is opened.
    """
    from visuals.services import publish, refresh_snapshot

    for visual in Visual.objects.filter(source_kind=STORIES, status=Visual.PUBLISHED):
        refresh_snapshot(visual, actor)
        publish(visual, actor)


_LINK = re.compile(r"^\[(.+)\]\((https?://\S+)\)$")


def load(actor, rows_in, source="Missouri Press Association"):
    """Add stories from CSV rows in the table's own shape (Date, Headline as
    a Markdown link, Type, Publications, Owners, Paragraph). A story whose
    link is already listed is skipped. Returns (added, skipped, errors)."""
    have = set(OutletStory.objects.values_list("url", flat=True))
    cleaned, errors = [], []
    for n, r in enumerate(rows_in, start=2):
        m = _LINK.match((r.get("Headline") or "").strip())
        data = {
            "published": r.get("Date"),
            "source": r.get("Source") or source,
            "headline": m.group(1) if m else r.get("Headline"),
            "url": m.group(2) if m else r.get("URL") or r.get("url"),
            "type": r.get("Type") or OutletStory.OWNERSHIP,
            "publications": r.get("Publications"),
            "owners": r.get("Owners"),
            "text": r.get("Paragraph"),
        }
        fields, problems = clean(data)
        if problems:
            errors.append(f"line {n}: {'; '.join(problems)}")
        cleaned.append(fields)
    if errors:
        return 0, 0, errors
    added = skipped = 0
    with transaction.atomic():
        for fields in cleaned:
            if fields["url"] in have:
                skipped += 1
                continue
            OutletStory.objects.create(**fields, added_by=actor)
            have.add(fields["url"])
            added += 1
        if added:
            AuditLogEntry.objects.create(
                actor=actor,
                action="outlet_story:load",
                target_table=OutletStory._meta.db_table,
                target_ids=[],
                after={"added": added, "skipped": skipped},
                reason="stories loaded from a file",
            )
    if added:
        refresh_published(actor)
    return added, skipped, []


def read_csv(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(io.StringIO(fh.read())))
