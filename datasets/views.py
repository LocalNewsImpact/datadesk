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

from accounts.decorators import admin_required
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


@admin_required
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


@admin_required
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


def _get_dataset(slug):
    dataset = Dataset.objects.filter(slug=slug).first()
    if dataset is None:
        raise Http404("No such dataset")
    return dataset


@admin_required
def dataset_detail(request, slug):
    dataset = _get_dataset(slug)
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


def _validate_source_form(post):
    """Shared validation for source create/edit. Returns (fields, errors,
    suggestions)."""
    fields = {
        "canonical_name": post.get("canonical_name", "").strip() or None,
        "city": post.get("city", "").strip() or None,
        "county": post.get("county", "").strip() or None,
        "owner": post.get("owner", "").strip() or None,
        "type": post.get("type", "").strip() or None,
    }
    state = post.get("state", "").strip().upper()
    errors, suggestions = [], []
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
    return fields, state, errors


@admin_required
def source_create(request):
    context = {
        "values": {},
        "errors": [],
        "datasets": Dataset.objects.order_by("label"),
    }
    if request.method == "POST":
        host = request.POST.get("host", "").strip().lower()
        fields, state, errors = _validate_source_form(request.POST)
        if not host:
            errors.append("A host is required.")
        elif Source.objects.filter(host_norm=host).exists():
            errors.append(f"A source for {host} already exists.")
        if errors:
            context["errors"] = errors
            context["values"] = request.POST
            return render(request, "datasets/source_form.html", context, status=400)
        source = Source(
            id=str(uuid.uuid4()),
            host=host,
            host_norm=host,
            meta={"state": state} if state else {},
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


@admin_required
def source_edit(request, source_id):
    source = Source.objects.filter(pk=source_id).first()
    if source is None:
        raise Http404("No such source")
    context = {
        "source": source,
        "values": {
            "canonical_name": source.canonical_name or "",
            "city": source.city or "",
            "county": source.county or "",
            "owner": source.owner or "",
            "type": source.type or "",
            "state": (source.meta or {}).get("state", ""),
        },
        "errors": [],
    }
    if request.method == "POST":
        fields, state, errors = _validate_source_form(request.POST)
        if errors:
            context["errors"] = errors
            context["values"] = request.POST
            return render(request, "datasets/source_form.html", context, status=400)
        audited_update(
            request.user,
            [source],
            fields,
            action="source:edit",
            reason=request.POST.get("reason", ""),
        )
        return redirect("datasets:list")
    return render(request, "datasets/source_form.html", context)
