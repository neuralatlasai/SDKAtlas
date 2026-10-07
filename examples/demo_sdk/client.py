"""Model resource discovery without credentials, dependencies, or network I/O."""

from functools import cached_property


class Things:
    """Provide a synthetic resource with a visible path-bearing transport call."""

    def __init__(self, client: "Client") -> None:
        """Retain the demonstration client without performing any I/O."""
        self._client = client

    def retrieve(self, thing_id: str) -> dict[str, str]:
        """Return demonstration route evidence for the supplied identifier.

        Args:
            thing_id: Identifier interpolated into the synthetic route.

        Returns:
            A local mapping containing the route; no request is sent.
        """
        return self._get(f"/things/{thing_id}")

    def _get(self, path: str) -> dict[str, str]:
        return {"path": path}


class Client:
    """Expose a property-backed resource for static graph discovery."""

    @cached_property
    def things(self) -> Things:
        """Construct and cache a local resource without external side effects."""
        return Things(self)
