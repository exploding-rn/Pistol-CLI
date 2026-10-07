"""Pistol's offline developer API. Functions return data; the CLI presents it."""

from . import doctor, req

__version__ = "1.0.0"

__all__ = [
    "doctor",
    "req",
    "__version__",
]
