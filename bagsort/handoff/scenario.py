"""Deterministic cross-terminal handoff scenario.

Drives one completed transfer and one interrupted transfer so a command line
operator can see the ownership rule, the two-party confirmation and the
rollback behave the same way on every run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from bagsort.app import Plant


def run_handoff_demo(plant: "Plant") -> dict[str, object]:
    """Exercise registration, confirmation, interruption and a return."""

    sender = plant.principal("T1", "dispatcher-li", "T1 outbound transfer to T2")
    receiver = plant.principal("T2", "receiver-chen", "T2 inbound acceptance")

    plant.register_handoff_bag("T1-HO01", sender)
    completed = plant.open_handoff("T1-HO01", sender, note="missorted onto the T2 line")
    plant.clock.advance(120)
    plant.confirm_handoff(completed.id, receiver)

    plant.register_handoff_bag("T1-HO02", sender)
    broken = plant.open_handoff("T1-HO02", sender, note="stray bag headed to T2")
    plant.clock.advance(900)
    plant.interrupt_handoff(broken.id, sender, "timeout")

    returning = plant.handoff.reopen_return(completed.id, receiver)
    plant.clock.advance(60)
    plant.confirm_handoff(returning.id, sender)

    reconciliation = plant.handoff.reconcile()
    return {
        "rule": "on_confirmation",
        "completed": completed.id,
        "interrupted": broken.id,
        "returned": returning.id,
        "owner_completed": plant.handoff.verdict("T1-HO01").owner_terminal,
        "owner_interrupted": plant.handoff.verdict("T1-HO02").owner_terminal,
        "terminal_views": {
            "T1": plant.handoff.terminal_view("T1").get("bags", []),
            "T2": plant.handoff.terminal_view("T2").get("bags", []),
        },
        "reconciled": reconciliation["ok"],
        "audit_intact": plant.verify(),
    }
