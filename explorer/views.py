"""The data explorer: grid views over the crawler corpus (SCOPE.md §2.2).

Read-only throughout — Phase 1 has no write path, and the connection
role couldn't write anyway. Filters are the ones that mattered in the
March reconciliation: dataset, status, wire state, publisher, date
range, label confidence.

Filter vocabularies (statuses, wire states, labels) are read from the
data, cached briefly — the grid never invents statuses the pipeline
doesn't know (SCOPE.md §2.2).
"""

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import F
from django.shortcuts import render

from accounts.decorators import role_required
from explorer.models import Article, Dataset, DatasetSource

PAGE_SIZE = 50
_VOCAB_CACHE_KEY = "explorer.article_filter_vocab"
_VOCAB_CACHE_SECONDS = 300


def _filter_vocab():
    """Distinct filter values as the data defines them, or None offline."""

    def fetch():
        return {
            "datasets": list(Dataset.objects.order_by("label").values("slug", "label")),
            "statuses": sorted(
                Article.objects.values_list("status", flat=True).distinct()
            ),
            "wire_statuses": sorted(
                Article.objects.values_list("wire_check_status", flat=True).distinct()
            ),
            "labels": sorted(
                Article.objects.filter(primary_label__isnull=False)
                .values_list("primary_label", flat=True)
                .distinct()
            ),
        }

    try:
        return cache.get_or_set(_VOCAB_CACHE_KEY, fetch, _VOCAB_CACHE_SECONDS)
    except DatabaseError:
        return None


def _filtered_articles(params):
    """Apply the grid filters from the query string to the corpus."""
    qs = Article.objects.select_related("candidate_link__source")

    if slug := params.get("dataset"):
        # The canonical membership path (the crawler's own enrichment
        # queries): article → candidate_link.source_id → dataset_sources.
        member_sources = DatasetSource.objects.filter(dataset__slug=slug).values(
            "source_id"
        )
        qs = qs.filter(candidate_link__source_id__in=member_sources)
    if status := params.get("status"):
        qs = qs.filter(status=status)
    if wire := params.get("wire"):
        qs = qs.filter(wire_check_status=wire)
    if publisher := params.get("publisher"):
        # Text search over host and canonical name; hundreds of sources
        # make a dropdown unwieldy and a search box is how March worked.
        qs = qs.filter(candidate_link__source__host_norm__icontains=publisher.lower())
    if label := params.get("label"):
        qs = qs.filter(primary_label=label)
    if date_from := params.get("from"):
        qs = qs.filter(publish_date__date__gte=date_from)
    if date_to := params.get("to"):
        qs = qs.filter(publish_date__date__lte=date_to)
    if conf_min := params.get("conf_min"):
        qs = qs.filter(primary_label_confidence__gte=conf_min)
    if conf_max := params.get("conf_max"):
        qs = qs.filter(primary_label_confidence__lte=conf_max)
    if q := params.get("q"):
        qs = qs.filter(title__icontains=q)

    return qs.order_by(F("publish_date").desc(nulls_last=True), "-created_at")


@role_required
def articles(request):
    vocab = _filter_vocab()
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "crawler_connected": vocab is not None,
        "vocab": vocab,
        "params": params,
    }
    if vocab is not None:
        try:
            page_number = int(request.GET.get("page", "1"))
        except ValueError:
            page_number = 1
        paginator = Paginator(_filtered_articles(request.GET), PAGE_SIZE)
        context["page"] = paginator.get_page(page_number)

    # htmx swaps just the results region; a plain GET renders the page.
    template = (
        "explorer/_articles_results.html"
        if request.headers.get("HX-Request")
        else "explorer/articles.html"
    )
    return render(request, template, context)
