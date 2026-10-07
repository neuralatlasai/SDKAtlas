"""Cover root discovery and assignment-backed resource access."""

from .resources import Widgets


class Client:
    """Provide one explicit inventory root."""

    def __init__(self, *, token: str = "anonymous") -> None:
        self.widgets = Widgets(token)

    @property
    def legacy_widgets(self) -> Widgets:
        """Provide a second graph path to the same definitions."""
        return self.widgets

    def status(self) -> str:
        """Expose a non-transport method directly on the client."""
        return "ready"
