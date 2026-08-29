"""Proposed changes waiting on a human (SCOPE.md §2.2).

A proposal is one field on one record that something — an imported
spreadsheet, a normalization pass — thinks should change, together with
what the checks found and what it suggests. Proposals are grouped by
record for review, because a publisher whose name, city, county and
owner are all in question is one decision, not four unrelated rows.

Nothing here writes to the corpus. Accepting a proposal runs the
ordinary audited write; the proposal only records that a person decided.
"""

from django.conf import settings
from django.db import models


class ChangeProposal(models.Model):
    # A queue item is a record with a named defect (REVIEW.md). The flag
    # says what is wrong; the vocabulary lives in review/flags.py.
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FIXED = "fixed"
    STATES = [(s, s) for s in (PENDING, ACCEPTED, REJECTED, FIXED)]

    source_batch = models.ForeignKey(
        "review.ImportBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proposals",
    )
    origin = models.CharField(max_length=120, default="")

    target = models.CharField(max_length=20, default="sources")
    # Empty when the proposal is that a publisher exists which the corpus
    # has never heard of. There is no record to name yet -- accepting the
    # proposal is what makes one.
    record_id = models.TextField(blank=True, default="")
    # One submitted form, so the fields of a publisher that does not exist
    # yet group into a single decision. Grouping those by record_id would
    # collapse every pending new publisher into one, since they all share
    # the empty string.
    submission = models.UUIDField(null=True, blank=True, db_index=True)
    record_label = models.TextField(blank=True, default="")
    dataset = models.CharField(max_length=100, blank=True, default="")

    field = models.CharField(max_length=60)
    current_value = models.TextField(blank=True, default="")
    proposed_value = models.TextField(blank=True, default="")

    flag = models.CharField(max_length=40, default="")
    # What makes this record's instance of the defect specific.
    detail = models.TextField(blank=True, default="")
    suggestion = models.TextField(blank=True, default="")

    # A machine-generated proposal has no proposer; a reported one must.
    # An edit offered to somebody else's dataset is only worth as much as
    # knowing who offered it, so the person is named rather than folded into
    # `origin`, which is free text a command sets.
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Where the fact came from: a URL, a filing, a phone call. Required of a
    # person, absent from a scan -- a reviewer deciding on somebody else's
    # word needs to see the word.
    citation = models.TextField(blank=True, default="")

    state = models.CharField(max_length=20, choices=STATES, default=PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    #: What was decided, in the words the buttons use. `STATES` pairs each
    #: value with itself, so `get_state_display` gives back "rejected" --
    #: which is not what the reviewer pressed. They pressed Keep.
    _STATE_LABELS = {
        ACCEPTED: "Accepted",
        REJECTED: "Kept",
        FIXED: "Fixed",
        PENDING: "Pending",
    }

    @property
    def state_label(self):
        return self._STATE_LABELS.get(self.state, self.state)

    # What the check believes the value should be, when it knows: the
    # gazetteer's spelling, the corpus's spelling of an owner. Offered as
    # its own option so the reviewer does not have to retype it.
    suggested_value = models.TextField(blank=True, default="")
    # For a fix: the value the reviewer supplied instead.
    final_value = models.TextField(blank=True, default="")
    note = models.TextField(blank=True, default="")
    audit_entry = models.ForeignKey(
        "audit.AuditLogEntry",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["record_label", "field"]
        indexes = [
            models.Index(fields=["state", "target"]),
            models.Index(fields=["record_id"]),
        ]

    def __str__(self):
        return f"{self.record_label}.{self.field}: {self.proposed_value!r}"

    @property
    def creates_a_record(self):
        """No record to change: accepting this makes one."""
        return not self.record_id

    @property
    def group_key(self):
        """What the queue groups by. A record if there is one, otherwise the
        submission that proposed it."""
        return self.record_id or f"new:{self.submission}"

    @property
    def field_label(self):
        """What to call the field on screen.

        A dotted field is a key inside a JSON column and the path is a
        storage detail: a reviewer deciding about a state should read
        "state", not "meta.state".
        """
        return self.field.rpartition(".")[2] or self.field

    @property
    def vocabulary_words(self):
        """The values a fix to this field may take, or [] for free text.

        A field with a controlled vocabulary has a right answer and a
        list of them, so a reviewer picks one rather than typing --
        typing is how `digital_native` gets written beside `digital
        native` in the first place, and a queue whose fix box invites it
        is a queue that creates the defect it exists to clear.

        The kinds, not every word that means one: a fix writes what the
        record should say, and `tv` is a word records say rather than
        the value they should hold.
        """
        from datasets.schema import BY_KEY

        field = BY_KEY.get(self.field)
        if not field or not field.vocabulary:
            return []
        from datasets.terms import terms

        return sorted(
            {
                spelling
                for spelling, _label in terms(field.vocabulary).values()
                if spelling
            }
        )

    @property
    def flag_label(self):
        from review.flags import ALL_BY_KEY

        found = ALL_BY_KEY.get(self.flag)
        return found.label if found else self.flag

    @property
    def flag_defect(self):
        from review.flags import ALL_BY_KEY

        found = ALL_BY_KEY.get(self.flag)
        return found.defect if found else ""

    @property
    def useful_suggestion(self):
        """The check's own value, when it is genuinely a third option.

        A suggestion equal to what is recorded is what Keep already
        does, and one equal to the proposal is what Accept already does.
        Offering either as a separate button puts the same value on
        screen twice and asks the reviewer to tell them apart.
        """
        value = (self.suggested_value or "").strip()
        if not value:
            return ""
        if value == (self.current_value or "").strip():
            return ""
        if value == (self.proposed_value or "").strip():
            return ""
        return value

    @property
    def actionable(self):
        """Every flagged field is decidable.

        There was a category here for "a file contradicts itself", held
        back from the queue on the grounds that no value was safe to
        accept. That confused two things: not knowing which value to
        propose, which is ordinary and handled by leaving the proposed
        column empty, and the reviewer not being able to answer, which
        was never true — Keep and Fix always apply, and the person
        reading the record usually knows exactly which value is meant.
        """
        return True


class DatasetScan(models.Model):
    """What one directory's records looked like the last time it scanned.

    A daily scan that redoes the work every day is a daily scan nobody
    leaves switched on. This is what lets it skip: the stamp is a hash of
    everything a scan reads, so a directory whose records have not moved
    since the last run has nothing new to say about them.

    There is no timestamp on a publisher record to compare instead --
    `Source` carries no `updated_at` -- so the stamp is over the content
    itself, which is the honest version of the question anyway. A record
    edited and edited back has not changed.

    The flag vocabulary is in the stamp too. A scan is the checks applied
    to the records, so a new check is a reason to look again at records
    that have not moved.
    """

    dataset = models.CharField(max_length=100, unique=True)
    stamp = models.CharField(max_length=64)
    scanned_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "review_dataset_scan"

    def __str__(self):
        return f"{self.dataset} {self.stamp[:8]} {self.scanned_at:%Y-%m-%d %H:%M}"


def sources_stamp(dataset_slug):
    """A hash of every record a scan of this dataset would read.

    Ordered by id and serialised the same way every time, or the hash
    would change with the order rows came back in and a scan would run
    every day whatever happened.
    """
    import hashlib
    import json

    from datasets.models import VocabularyTerm
    from explorer.models import DatasetSource, Source
    from review.flags import FLAGS

    ids = DatasetSource.objects.filter(dataset__slug=dataset_slug).values_list(
        "source_id", flat=True
    )
    rows = (
        Source.objects.filter(id__in=ids)
        .order_by("id")
        .values_list("id", "canonical_name", "city", "county", "owner", "type", "meta")
    )
    digest = hashlib.sha256()
    # The checks first, so adding one changes every dataset's stamp.
    digest.update(json.dumps([f.key for f in FLAGS]).encode())
    # And the words those checks read, for the same reason and one this
    # missed: a check that asks whether a value is in the vocabulary
    # changes its mind when the vocabulary changes, and nothing about the
    # records does.
    #
    # Without this, adding a word on the schema page left every dataset's
    # stamp identical, so the nightly scan skipped them all and the edit
    # reached the queue only when something else happened to move a
    # record. Retiring a word and changing a kind's spelling were
    # invisible the same way.
    digest.update(
        json.dumps(
            list(
                VocabularyTerm.objects.order_by("vocabulary", "value").values_list(
                    "vocabulary", "value", "spelling", "retired"
                )
            ),
            default=str,
        ).encode()
    )
    for row in rows.iterator(chunk_size=1000):
        digest.update(json.dumps(row, sort_keys=True, default=str).encode())
    return digest.hexdigest()


class ScanRun(models.Model):
    """One run of the publisher scan, so the queue can say when it last ran.

    The scan is what puts questions in the queue, and until now the only
    way to run it was a management command somebody had to remember. A
    reviewer looking at an empty queue could not tell whether there was
    nothing wrong or whether nothing had looked.

    A row is created when a run starts and finished when it ends, so an
    unfinished one is a run in flight. That is the guard: two scans at once
    would each sweep rows the other had just made.
    """

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STATES = [(s, s) for s in (RUNNING, DONE, FAILED)]

    dataset = models.CharField(max_length=100, blank=True, default="")
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=20, choices=STATES, default=RUNNING)
    #: What it did, for the line the queue shows.
    queued = models.PositiveIntegerField(default=0)
    withdrawn = models.PositiveIntegerField(default=0)
    scanned = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.dataset or 'all'} {self.state} {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def in_flight(self):
        return self.state == self.RUNNING

    @classmethod
    def running(cls):
        """The run in flight, or None.

        A run that started and never finished blocks the next one forever,
        so anything older than an hour is treated as dead. The scan takes
        seconds; an hour is not a judgement about how long it should take,
        it is long enough that no live run is ever mistaken for a corpse.
        """
        from datetime import timedelta

        from django.utils import timezone

        cutoff = timezone.now() - timedelta(hours=1)
        return cls.objects.filter(state=cls.RUNNING, started_at__gte=cutoff).first()
