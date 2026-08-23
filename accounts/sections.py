"""The console's sections, as one list the sidebar and the tests share.

Navigation is data, not markup, for a reason: the same list that decides
what a user sees also decides what test_admin_access.py walks. A section
added here without its guard fails the suite.

Each group names the role it requires, rather than that role being
implied by which of two lists a section sits in, and a section may
raise that bar for itself. The guard belongs beside the link: a
section's effective role and the decorator on its view are checked
against each other, so moving a link between groups cannot quietly
widen or narrow who reaches it.

A group appears when the role reaches at least one of its sections, and
shows only the sections it reaches — an editor working publisher
records sees the Sources group without the admin-only tools in it.

A section may point at another LNIC service instead of a Datadesk view.
Those carry `site` (an absolute URL) rather than `url`, and the console
cannot enforce a role on them — the destination does its own sign-in.
They are listed here anyway so the nav is one list: a reader should not
have to know which console a tool happens to live in.
"""

from accounts.roles import ADMIN, EDITOR, VIEWER

# What a group requires. ANY means any assigned role — the everyday
# surface, open to viewers; the actions inside it carry their own guards.
ANY = VIEWER

SECTION_GROUPS = (
    {
        "label": "Data",
        "requires": ANY,
        "sections": (
            {
                "url": "explorer:articles",
                "label": "Articles",
                "note": (
                    "The corpus, filtered by dataset, status, label, "
                    "scope and geography."
                ),
            },
            {
                "url": "explorer:enrichment",
                "label": "Enrichment",
                "note": "Enrichment records by scope, FIPS claim and skip reason.",
            },
            {
                "url": "visuals:index",
                "label": "Visuals",
                "note": "Published charts and maps, their embeds and pinned snapshots.",
            },
        ),
    },
    {
        # "Proposed changes" does not say what it covers. Under this
        # header it does: these are publisher records.
        "label": "Sources",
        "requires": EDITOR,
        "sections": (
            {
                "url": "review:proposals",
                "label": "Proposed changes",
                "note": "Publisher records the scan flagged, awaiting a decision.",
            },
            {
                "url": "review:import_batches",
                "label": "Import",
                "note": "Upload a spreadsheet, map its columns, review the diff.",
            },
            {
                "url": "datasets:source_create",
                "label": "Add a publisher",
                "requires": ADMIN,
                "note": "Create a source record and attach it to a dataset.",
            },
            {
                "site": "https://sources.localnewsimpact.org/",
                "label": "Source directory",
                "note": (
                    "The registry of local news outlets: the record of "
                    "record, its public widget and versioned exports."
                ),
            },
        ),
    },
    {
        "label": "Extraction",
        "requires": ANY,
        "sections": (
            {
                "url": "review:queue",
                "label": "Review queue",
                "note": "Articles automated triage could not use, awaiting a decision.",
            },
        ),
    },
    {
        "label": "Admin",
        "requires": ADMIN,
        "sections": (
            {
                "url": "explorer:costs",
                "label": "Cost",
                "note": (
                    "Recorded against billed, the cache discount, "
                    "per dataset and model."
                ),
            },
            {
                "url": "datasets:list",
                "label": "Datasets",
                "note": (
                    "Dataset and source records, enrichment profiles, "
                    "gazetteer status."
                ),
            },
            {
                "url": "accounts:users",
                "label": "Users",
                "note": "Who can sign in, their role, and when they last did.",
            },
            {
                "url": "accounts:roles",
                "label": "Roles",
                "note": "Role assignment: viewer, editor, admin.",
            },
            {
                "url": "review:audit_log",
                "label": "Audit log",
                "note": "Every mutating action, append-only, before and after.",
            },
        ),
    },
)

# A role reaches its own groups and every group below it.
_REACH = {
    None: (),
    VIEWER: (ANY,),
    EDITOR: (ANY, EDITOR),
    ADMIN: (ANY, EDITOR, ADMIN),
}


def requires_for(group, section):
    """The role a section actually needs: its own, or its group's."""
    return section.get("requires", group["requires"])


def _rendered(section):
    """A section with its link resolved, for a template to render.

    `href` is the one thing markup needs; `url` survives so the active
    marker can still compare route names, and is absent on sections that
    point at another service.
    """
    from django.urls import reverse

    site = section.get("site")
    return {
        "label": section["label"],
        "note": section["note"],
        "href": site or reverse(section["url"]),
        "url": None if site else section["url"],
        "external": bool(site),
    }


def groups_for(role):
    """The navigation groups a role sees, in order, each carrying only
    the sections that role reaches. A group with nothing left is absent
    rather than empty."""
    reach = _REACH.get(role, ())
    groups = []
    for group in SECTION_GROUPS:
        visible = tuple(
            _rendered(section)
            for section in group["sections"]
            if requires_for(group, section) in reach
        )
        if visible:
            groups.append({"label": group["label"], "sections": visible})
    return tuple(groups)


def internal_sections():
    """Sections backed by a Datadesk view, paired with the role they
    require. Sections pointing at another service are excluded: there is
    no local view to guard."""
    return tuple(
        (section, requires)
        for section, requires in all_sections()
        if not section.get("site")
    )


def external_sections():
    """Sections pointing at another LNIC service."""
    return tuple(section for section, _ in all_sections() if section.get("site"))


def all_sections():
    """Every section, flat, paired with the role it effectively requires."""
    return tuple(
        (section, requires_for(group, section))
        for group in SECTION_GROUPS
        for section in group["sections"]
    )
