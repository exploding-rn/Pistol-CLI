"""Presentation helpers; business modules return plain structured data."""
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path


def json_output(value):
    def convert(item):
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"Cannot serialize {type(item).__name__}")
    print(json.dumps(value, default=convert, ensure_ascii=False, indent=2))


def pairs(value: dict):
    for key, item in value.items():
        if isinstance(item, list):
            item = ", ".join(str(part) for part in item) or "none"
        elif item is None:
            item = "not detected"
        print(f"{key.replace('_', ' ').capitalize():18} {item}")


def health(report):
    print(f"Project: {report.project}")
    marks = {"ok": "✓", "error": "✗", "warning": "!", "info": "·"}
    for check in report.checks:
        print(f"{marks[check.level]} {check.message}")
        if check.detail:
            print(f"  {check.detail}")

