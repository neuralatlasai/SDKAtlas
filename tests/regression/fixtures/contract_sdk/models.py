"""Cover returned models, inherited fields, aliases, and typing qualifiers."""

from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, NamedTuple, NotRequired, TypedDict


class Payload(TypedDict):
    """Preserve required and optional response keys."""

    identifier: str
    label: NotRequired[str]


class Coordinates(NamedTuple):
    x: float
    y: float


@dataclass
class Metadata:
    """Provide inherited instance fields and an excluded class constant."""

    request_id: str
    category: ClassVar[str] = "widget"


@dataclass
class Envelope(Metadata):
    """Represent a declared response without runtime model construction."""

    payload: Payload
    status: Literal["ready", "pending"] = "ready"

    @property
    def complete(self) -> bool:
        """Report a computed response field."""
        return self.status == "ready"


Response = Annotated[Envelope, "response metadata"]
