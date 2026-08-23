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
    record_id = models.TextField()
    record_label = models.TextField(blank=True, default="")
    dataset = models.CharField(max_length=100, blank=True, default="")

    field = models.CharField(max_length=60)
    current_value = models.TextField(blank=True, default="")
    proposed_value = models.TextField(blank=True, default="")

    flag = models.CharField(max_length=40, default="")
    # What makes this record's instance of the defect specific.
    detail = models.TextField(blank=True, default="")
    suggestion = models.TextField(blank=True, default="")

    state = models.CharField(max_length=20, choices=STATES, default=PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
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
