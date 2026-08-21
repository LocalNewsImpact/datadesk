"""Read-only admin surface for the audit log (SCOPE.md §2.1)."""

from django.contrib import admin

from audit.models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    """Visible in the admin; no add, change, or delete."""

    list_display = ("timestamp", "actor", "action", "target_table")
    list_filter = ("action", "target_table")
    search_fields = ("action", "target_table", "actor__email")
    date_hierarchy = "timestamp"
    readonly_fields = [
        "actor",
        "timestamp",
        "action",
        "target_table",
        "target_ids",
        "before",
        "after",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
