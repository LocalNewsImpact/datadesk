"""The registry's console (SCOPE.md §2.7 v1): visuals are registered,
snapshotted, and published here; authoring stays in code."""

from django.contrib import admin, messages
from django.utils.html import format_html

from visuals.models import Visual, VisualSnapshot
from visuals.services import DataSourceError, publish, refresh_snapshot, unpublish

#: Where an embed points. Its own name because an embed URL, once pasted
#: into somebody's article, cannot be moved (ROADMAP item 24).
EMBED_HOST = "data.localnewsimpact.org"


@admin.register(Visual)
class VisualAdmin(admin.ModelAdmin):
    list_display = ("slug", "title", "status", "pinned_version", "updated_at")
    list_filter = ("status", "source_kind")
    search_fields = ("slug", "title")
    readonly_fields = ("pinned_snapshot", "published_at", "embed_code")
    prepopulated_fields = {"slug": ("title",)}
    actions = ("action_snapshot", "action_publish", "action_unpublish")

    fieldsets = (
        (None, {"fields": ("slug", "title", "status", "template")}),
        ("Data source", {"fields": ("source_kind", "query", "bucket_path")}),
        (
            "Embed",
            {
                "fields": (
                    "frame_ancestors",
                    "allow_live",
                    "pinned_snapshot",
                    "published_at",
                    "embed_code",
                )
            },
        ),
    )

    @admin.display(description="pinned")
    def pinned_version(self, visual):
        return visual.pinned_snapshot.version if visual.pinned_snapshot else "—"

    @admin.display(description="Embed code")
    def embed_code(self, visual):
        """The snippet a publisher pastes.

        A placeholder and a script, not an iframe with a height. 480px was
        wrong for every visual and the person embedding cannot know the
        right number, because it depends on the reader's screen: the framed
        page reports its own height and the script resizes to match
        (ROADMAP item 22).

        The host is data.localnewsimpact.org rather than the console. An
        embed URL is written into somebody else's page and cannot be moved
        afterwards, so it names the public surface rather than an
        implementation detail (ROADMAP item 24).
        """
        if visual.pk is None:
            return "—"
        return format_html(
            '<code>&lt;div class="datadesk-visual" data-visual="{}"&gt;'
            '&lt;a href="https://{}/visuals/{}/"&gt;{}&lt;/a&gt;&lt;/div&gt;<br>'
            '&lt;script src="https://{}/static/js/datadesk-embed.js" '
            "async&gt;&lt;/script&gt;</code>",
            visual.slug,
            EMBED_HOST,
            visual.slug,
            visual.title,
            EMBED_HOST,
        )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Refresh snapshot from the data source")
    def action_snapshot(self, request, queryset):
        for visual in queryset:
            try:
                snapshot = refresh_snapshot(visual, request.user)
            except DataSourceError as exc:
                self.message_user(request, f"{visual.slug}: {exc}", messages.ERROR)
            else:
                self.message_user(
                    request, f"{visual.slug}: snapshot v{snapshot.version}"
                )

    @admin.action(description="Publish (pin the latest snapshot)")
    def action_publish(self, request, queryset):
        for visual in queryset:
            try:
                publish(visual, request.user)
            except DataSourceError as exc:
                self.message_user(request, f"{visual.slug}: {exc}", messages.ERROR)
            else:
                self.message_user(
                    request,
                    f"{visual.slug}: published at v{visual.pinned_snapshot.version}",
                )

    @admin.action(description="Unpublish")
    def action_unpublish(self, request, queryset):
        for visual in queryset:
            unpublish(visual, request.user)
            self.message_user(request, f"{visual.slug}: back to draft")


@admin.register(VisualSnapshot)
class VisualSnapshotAdmin(admin.ModelAdmin):
    """Snapshots are immutable history, like audit entries."""

    list_display = ("visual", "version", "created_by", "created_at")
    readonly_fields = ("visual", "version", "data", "created_by", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
