"""Represent a native extension through readable static declarations."""

class NativeArray:
    """A stub-only class still exposes its declared API."""

    shape: tuple[int, ...]

    def reshape(self, shape: tuple[int, ...], /) -> NativeArray: ...
