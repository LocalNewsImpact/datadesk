"""Dataset creation and maintenance (SCOPE.md §2.5). Admin role.

All corpus mutations flow through the audited write path; the forms
enforce the invariants learned this cycle — gazetteer-validated cities,
membership consequences surfaced, profile schema validation with the
version-bump contract."""

import json
import uuid

from django.db.models import Count, Max
from django.http import Http404
from django.shortcuts import redirect, render

from accounts.decorators import requires, requires_admin
from accounts.privileges import READ, WRITE

# Decided 2026-08-23, not yet built: an editor sees the datasets they
# hold in `dataset_list` and may change anything inside their own,
# publishers included -- with a shared publisher's edit becoming a
# proposal to the other datasets holding it, and a dataset's public flag
# deciding whether its publishers are visible outside it. See ROADMAP
# items 1 and 10. Until that lands these stay admin-only rather than
# half-scoped. ROADMAP item 1 says an editor starts
# a dataset and then owns it, and `accounts.access.may_create_dataset`
# answers that -- but wiring it here also means deciding what a non-admin
# sees on `dataset_list` and `dataset_detail`, which carry admin
# affordances beyond the dataset itself. That is its own change.
from datasets.models import GazetteerBuildRequest
from datasets.places import validate_city
from datasets.profiles import (
    EXCLUDABLE_SCOPES,
    PRODUCTION_PRESETS,
    ProfileError,
    requires_version_bump,
    validate_profile,
)
from explorer.models import Dataset, DatasetSource, Gazetteer, Source
from review.services import (
    audited_create,
    audited_delete,
    audited_update,
)


@requires_admin
def dataset_list(request):
    datasets = list(Dataset.objects.order_by("label"))
    members = dict(
        DatasetSource.objects.values_list("dataset_id")
        .annotate(n=Count("id"))
        .values_list("dataset_id", "n")
    )
    for dataset in datasets:
        dataset.member_count = members.get(dataset.id, 0)
        meta = dataset.meta or {}
        dataset.default_state = meta.get("default_state", "")
        dataset.profile_version = (meta.get("enrichment_profile") or {}).get(
            "version", "—"
        )
    return render(request, "datasets/list.html", {"datasets": datasets})


@requires_admin
def dataset_create(request):
    error = None
    if request.method == "POST":
        slug = request.POST.get("slug", "").strip()
        label = request.POST.get("label", "").strip()
        if not slug or not label:
            error = "Slug and label are required."
        elif Dataset.objects.filter(slug=slug).exists():
            error = f"A dataset with slug '{slug}' already exists."
        else:
            dataset = Dataset(
                id=str(uuid.uuid4()),
                slug=slug,
                label=label,
                name=request.POST.get("name", "").strip() or None,
                description=request.POST.get("description", "").strip() or None,
                meta={},
                cron_enabled=False,  # a new dataset collects nothing until enabled
            )
            audited_create(
                request.user,
                [dataset],
                action="dataset:create",
                reason=f"created dataset {slug}",
            )
            return redirect("datasets:detail", slug)
    return render(request, "datasets/create.html", {"error": error})


def _get_dataset(user, slug, privilege=READ):
    """The dataset, if this person may exercise `privilege` on it.

    Scoped rather than fetched: an unscoped lookup here would let anyone
    with a grant on any dataset open every other one by URL.
    """
    from explorer.scoping import datasets_for

    dataset = datasets_for(user, privilege).filter(slug=slug).first()
    if dataset is None:
        raise Http404("No such dataset")
    return dataset


