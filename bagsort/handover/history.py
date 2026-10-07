"""Handover history rebuilt from the journal.

The ledger keeps the folded view (the latest sheet per id). This projection
walks the committed ``handover.put`` commands instead, so every version of a
sheet - proposal, receiver confirmation, interruption - stays available for
later review and reuse (dispute review, re-running an interrupted transfer,
rebuilding a terminal's view after restart).
"""

from __future__ import annotations

from bagsort.model.entities import HandoverOrder
from bagsort.model.enums import HandoverStatus
from bagsort.store.store import Store


class HandoverHistory:
    """Every committed handover sheet version, in journal order."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def _versions_from_entries(self) -> list[HandoverOrder]:
        return [
            HandoverOrder.from_dict(entry.payload["order"])
            for entry in self._store.entries()
            if entry.kind == "handover.put"
        ]

    def versions(self, order_id: str = "") -> list[HandoverOrder]:
        versions = self._versions_from_entries()
        if order_id:
            return [order for order in versions if order.id == order_id]
        return versions

    def orders(self) -> list[HandoverOrder]:
        """The latest committed version of each sheet, in first-seen order."""

        latest: dict[str, HandoverOrder] = {}
        for order in self._versions_from_entries():
            latest[order.id] = order
        return list(latest.values())

    def for_bag(self, bag_id: str) -> list[HandoverOrder]:
        """Every sheet that ever concerned one bag, proposal order first."""

        return [order for order in self.orders() if order.bag_id == bag_id]

    def between(self, from_terminal: str, to_terminal: str) -> list[HandoverOrder]:
        sender = from_terminal.strip().upper()
        receiver = to_terminal.strip().upper()
        return [
            order
            for order in self.orders()
            if order.from_terminal == sender and order.to_terminal == receiver
        ]

    def open_orders(self) -> list[HandoverOrder]:
        return [order for order in self.orders() if order.status.open]

    def completed(self) -> list[HandoverOrder]:
        return [order for order in self.orders() if order.status is HandoverStatus.COMPLETED]

    def aborted(self) -> list[HandoverOrder]:
        return [order for order in self.orders() if order.status is HandoverStatus.ABORTED]

    def reuse_confirmation(self, order_id: str) -> dict[str, str]:
        """Pull the confirmed pair back out of a past sheet for a repeat handover."""

        versions = self.versions(order_id)
        if not versions:
            raise KeyError(order_id)
        final = versions[-1]
        if final.status is not HandoverStatus.COMPLETED:
            raise ValueError(f"handover {order_id!r} is {final.status.value}, not reusable")
        return {
            "bag_id": final.bag_id,
            "from_terminal": final.from_terminal,
            "to_terminal": final.to_terminal,
            "proposed_by": final.proposed_by,
            "received_by": final.received_by,
        }
