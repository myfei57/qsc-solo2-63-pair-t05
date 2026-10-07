"""Invariants that ownership, handoff sheets and carousel records must share.

The three views of one bag -- who owns it, what its handoff sheet says and
which terminal's carousel holds it -- are derived from the same committed
journal, but they are written by different commands. This module re-checks
them against each other before a handoff transaction commits, and powers the
read-only reconciliation report. A mismatch rejects the transaction rather
than letting two terminals disagree about the same bag.
"""

from __future__ import annotations

from typing import Any

from bagsort.errors import ConflictError
from bagsort.model.enums import HandoffStatus


def active_carousel(state, bag_id: str) -> dict[str, Any] | None:
    for record in reversed(state.carousel_records):
        if record["bag_id"] == bag_id and record.get("active", True):
            return dict(record)
    return None


def latest_sheet(state, bag_id: str) -> dict[str, Any] | None:
    for sheet in sorted(
        state.handoff_sheets.values(),
        key=lambda item: (item["opened_at"], item["id"]),
        reverse=True,
    ):
        if sheet["bag_id"] == bag_id:
            return sheet
    return None


def expected_owner(state, bag_id: str) -> str | None:
    """Owner implied by the sheet timeline; ``None`` before any handoff."""

    sheets = sorted(
        (sheet for sheet in state.handoff_sheets.values() if sheet["bag_id"] == bag_id),
        key=lambda item: (item["opened_at"], item["id"]),
    )
    owner: str | None = None
    for sheet in sheets:
        status = sheet["status"]
        if status == HandoffStatus.OPEN.value:
            owner = sheet["from_terminal"]
        elif status == HandoffStatus.CONFIRMED.value:
            owner = sheet["to_terminal"]
        elif status == HandoffStatus.INTERRUPTED.value:
            owner = sheet["from_terminal"]
    return owner


def check_bag(state, bag_id: str) -> list[str]:
    """Return every disagreement found for one bag."""

    problems: list[str] = []
    registration = state.handoff_registry.get(bag_id)
    if registration is None:
        problems.append(f"bag {bag_id!r} has no shared-namespace registration")
        return problems

    sheet = latest_sheet(state, bag_id)
    if sheet is None:
        return problems

    status = sheet["status"]
    owner = registration["owner_terminal"]
    folded_owner = expected_owner(state, bag_id)
    if folded_owner is not None and owner != folded_owner:
        problems.append(
            f"registry owner {owner} for {bag_id!r} disagrees with the sheet "
            f"timeline, which implies {folded_owner}"
        )
    if status == HandoffStatus.OPEN.value and owner != sheet["from_terminal"]:
        problems.append(
            f"open handoff {sheet['id']} must keep {bag_id!r} with "
            f"{sheet['from_terminal']}, registry says {owner}"
        )
    if status == HandoffStatus.INTERRUPTED.value and owner != sheet["from_terminal"]:
        problems.append(
            f"interrupted handoff {sheet['id']} must roll {bag_id!r} back to "
            f"{sheet['from_terminal']}, registry says {owner}"
        )
    if status == HandoffStatus.CONFIRMED.value and owner != sheet["to_terminal"]:
        problems.append(
            f"confirmed handoff {sheet['id']} must give {bag_id!r} to "
            f"{sheet['to_terminal']}, registry says {owner}"
        )

    record = active_carousel(state, bag_id)
    if record is None:
        problems.append(f"bag {bag_id!r} has no active carousel record")
    elif record["terminal"] != owner:
        problems.append(
            f"bag {bag_id!r} carousel is at {record['terminal']} but owner is {owner}"
        )
    elif record["handoff_id"] != sheet["id"] and status in (
        HandoffStatus.OPEN.value,
        HandoffStatus.INTERRUPTED.value,
        HandoffStatus.CONFIRMED.value,
    ):
        problems.append(
            f"bag {bag_id!r} carousel record cites {record['handoff_id']!r}, "
            f"latest sheet is {sheet['id']!r}"
        )

    view = state.handoff_views.get(owner)
    if view is None:
        problems.append(f"owning terminal {owner} has no committed view of {bag_id!r}")
    else:
        bags = set(view.get("bags", ()))
        if bag_id not in bags:
            problems.append(f"owning terminal {owner} view omits {bag_id!r}")
    other = sheet["to_terminal"] if owner == sheet["from_terminal"] else sheet["from_terminal"]
    other_view = state.handoff_views.get(other)
    if other_view is not None and bag_id in set(other_view.get("bags", ())):
        problems.append(f"non-owning terminal {other} view still claims {bag_id!r}")
    return problems


def assert_bag(state, bag_id: str) -> None:
    problems = check_bag(state, bag_id)
    if problems:
        raise ConflictError("; ".join(problems))


def reconcile(state) -> dict[str, object]:
    """Audit every registered bag and both terminal views."""

    problems: list[str] = []
    bag_ids = sorted(state.handoff_registry)
    for bag_id in bag_ids:
        problems.extend(check_bag(state, bag_id))
    for terminal in ("T1", "T2"):
        view = state.handoff_views.get(terminal)
        if view is None:
            problems.append(f"terminal {terminal} has no committed handoff view")
    return {
        "ok": not problems,
        "bags": len(bag_ids),
        "problems": problems,
    }