@requires_admin
def dataset_detail(request, slug):
    dataset = _get_dataset(request.user, slug)
    meta = dataset.meta or {}
    error = request.session.pop("datasets_error", None)

    if request.method == "POST":
        form = request.POST.get("form")
        if form == "fields":
            new_meta = dict(meta)
            default_state = request.POST.get("default_state", "").strip().upper()
            if default_state:
                new_meta["default_state"] = default_state
            else:
                new_meta.pop("default_state", None)
            audited_update(
                request.user,
                [dataset],
                {
                    "name": request.POST.get("name", "").strip() or None,
                    "description": request.POST.get("description", "").strip() or None,
                    "cron_enabled": request.POST.get("cron_enabled") == "1",
                    "meta": new_meta,
                    # Attribution for anything published from this
                    # dataset. Blank stores null rather than "", so
                    # "nobody has said" and "somebody said nothing" are
                    # the same state and a published page can test one
                    # thing to decide whether to credit anybody.
                    "owner_name": request.POST.get("owner_name", "").strip() or None,
                    "owner_email": request.POST.get("owner_email", "").strip() or None,
                },
                action="dataset:edit",
                reason=request.POST.get("reason", ""),
            )
        elif form == "profile":
            try:
                profile = json.loads(request.POST.get("profile", ""))
                validate_profile(profile)
                old = meta.get("enrichment_profile")
                if requires_version_bump(old, profile):
                    raise ProfileError(
                        "The profile content changed but the version did not "
                        "increase. Bump version — the pipeline reprocesses "
                        "articles whose profile_version is lower — or revert "
                        "the content change."
                    )
            except json.JSONDecodeError as exc:
                request.session["datasets_error"] = f"Not valid JSON: {exc}"
            except ProfileError as exc:
                request.session["datasets_error"] = str(exc)
            else:
                new_meta = dict(meta) | {"enrichment_profile": profile}
                audited_update(
                    request.user,
                    [dataset],
                    {"meta": new_meta},
                    action="dataset:profile",
                    reason=f"profile v{profile['version']}",
                )
        elif form == "add_source":
            source = Source.objects.filter(pk=request.POST.get("source_id")).first()
            if (
                source
                and not DatasetSource.objects.filter(
                    dataset_id=dataset.id, source_id=source.id
                ).exists()
            ):
                audited_create(
                    request.user,
                    [
                        DatasetSource(
                            id=str(uuid.uuid4()), dataset=dataset, source=source
                        )
                    ],
                    action="dataset:add_source",
                    reason=f"{source.host_norm} into {dataset.slug}",
                )
        elif form == "remove_source":
            membership = DatasetSource.objects.filter(
                dataset_id=dataset.id, source_id=request.POST.get("source_id")
            ).first()
            if membership:
                audited_delete(
                    request.user,
                    [membership],
                    action="dataset:remove_source",
                    reason=request.POST.get("reason", ""),
                )
        elif form == "gazetteer_build":
            GazetteerBuildRequest.objects.create(
                dataset_slug=dataset.slug,
                state=meta.get("default_state", ""),
                requested_by=request.user,
            )
        return redirect("datasets:detail", slug)

    memberships = list(
        DatasetSource.objects.filter(dataset_id=dataset.id).select_related("source")
    )
    gazetteer = {
        row["source_id"]: row
        for row in Gazetteer.objects.filter(dataset_id=dataset.id)
        .values("source_id")
        .annotate(entries=Count("id"), last_built=Max("created_at"))
    }
    for membership in memberships:
        stats = gazetteer.get(membership.source_id)
        membership.gazetteer_entries = stats["entries"] if stats else 0
        membership.gazetteer_built = stats["last_built"] if stats else None

    default_state = (dataset.meta or {}).get("default_state", "")
    # SCOPE.md §2.5: when a dataset enters a new state, the missing
    # Geofabrik extract is flagged — no gazetteer rows in the dataset at
    # all means the offline extract likely is not staged yet.
    new_state_warning = (
        default_state and not Gazetteer.objects.filter(dataset_id=dataset.id).exists()
    )

    profile = (dataset.meta or {}).get("enrichment_profile")
    return render(
        request,
        "datasets/detail.html",
        {
            "dataset": dataset,
            "default_state": default_state,
            "memberships": memberships,
            "other_sources": Source.objects.exclude(
                id__in=[m.source_id for m in memberships]
            ).order_by("host_norm"),
            "profile_json": json.dumps(profile, indent=2) if profile else "",
            "presets": PRODUCTION_PRESETS,
            "excludable": EXCLUDABLE_SCOPES,
            "build_requests": GazetteerBuildRequest.objects.filter(
                dataset_slug=dataset.slug
            )[:5],
            "new_state_warning": new_state_warning,
            "error": error,
        },
    )


