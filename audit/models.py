"""The append-only audit-log model (SCOPE.md §2.1)."""

from django.conf import settings
from django.db import models


class AppendOnlyError(Exception):
    """Raised on any attempt to update or delete an audit entry."""


class AuditLogEntry(models.Model):
    """One mutating action: who, when, what, and the before/after values.

    Append-only: updates and deletes raise at the model level, and the
    admin registration exposes no add/change/delete permissions. Reverts
    (SCOPE.md §2.2) are performed by writing a new compensating action,
    never by editing history.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=100)
    target_table = models.CharField(max_length=100)
    target_ids = models.JSONField(default=list)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    # Dispositions carry recorded reasons (SCOPE.md §2.2); free text.
    reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "audit log entry"
        verbose_name_plural = "audit log entries"

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} on {self.target_table}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AppendOnlyError("Audit log entries cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit log entries cannot be deleted.")
