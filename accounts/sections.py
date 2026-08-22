"""The console's sections, as one list the sidebar and the tests share.

Navigation is data, not markup, for a reason: the same list that decides
what a user sees also decides what test_admin_access.py walks. A section
added here without its guard fails the suite.

Everyday work is open to any assigned role. Administration — dataset and
source records, accounts, role assignment, the audit trail and spend — is
the admin role's, and every view behind it enforces that itself.
"""

WORK_SECTIONS = (
    {
        "url": "explorer:articles",
        "label": "Articles",
        "note": "The corpus, filtered by dataset, status, label, scope and geography.",
    },
    {
        "url": "explorer:enrichment",
        "label": "Enrichment",
        "note": "Enrichment records by scope, FIPS claim and skip reason.",
    },
    {
        "url": "review:queue",
        "label": "Review queue",
        "note": "Articles automated triage could not use, awaiting a decision.",
    },
    {
        "url": "visuals:index",
        "label": "Visuals",
        "note": "Published charts and maps, their embeds and pinned snapshots.",
    },
)

ADMIN_SECTIONS = (
    {
        "url": "datasets:list",
        "label": "Datasets",
        "note": "Dataset and source records, enrichment profiles, gazetteer status.",
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
        "note": "Every mutating action, append-only, with its before and after.",
    },
    {
        "url": "explorer:costs",
        "label": "Cost",
        "note": "Recorded against billed, the cache discount, per dataset and model.",
    },
)