#: The paywall panel. Not schema fields: a checkbox, an amount and the
#: period it covers are three shapes the schema's rules do not have, and
#: they belong together on the record as one question -- is this behind a
#: paywall, what does it cost, and where does a person sign in.
PAYWALL_FIELDS = (
    "has_paywall",
    "subscription_cost",
    "subscription_period",
    "login_url",
)

PERIODS = ("monthly", "annual")


def _paywall_from_form(post, errors):
    """The paywall panel's values, checked.

    No credentials. The username and password for a publisher live in
    Secret Manager under `auth_secret_name`, which is what the crawler
    does and why `auth_config` carries its comment that credentials are
    never stored in the table: a password column would be readable by
    every role holding SELECT on sources.
    """
    from decimal import Decimal, InvalidOperation

    from datasets.schema import OPTIONAL, URL, FieldSpec, check

    out = {
        # Unticked is False rather than null. "Nobody has looked" and
        # "there is no paywall" are different answers and a null cannot
        # tell them apart, so the box says what somebody decided.
        "has_paywall": bool(post.get("has_paywall")),
        "subscription_cost": None,
        "subscription_period": "",
        "login_url": "",
    }

    cost = (post.get("subscription_cost") or "").strip().lstrip("$").replace(",", "")
    if cost:
        try:
            amount = Decimal(cost)
        except InvalidOperation:
            errors.append(f"Subscription cost: {cost!r} is not an amount.")
        else:
            if amount < 0:
                errors.append(
                    "Subscription cost: a subscription cannot cost less than nothing."
                )
            else:
                # As a string. The column is a Decimal and Django coerces
                # it back on save, but the audit entry beside it is JSON,
                # and a Decimal is not JSON -- so an amount somebody typed
                # raised on the way to being recorded rather than saved.
                out["subscription_cost"] = str(amount)

    period = (post.get("subscription_period") or "").strip().lower()
    if period and period not in PERIODS:
        errors.append(f"Subscription period: {period!r} is not monthly or annual.")
    elif period:
        out["subscription_period"] = period

    url = (post.get("login_url") or "").strip()
    if url:
        ok, why = check(
            FieldSpec("login_url", "Login page", need=OPTIONAL, rule=URL), url
        )
        if ok:
            out["login_url"] = url
        else:
            errors.append(f"Login page: {why}")

    # An amount with no period is a number nobody can read: $12 a month
    # and $12 a year are different subscriptions.
    if out["subscription_cost"] is not None and not out["subscription_period"]:
        errors.append("Subscription cost: say whether that is monthly or annual.")
    return out


def _paywall_of(source):
    """The paywall panel as the record holds it, for the form.

    `requires_login` and what it needs are the crawler's and are shown
    rather than edited: they are how the extractor signs in, configured
    when somebody automates a publisher, and the secret named there is
    the one thing on this page that must not be settable from a form
    field.
    """
    return {
        "has_paywall": bool(getattr(source, "has_paywall", False)),
        "subscription_cost": (
            "" if source.subscription_cost is None else str(source.subscription_cost)
        ),
        "subscription_period": source.subscription_period or "",
        "login_url": source.login_url or "",
        "periods": PERIODS,
        # What the crawler already knows how to do here, read-only.
        "requires_login": bool(getattr(source, "requires_login", False)),
        "auth_type": source.auth_type or "",
        "secret_name": source.auth_secret_name or "",
        # A login page the extractor was configured with, where the
        # record itself has none: better than showing nothing and letting
        # somebody type in what is already known.
        "configured_login_url": ((source.auth_config or {}).get("login_url") or ""),
    }


def _source_form_fields(values):
    """The form's inputs, from the schema.

    Five were listed here by hand and the schema declares thirteen, so a
    publisher added through this page could not be given a ZIP code, an
    address or a telephone number -- and the state it insisted on before
    it would accept a city was thrown away by the save.

    The host is not among them: it is the record's identity, asked for
    once when the record is made and never edited.

    A vocabulary field is a text box with its words suggested rather than
    a menu of them. A record already holding a word nobody listed would
    lose it to a menu that cannot show it, and what is not listed is a
    question for the queue rather than something to silently drop.
    """
    from datasets.schema import FIELDS as SCHEMA_FIELDS
    from datasets.terms import terms

    out = []
    for field in SCHEMA_FIELDS:
        if field.key == "host":
            continue
        name = field.key.partition(".")[2] if field.in_meta else field.key
        words = []
        if field.vocabulary:
            words = sorted(
                {
                    spelling or value
                    for value, (spelling, _label) in terms(field.vocabulary).items()
                }
            )
        out.append(
            {
                "name": name,
                "label": field.label,
                "required": field.required,
                "suggested": field.need == "suggested",
                "rule": field.rule_name,
                "value": values.get(name, "") if values else "",
                "words": words,
            }
        )
    return out


