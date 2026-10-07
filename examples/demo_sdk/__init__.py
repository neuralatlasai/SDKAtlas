"""Expose a synthetic client for an offline static-analysis demonstration."""

from .client import Client
from .helpers import DEFAULT_LIMIT, normalize_identifier

__all__ = ["DEFAULT_LIMIT", "Client", "normalize_identifier"]
