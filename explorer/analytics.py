"""Read-only BigQuery access to the analytics mirror (SCOPE.md §1).

Queries run as the runtime service account and are billed to this project
(roles/bigquery.jobUser, infra/bootstrap.sh); the data lives in the crawler
project's dataset, readable through a dataset-level dataViewer grant. Two
standing rules apply (SCOPE.md §1): BigQuery is derived — never a source
for corrections — and Datadesk never writes to it.

The client is created lazily so importing this module costs nothing in
development, where no credentials exist.
"""

import os

ANALYTICS_DATASET = os.environ.get(
    "ANALYTICS_DATASET", "mizzou-news-crawler.mizzou_analytics"
)


def get_client():
    """A BigQuery client on Application Default Credentials.

    On Cloud Run that is datadesk-run@; locally, `gcloud auth
    application-default login`. Raises DefaultCredentialsError when
    neither exists — callers treat that as "not connected".
    """
    from google.cloud import bigquery

    return bigquery.Client()


def query_rows(sql: str, **params) -> list[dict]:
    """Run a SELECT and return plain dicts.

    Query parameters are passed as named ScalarQueryParameters — string
    interpolation into analytics SQL is as wrong here as anywhere.
    """
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(name, _bq_type(value), value)
            for name, value in params.items()
        ]
    )
    return [dict(row) for row in get_client().query(sql, job_config=job_config)]


def _bq_type(value) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    return "STRING"
