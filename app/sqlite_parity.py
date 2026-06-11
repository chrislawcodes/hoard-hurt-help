"""Make SQLite (dev/tests) reject the same writes Postgres (prod) rejects.

SQLite quietly ignores ``VARCHAR(n)`` length limits, so a too-long string is
stored fine in dev and tests but blows up in prod with ``value too long for
type character varying(n)`` -> 500. Postgres always enforces the limit.

This module registers a ``before_flush`` guard that reproduces that check
whenever the session is bound to SQLite. It is a no-op on Postgres (which
already enforces lengths), so importing it is safe everywhere.

Limitation: this only sees ORM inserts/updates. Core ``insert()``/``update()``
statements bypass the flush and are not checked here.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, event, inspect
from sqlalchemy.orm import Session


class StringLengthExceeded(ValueError):
    """A string is longer than its column's declared length (prod would 500)."""


def _check_string_lengths(
    session: Session, _flush_context: Any, _instances: Any
) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for obj in (*session.new, *session.dirty):
        mapper = inspect(obj).mapper
        for attr in mapper.column_attrs:
            column = attr.columns[0]
            col_type = column.type
            # Only plain String columns. FlexibleEnumType is a TypeDecorator,
            # not a String subclass, so enum members are skipped here (they are
            # already validated on bind).
            if not isinstance(col_type, String) or col_type.length is None:
                continue
            value = getattr(obj, attr.key)
            if isinstance(value, str) and len(value) > col_type.length:
                raise StringLengthExceeded(
                    f"{mapper.class_.__name__}.{attr.key} is {len(value)} chars "
                    f"but the column holds at most {col_type.length}; "
                    f"Postgres would reject this write."
                )


def install_sqlite_parity_guards() -> None:
    """Register the SQLite parity guards once, globally, idempotently."""
    if not event.contains(Session, "before_flush", _check_string_lengths):
        event.listen(Session, "before_flush", _check_string_lengths)
