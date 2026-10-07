"""Cover module functions, overloads, unknown returns, and field unions."""

from typing import overload

from .models import Coordinates, Envelope, Payload


@overload
def convert(value: int, /) -> int: ...


@overload
def convert(value: str, /) -> str: ...


def convert(value: int | str, /) -> int | str:
    """Preserve implementation and overload variants independently."""
    return value


def combine(*values: int, scale: float = 1.0, **metadata: str) -> Coordinates:
    """Preserve variadic kinds and literal defaults."""
    return Coordinates(sum(values) * scale, len(metadata))


def lookup(key: str) -> Envelope | Payload | None:
    """Project bounded unions while retaining None in the annotation."""
    return None


def opaque(value):
    """An absent return annotation remains an explicit evidence gap."""
    return value
