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

from accounts.access import has_privilege_anywhere, is_application_admin
from accounts.privileges import CREATE, READ, WRITE

# What a group requires, as a privilege rather than a role name. Roles are
# per dataset now, and one person holds several -- so "is this an editor"
# has no answer, while "does this person hold `write` on anything" does.
#
# ANY is `read`: the everyday surface, open to anyone with a dataset to
# look at. The actions inside it carry their own guards.
ANY = READ
EDITOR = WRITE

# Not a privilege. User administration and the audit log are not per
# dataset, so this asks for an application-wide admin grant instead.
ADMIN = "administration"

# Bringing new data in, as opposed to correcting what is there. An
# ordinary privilege -- this name exists so the section reads as what the
# page is called.
IMPORT = CREATE

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
                "url": "explorer:sources",
                # Not "Sources": that is the editor group's own name, and a
                # viewer seeing a section with the group's label would read
                # as the group leaking into a sidebar that hides it.
                "label": "Publishers",
                "note": (
                    "Publisher records in the datasets you can see, and the "
                    "form for reporting one that is out of date."
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
                "label": "Review queue",
                "note": "Publisher records the scan flagged, awaiting a decision.",
            },
            {
                "url": "review:import_batches",
                "requires": IMPORT,
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
        "requires": EDITOR,
        "sections": (
            {
                "url": "review:queue",
                "label": "Review queue",
                "note": "Articles automated triage could not use, awaiting a decision.",
            },
        ),
    },
    # Cost sits on its own rather than under Admin. ROADMAP item 1 put
    # spend on `write` -- a management fact, not a research one, but not
    # an administrative one either -- so an editor sees it for the
    # datasets they write. Leaving it under a group labelled Admin would
    # have meant either lying to an editor about why they can see it, or
    # hiding a page they are allowed to open. Item 19's Production group
    # is where this belongs once that exists.
    {
        "label": "Cost",
        "requires": EDITOR,
        "sections": (
            {
                "url": "explorer:costs",
                "label": "Cost",
                "note": (
                    "Recorded against billed, the cache discount, "
                    "per dataset and model."
                ),
            },
        ),
    },
    {
        "label": "Admin",
        "requires": ADMIN,
        "sections": (
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


def _reaches(user, app, requirement):
    """Does this person reach a section with this requirement?

    There is no precedence to apply. The three global groups needed one --
    an editor implied a viewer -- but a privilege answers for itself, and
    a person holding `write` on one dataset and `read` on another reaches
    both kinds of section without any role being ranked above another.
    """
    if requirement == ADMIN:
        return is_application_admin(user, app)
    return has_privilege_anywhere(user, app, requirement)


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


def groups_for(user, app=None):
    """The navigation groups this person sees, in order, each carrying
    only the sections they reach. A group with nothing left is absent
    rather than empty.

    Takes a user rather than a role, because a person now holds several
    roles across datasets and no single one of them describes what they
    can see.
    """
    from accounts.decorators import APP

    app = app or APP
    if user is None:
        return ()
    groups = []
    for group in SECTION_GROUPS:
        visible = tuple(
            _rendered(section)
            for section in group["sections"]
            if _reaches(user, app, requires_for(group, section))
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
