"""Append-only audit log (SCOPE.md §2.1).

Every mutating action records actor, timestamp, target rows, and
before/after values. The log is append-only and visible read-only in the
Django admin.
"""
