"""SQLAlchemy helpers for enum-backed string columns."""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class FlexibleEnumType(TypeDecorator[enum.Enum]):
    """Store enum values as strings, while reading legacy names or values.

    The app stores the enum's ``.value`` in the database, but older rows may
    still carry the enum member name from the default SQLAlchemy enum behavior.
    This type accepts either form on load and always writes the canonical value.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_cls: type[enum.Enum], *, length: int) -> None:
        self.enum_cls = enum_cls
        super().__init__(length=length)

    def _coerce_enum(self, value: str) -> enum.Enum:
        try:
            return self.enum_cls(value)
        except ValueError:
            try:
                return self.enum_cls[value.upper()]
            except KeyError as exc:
                valid = ", ".join(member.value for member in self.enum_cls)
                raise LookupError(
                    f"{value!r} is not a valid {self.enum_cls.__name__}; "
                    f"expected one of: {valid}"
                ) from exc

    @property
    def python_type(self) -> type[enum.Enum]:
        return self.enum_cls

    def process_bind_param(
        self,
        value: enum.Enum | str | None,
        dialect: Dialect,
    ) -> str | None:
        del dialect
        if value is None:
            return None
        if isinstance(value, str):
            return self._coerce_enum(value).value
        return self._coerce_enum(value.value).value

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> enum.Enum | None:
        del dialect
        if value is None:
            return None
        return self._coerce_enum(value)

    def copy(self, **kw: Any) -> FlexibleEnumType:
        return type(self)(self.enum_cls, length=self.impl.length or 0)
