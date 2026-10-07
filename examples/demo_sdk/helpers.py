"""Expose ordinary Python module members alongside client/resource surfaces."""

DEFAULT_LIMIT = 20


def normalize_identifier(value: str) -> str:
    """Return an identifier with surrounding whitespace removed.

    Args:
        value: Any string, including an empty or whitespace-only string.

    Returns:
        The stripped string. Empty input remains empty; no input is mutated.
    """
    return value.strip()
