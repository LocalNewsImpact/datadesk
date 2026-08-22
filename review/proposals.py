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
    # What the checks concluded, in the order a reviewer meets them.
    READY = "ready"
    OWNER_CONFLICT = "owner_conflict"
    UNKNOWN_OWNER = "unknown_owner"
    GAZETTEER = "gazetteer"
    DUPLICATE = "duplicate"
    NO_MATCH = "no_match"
    FINDINGS = [
        (READY, "Passes every check"),
        (OWNER_CONFLICT, "Names a different owner"),
        (UNKNOWN_OWNER, "Owner not recognised"),
        (GAZETTEER, "Not a place or county here"),
        (DUPLICATE, "Source file disagrees with itself"),
        (NO_MATCH, "No such record"),
    ]

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

    finding = models.CharField(max_length=30, choices=FINDINGS)
    why = models.TextField(blank=True, default="")
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
    def check_failed(self):
        """Did a check reject the proposed value?

        Accepting one of these writes a value the checks refused, which
        is a reviewer overruling the check — legitimate, but it must not
        look like the ordinary path.
        """
        return self.finding in (
            self.OWNER_CONFLICT,
            self.UNKNOWN_OWNER,
            self.GAZETTEER,
        )

    @property
    def actionable(self):
        """Can a reviewer accept this as proposed?

        A duplicate or an unmatched record has nothing safe to accept —
        the file disagrees with itself, or names a record that is not
        there — so those are reported for correction at the source.
        """
        return self.finding not in (self.DUPLICATE, self.NO_MATCH)
