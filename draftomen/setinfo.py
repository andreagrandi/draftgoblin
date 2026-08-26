"""Set display helpers for player-facing output.
Keep set names centralized so terse Arena set codes get readable labels.
"""

from __future__ import annotations

SET_DISPLAY_NAMES = {
    "MSH": "Marvel Super Heroes",
}


def format_set_label(*, set_code: str | None) -> str:
    """Return a readable label for an MTG set code.
    Unknown codes fall back to the uppercase code so output stays deterministic.
    """

    if set_code is None or set_code == "":
        return "unknown"

    normalized = set_code.upper()
    name = SET_DISPLAY_NAMES.get(normalized)
    if name is None:
        return normalized

    return f"{normalized} — {name}"

