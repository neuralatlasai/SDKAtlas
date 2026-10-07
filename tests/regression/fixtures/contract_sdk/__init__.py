"""Expose the controlled, import-hostile SDK used for semantic snapshots."""

from .client import Client
from .models import Envelope, Payload
from .resources import Widgets

__all__ = ["Client", "Envelope", "Payload", "Widgets"]

# A static inventory must never execute target initialization, even indirectly.
raise RuntimeError("the golden fixture must never be imported")
