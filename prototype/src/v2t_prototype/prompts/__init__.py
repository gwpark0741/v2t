from __future__ import annotations

from importlib.resources import files


def load_prompt(filename: str) -> str:
    return files(__package__).joinpath(filename).read_text(encoding="utf-8").strip()
