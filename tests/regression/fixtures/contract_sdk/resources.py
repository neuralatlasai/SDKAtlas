"""Cover overloads, binding, private aliases, transport, and resource cycles."""

from typing import Literal, overload

from .models import Envelope, Payload, Response


class BaseResource:
    """Provide an inherited public definition."""

    def ping(self, /, *, retries: int = 1) -> bool:
        """Return connectivity evidence."""
        return retries > 0


class Widgets(BaseResource):
    """Expose synchronous and asynchronous resource methods."""

    def __init__(this, token: str = "anonymous") -> None:
        this.token = token

    @overload
    def retrieve(self, identifier: str, *, raw: Literal[False] = False) -> Response:
        """Return a structured widget response."""
        ...

    @overload
    def retrieve(self, identifier: str, *, raw: Literal[True]) -> Payload: ...

    def retrieve(self, identifier: str, *, raw: bool = False) -> Response | Payload:
        """Keep all declaration variants and their response models."""
        return self._get(f"/widgets/{identifier}")

    async def create(self, payload: Payload, /, *, timeout: float = 10.0) -> Envelope:
        """Preserve async calls, positional-only data, and keyword defaults."""
        return await self._post("/widgets", body=payload, timeout=timeout)

    def _implementation(self, value: int = 1) -> int:
        """Remain visible through the public class-local callable alias."""
        return value

    calculate = _implementation

    @staticmethod
    def normalize(self: str, /) -> str:
        """A static parameter named self remains caller supplied."""
        return self.strip()

    @classmethod
    def from_token(klass, token: str) -> "Widgets":
        """Recognize an implicit class receiver regardless of spelling."""
        return klass(token)

    @property
    def nested(self) -> "Widgets":
        """Record a bounded self-referential resource."""
        return self

    def __len__(self) -> int:
        """Preserve a public Python protocol definition."""
        return 0