def _validate_source_form(post):
    """Shared validation for source create/edit.

    Reads `datasets/schema.py` rather than listing the fields again. The
    form knew five of them and the schema declares thirteen, so a record
    made here could not be given a ZIP code, an address or a telephone
    number at all -- and nothing it could be given was checked against
    anything.

    Returns (columns, meta, errors).
    """
    from datasets.schema import FIELDS as SCHEMA_FIELDS
    from datasets.schema import check as check_value

    columns, meta = {}, {}
    errors, suggestions = [], []
    for field in SCHEMA_FIELDS:
        if field.key == "host":
            # Read by the caller: it is the record's identity, and what
            # makes a second row for one host a different question.
            continue
        # `state` and `zip`, not `meta.state` and `meta.zip`. The form
        # names a field the way somebody says it.
        name = field.key.partition(".")[2] if field.in_meta else field.key
        value = post.get(name, "").strip()
        if field.key == "meta.state":
            value = value.upper()
        # A missing value is not refused here, even where the schema
        # calls the field required. Required means the scan asks about a
        # record that lacks it -- refusing it at the door would mean a
        # publisher somebody has just found, and half knows, cannot be
        # written down at all. The form marks which fields are needed;
        # the queue is what chases them.
        if not value:
            continue
        ok, why = check_value(field, value)
        if not ok:
            errors.append(f"{field.label}: {why}")
            continue
        if field.in_meta:
            meta[field.key.partition(".")[2]] = value
        else:
            columns[field.key] = value

    columns.update(_paywall_from_form(post, errors))

    fields = {
        key: columns.get(key)
        for key in (
            "canonical_name",
            "city",
            "county",
            "owner",
            "type",
            *PAYWALL_FIELDS,
        )
    }
    # A blank text column is null; a paywall that is not ticked is False,
    # which is an answer rather than an absence.
    for key, value in list(fields.items()):
        if key not in PAYWALL_FIELDS:
            fields[key] = value or None
    state = meta.get("state", "")
    if fields["city"]:
        if not state:
            errors.append("A state is required to validate the city.")
        else:
            known, suggestions = validate_city(state, fields["city"])
            if not known:
                message = (
                    f"'{fields['city']}' is not in the Census place gazetteer "
                    f"for {state}."
                )
                if suggestions:
                    message += f" Did you mean: {', '.join(suggestions)}?"
                errors.append(message)
    return fields, state, errors, meta


@requires_admin
def source_create(request):
    context = {
        "values": {},
        "fields": _source_form_fields({}),
        "errors": [],
        "datasets": Dataset.objects.order_by("label"),
    }
    if request.method == "POST":
        host = request.POST.get("host", "").strip().lower()
        fields, state, errors, meta = _validate_source_form(request.POST)
        if not host:
            errors.append("A host is required.")
        elif Source.objects.filter(host_norm=host).exists():
            errors.append(f"A source for {host} already exists.")
        if errors:
            context["errors"] = errors
            context["values"] = request.POST
            context["fields"] = _source_form_fields(request.POST)
            return render(request, "datasets/source_form.html", context, status=400)
        source = Source(
            id=str(uuid.uuid4()),
            host=host,
            host_norm=host,
            # Everything the schema declares inside `meta`, not the state
            # alone: a record made here could not be given a ZIP code, an
            # address or a telephone number, because this named one key.
            meta=meta,
            **fields,
        )
        audited_create(
            request.user, [source], action="source:create", reason=f"added {host}"
        )
        if slug := request.POST.get("dataset"):
            dataset = Dataset.objects.filter(slug=slug).first()
            if dataset:
                audited_create(
                    request.user,
                    [
                        DatasetSource(
                            id=str(uuid.uuid4()), dataset=dataset, source=source
                        )
                    ],
                    action="dataset:add_source",
                    reason=f"{host} into {dataset.slug} at creation",
                )
                return redirect("datasets:detail", dataset.slug)
        return redirect("datasets:list")
    return render(request, "datasets/source_form.html", context)


