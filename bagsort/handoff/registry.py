"""Shared ownership registry for the two terminals.

Terminal prefixes are one shared namespace: a bag id is ``TERMINAL-barcode``
and the same id may only be registered once. Registration is also the moment
the first carousel record is born, on the registering terminal's carousel.
"""

from __future__ import annotations

import re
from typing import Any

from bagsort.errors import ConflictError, NotFoundError, ValidationError

TERMINALS: tuple[str, ...] = ("T1", "T2")

_BAG_ID = re.compile(r"^(T1|T2)-(\S+)$")


def split_bag_id(bag_id: str) -> tuple[str, str]:
    """Return ``(terminal, suffix)`` for a shared-namespace bag id."""

    match = _BAG_ID.match((bag_id or "").strip())
    if match is None:
        raise ValidationError(
            f"bag id {bag_id!r} must be prefixed with a shared terminal (T1-/T2-)"
        )
    return match.group(1), match.group(2)


def other_terminal(terminal: str) -> str:
    terminal = (terminal or "").strip().upper()
    if terminal not in TERMINALS:
        raise ValidationError(f"unknown terminal {terminal!r}; expected T1 or T2")
    return TERMINALS[1] if terminal == TERMINALS[0] else TERMINALS[0]


class OwnershipRegistry:
    """Owns the committed mapping of shared bag id to responsible terminal."""

    def __init__(self, state) -> None:
        self._state = state

    def is_registered(self, bag_id: str) -> bool:
        return bag_id in self._state.handoff_registry

    def require_registered(self, bag_id: str) -> dict[str, Any]:
        entry = self._state.handoff_registry.get(bag_id)
        if entry is None:
            raise NotFoundError(f"bag {bag_id!r} is not registered in the shared namespace")
        return entry

    def owner_of(self, bag_id: str) -> str:
        return self.require_registered(bag_id)["owner_terminal"]

    def register(self, session, bag_id: str, terminal: str, at: str) -> dict[str, Any]:
        """Insert the ownership row, refusing a duplicate on the spot."""

        prefix, _suffix = split_bag_id(bag_id)
        actor = (terminal or "").strip().upper()
        if actor != prefix:
            raise ValidationError(
                f"caller terminal {actor!r} cannot register {bag_id!r}; prefix belongs to {prefix}"
            )
        if self.is_registered(bag_id):
            existing = self._state.handoff_registry[bag_id]
            raise ConflictError(
                f"bag {bag_id!r} is already registered to "
                f"{existing['owner_terminal']}; shared prefixes cannot be registered twice"
            )
        entry = {
            "bag_id": bag_id,
            "owner_terminal": terminal,
            "registered_at": at,
            "version": 1,
        }
        session.state.handoff_registry[bag_id] = entry
        session.record("handoff.register", {"entry": dict(entry)}, at)
        return entry

    def transfer(self, session, bag_id: str, terminal: str, at: str) -> dict[str, Any]:
        """Move ownership to ``terminal`` and bump the optimistic version."""

        entry = self.require_registered(bag_id)
        updated = dict(entry)
        updated["owner_terminal"] = terminal
        updated["version"] = int(entry.get("version", 1)) + 1
        updated["changed_at"] = at
        session.state.handoff_registry[bag_id] = updated
        session.record("handoff.register", {"entry": dict(updated)}, at)
        return updated

    def rows(self) -> list[dict[str, Any]]:
        return [dict(self._state.handoff_registry[key]) for key in sorted(self._state.handoff_registry)]

    def owned_by(self, terminal: str) -> list[str]:
        return sorted(
            bag_id
            for bag_id, entry in self._state.handoff_registry.items()
            if entry["owner_terminal"] == terminal
        )
