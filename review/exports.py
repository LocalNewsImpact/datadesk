"""Exports in the standardized deliverable shape (SCOPE.md §2.4):
UTF-8 BOM CSVs, one logical row per physical line, article UUID as the
join key."""

import csv

from django.http import HttpResponse

# What an export may carry. The UUID key is always first, uninvited.
EXPORT_COLUMNS = {
    "url": lambda a: a.url or "",
    "title": lambda a: a.title or "",
    "author": lambda a: a.author or "",
    "publish_date": lambda a: (
        a.publish_date.date().isoformat() if a.publish_date else ""
    ),
    "publisher": lambda a: (
        a.candidate_link.source.host_norm if a.candidate_link.source else ""
    ),
    "status": lambda a: a.status,
    "wire_check_status": lambda a: a.wire_check_status,
    "primary_label": lambda a: a.primary_label or "",
    "primary_label_confidence": lambda a: (
        "" if a.primary_label_confidence is None else a.primary_label_confidence
    ),
    "content": lambda a: a.content or a.text or "",
}


def _one_line(value):
    """One logical row per physical line: embedded newlines become the
    literal two characters backslash-n, reversibly."""
    if isinstance(value, str):
        return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return value


def csv_response(queryset, columns, filename):
    """Stream the queryset as the standard deliverable CSV."""
    columns = [c for c in columns if c in EXPORT_COLUMNS]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # utf-8-sig: Excel needs the BOM to read UTF-8 as UTF-8.
    response.write("\ufeff")
    writer = csv.writer(response, lineterminator="\n")
    writer.writerow(["article_uuid", *columns])
    for article in queryset.iterator(chunk_size=500):
        writer.writerow(
            [article.id, *(_one_line(EXPORT_COLUMNS[c](article)) for c in columns)]
        )
    return response
