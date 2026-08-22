"""Import batches and saved export definitions (SCOPE.md §2.4).

Application state, in Datadesk's own database — the crawler corpus is
touched only at apply time, through the audited write path.
"""

from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    """One uploaded CSV moving through the backpatch protocol:
    upload → column mapping → diff report → explicit apply.

    The batch id is the unit of inspection and revert. Rows are stored
    verbatim at upload so the diff and the apply read the same data.
    """

    UPLOADED = "uploaded"
    MAPPED = "mapped"
    APPLIED = "applied"
    REVERTED = "reverted"
    STATUSES = [(s, s) for s in (UPLOADED, MAPPED, APPLIED, REVERTED)]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    filename = models.CharField(max_length=255)
    columns = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    key_column = models.CharField(max_length=100, blank=True, default="")
    column_map = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUSES, default=UPLOADED)
    applied_at = models.DateTimeField(null=True, blank=True)
    audit_entry = models.ForeignKey(
        "audit.AuditLogEntry", null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.status})"


class ExportDefinition(models.Model):
    """A saved export: the grid's filter params plus a column list,
    re-runnable against current data (SCOPE.md §2.4)."""

    name = models.CharField(max_length=200, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    params = models.JSONField(default=dict)
    columns = models.JSONField(default=list)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
