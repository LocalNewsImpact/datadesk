"""The form-driven builder's server side (SCOPE.md §2.6 v2).

The builder produces a config dict the chart runtime reads; the server
whitelists its keys and validates the kind, and everything downstream —
snapshots, publishing, the pinned embed — is the v1 machinery unchanged.
"""

import csv
import io

CHART_KINDS = (
    "table",
    "bar",
    "line",
    "area",
    "scatter",
    "donut",
    "chord",
    "arc",
    "choropleth",
    "points",
)

_STRING_KEYS = (
    "x",
    "y",
    "series",
    "size",
    "from",
    "to",
    "value",
    "label",
    "sort",
    "xlabel",
    "ylabel",
    "subtitle",
    "source",
    "geo_level",
    "geo_join",
    "geo_value",
    "geo_palette",
    "lat",
    "lon",
)
_BOOL_KEYS = ("horizontal", "stacked", "geo_fit")

MAX_ROWS = 20_000


class BuilderError(ValueError):
    """User-facing builder problem."""


def config_from_form(post):
    """Assemble and validate a chart config from form fields."""
    kind = post.get("kind", "")
    if kind not in CHART_KINDS:
        raise BuilderError(f"Unknown chart kind: {kind or '(none)'}")
    config = {"kind": kind}
    for key in _STRING_KEYS:
        value = post.get(key, "").strip()
        if value:
            config[key] = value
    for key in _BOOL_KEYS:
        if post.get(key) == "1":
            config[key] = True
    return config


def parse_upload(uploaded_file):
    """Parse an uploaded CSV into typed rows for an inline snapshot.

    Columns whose every non-empty value parses as a number become
    numbers, so the runtime and BigQuery/GCS sources agree on types.
    """
    try:
        text = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BuilderError("Not UTF-8. Save the file as UTF-8 CSV.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BuilderError("No header row found.")
    rows = list(reader)
    if not rows:
        raise BuilderError("No data rows found.")
    if len(rows) > MAX_ROWS:
        raise BuilderError(f"{len(rows)} rows; the limit is {MAX_ROWS}.")

    numeric = set(reader.fieldnames)
    for row in rows:
        for column in list(numeric):
            value = (row.get(column) or "").strip()
            if value:
                try:
                    float(value)
                except ValueError:
                    numeric.discard(column)
    typed = []
    for row in rows:
        out = {}
        for column in reader.fieldnames:
            value = (row.get(column) or "").strip()
            if column in numeric and value:
                number = float(value)
                out[column] = int(number) if number.is_integer() else number
            else:
                out[column] = value or None
        typed.append(out)
    return typed