@requires(WRITE)
def source_edit(request, source_id):
    """Editing a source is a dataset privilege, not an application one.

    A 404 rather than a 403 for a source outside the person's datasets:
    telling somebody a record exists but is not theirs to edit says more
    than the guard is willing to.
    """
    source = _reachable(request.user, source_id, WRITE)
    if source is None:
        raise Http404("No such source")
    # Every field the schema declares, read off the record. Six were
    # listed here and the other seven arrived empty, so opening a record
    # and saving it silently emptied whichever of them it had.
    from datasets.schema import FIELDS as SCHEMA_FIELDS
    from datasets.schema import read as read_field

    values = {
        (f.key.partition(".")[2] if f.in_meta else f.key): read_field(source, f.key)
        for f in SCHEMA_FIELDS
        if f.key != "host"
    }
    context = {
        "source": source,
        "values": values,
        "fields": _source_form_fields(values),
        "paywall": _paywall_of(source),
        "errors": [],
    }
    if request.method == "POST":
        fields, state, errors, meta = _validate_source_form(request.POST)
        if errors:
            context["errors"] = errors
            context["values"] = request.POST
            context["fields"] = _source_form_fields(request.POST)
            context["paywall"] = {
                **context["paywall"],
                **_paywall_from_form(request.POST, []),
            }
            return render(request, "datasets/source_form.html", context, status=400)
        # The keys inside `meta` as well as the columns. This wrote the
        # columns alone, so the state a form insisted on before it would
        # accept a city was then thrown away -- and every other key the
        # schema declares had no way to be edited at all.
        changes = dict(fields)
        changes.update({f"meta.{name}": value for name, value in meta.items()})
        audited_update(
            request.user,
            [source],
            changes,
            action="source:edit",
            reason=request.POST.get("reason", ""),
        )
        # Answered where it was asked. Editing a publisher from the review
        # queue is one question inside another, and sending somebody to
        # the datasets list afterwards loses the queue they were working.
        if request.GET.get("bare"):
            return render(request, "datasets/source_form.html", context)
        return redirect("datasets:list")
    # `?bare=1` is the same form with the console taken off, for a dialog
    # to hold. The link works without it -- and without any JavaScript --
    # because it is the ordinary edit page.
    context["bare"] = bool(request.GET.get("bare"))
    return render(request, "datasets/source_form.html", context)


def _reachable(user, source_id, privilege):
    """The source, if this person may exercise `privilege` on a dataset it
    belongs to. `narrow` reaches a source through DatasetSource; from a
    Source queryset that path is the row's own key."""
    from explorer.scoping import narrow

    qs = narrow(Source.objects.filter(pk=source_id), user, privilege, source_path="id")
    return qs.first()


