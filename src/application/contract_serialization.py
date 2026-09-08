"""Serialize Application DTOs into the language-neutral contract shape."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Mapping


def to_transport_value(value: Any):
    """Convert DTO values to JSON-compatible primitives deterministically."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__fspath__"):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_transport_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): to_transport_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [to_transport_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported contract value: {type(value).__name__}")