@requires(READ)
def source_propose(request, source_id):
    """Offer a change to a source without writing one.

    Anyone who can see a record can know something true about it -- an
    owner who sold, a paper that folded -- and until now had nowhere to put
    it: editing was an application-admin action. This writes proposals, not
    the corpus, and they land in the same queue as every machine-generated
    finding, where a person with write access decides.
    """
    source = _reachable(request.user, source_id, READ)
    if source is None:
        raise Http404("No such source")

    current = {
        "canonical_name": source.canonical_name or "",
        "city": source.city or "",
        "county": source.county or "",
        "owner": source.owner or "",
        "type": source.type or "",
        "state": (source.meta or {}).get("state", ""),
    }
    context = {"source": source, "values": dict(current), "errors": []}

    if request.method == "POST":
        from review.proposals import ChangeProposal

        citation = (request.POST.get("citation") or "").strip()
        # Only fields the form actually sent. Reading a missing key as ""
        # turns a partial submission into a proposal to blank everything it
        # left out.
        submitted = {
            f: (request.POST.get(f) or "").strip() for f in current if f in request.POST
        }
        changed = {f: v for f, v in submitted.items() if v != current[f]}

        errors = []
        if not changed:
            errors.append("Nothing is different from what the record holds.")
        if not citation:
            errors.append("Say where this came from — a URL, a filing, a call.")
        if errors:
            context["errors"] = errors
            context["values"] = submitted
            context["citation"] = citation
            return render(request, "datasets/source_propose.html", context, status=400)

        # One proposal per field: the queue groups them back into a single
        # decision per record, and a reviewer may take the owner and leave
        # the county.
        # Which dataset's queue this lands in: the one the proposer reaches
        # the source through. A source in several datasets the proposer can
        # read is ambiguous only in principle -- the queue groups by record,
        # so naming the first by slug keeps one row per decision.
        from accounts.access import ALL_SCOPES
        from explorer.models import DatasetSource
        from explorer.scoping import scopes_for

        memberships = DatasetSource.objects.filter(source_id=source.pk)
        scopes = scopes_for(request.user, READ)
        if scopes is not ALL_SCOPES:
            memberships = memberships.filter(dataset__slug__in=scopes)
        slug = (
            memberships.order_by("dataset__slug")
            .values_list("dataset__slug", flat=True)
            .first()
            or ""
        )

        ChangeProposal.objects.bulk_create(
            [
                ChangeProposal(
                    target="sources",
                    record_id=source.pk,
                    record_label=source.canonical_name or source.host,
                    field=field,
                    current_value=current[field],
                    proposed_value=value,
                    flag="reported",
                    origin="reported",
                    dataset=slug,
                    citation=citation,
                    proposed_by=request.user,
                    detail=(request.POST.get("detail") or "").strip(),
                    state=ChangeProposal.PENDING,
                )
                for field, value in changed.items()
            ]
        )
        return redirect("datasets:list")

    return render(request, "datasets/source_propose.html", context)


@requires(READ)
def source_propose_new(request):
    """Report a publisher the corpus has never heard of.

    The other propose form changes a record that exists. This one is the
    proposal that a record should exist at all: it writes fields with no
    record_id, and accepting them in the queue is what creates the source.

    Open to read for the same reason as the other: somebody who can see a
    dataset is usually the person who notices a paper missing from it.
    """
    from explorer.scoping import datasets_for

    FIELDS = ("canonical_name", "host", "city", "county", "state", "owner", "type")
    context = {
        "values": {},
        "errors": [],
        "datasets": datasets_for(request.user, READ),
    }

    if request.method == "POST":
        import uuid as _uuid

        from review.proposals import ChangeProposal

        values = {f: (request.POST.get(f) or "").strip() for f in FIELDS}
        values["host"] = values["host"].lower()
        values["state"] = values["state"].upper()
        citation = (request.POST.get("citation") or "").strip()
        slug = (request.POST.get("dataset") or "").strip()

        errors = []
        if not values["host"]:
            errors.append("A host is required — it is how the crawler reaches it.")
        elif Source.objects.filter(host_norm=values["host"]).exists():
            errors.append(
                f"A record for {values['host']} already exists. Propose a change "
                "to it instead of a new publisher."
            )
        if not values["canonical_name"]:
            errors.append("A name is required.")
        if not citation:
            errors.append("Say where this came from — a URL, a filing, a call.")
        if slug and not datasets_for(request.user, READ).filter(slug=slug).exists():
            errors.append("That is not a dataset you can see.")

        if errors:
            context["errors"] = errors
            context["values"] = values
            context["citation"] = citation
            return render(
                request, "datasets/source_propose_new.html", context, status=400
            )

        # One submission, so the queue groups these into a single decision
        # rather than seven unrelated rows sharing an empty record id.
        submission = _uuid.uuid4()
        ChangeProposal.objects.bulk_create(
            [
                ChangeProposal(
                    target="sources",
                    record_id="",
                    submission=submission,
                    record_label=values["canonical_name"],
                    field=field,
                    current_value="",
                    proposed_value=value,
                    flag="no_match",
                    origin="reported",
                    dataset=slug,
                    citation=citation,
                    proposed_by=request.user,
                    detail=(request.POST.get("detail") or "").strip(),
                    state=ChangeProposal.PENDING,
                )
                for field, value in values.items()
                if value
            ]
        )
        return redirect("explorer:sources")

    return render(request, "datasets/source_propose_new.html", context)
