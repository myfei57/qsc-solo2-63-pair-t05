"""Enumerations for the two state machines the plant tracks."""

from __future__ import annotations

from enum import Enum


class FlightState(str, Enum):
    """Lifecycle of one flight batch."""

    OPEN = "open"
    BOARDING = "boarding"
    CUTOFF = "cutoff"
    CLOSED = "closed"


class BagState(str, Enum):
    """Lifecycle of one piece of baggage."""

    CHECKED_IN = "checked_in"
    INJECTED = "injected"
    SORTED = "sorted"
    RECHECKED = "rechecked"
    LOADED = "loaded"
    DIVERTED = "diverted"

    @property
    def active(self) -> bool:
        """Whether the bag still occupies plant capacity."""

        return self in (
            BagState.CHECKED_IN,
            BagState.INJECTED,
            BagState.SORTED,
            BagState.RECHECKED,
        )

class Verdict(str, Enum):
    """Result of comparing an observed (epoch, sequence) pair with the high-water mark."""

    FRESH = "fresh"
    REPLAYED = "replayed"
    ROLLED_OVER = "rolled_over"


class HandoffStatus(str, Enum):
    """Lifecycle of a cross-terminal handoff sheet.

    ``OPEN`` waits for the receiving terminal's confirmation; ``CONFIRMED`` is
    the only status in which ownership and the carousel record have moved. An
    interrupted handoff is ``INTERRUPTED`` and keeps the pre-handoff owner.
    """

    OPEN = "open"
    CONFIRMED = "confirmed"
    INTERRUPTED = "interrupted"


class OwnershipRule(str, Enum):
    """When custody of a transferring bag changes terminal.

    The plant deliberately supports only ``ON_CONFIRMATION``: ownership moves
    when the receiving terminal confirms, so an interrupted handoff leaves the
    bag with the terminal that started it. The alternative (moving custody when
    the bag enters the transfer area) would assign an interrupted handoff to the
    receiver and is intentionally not offered.
    """

    ON_CONFIRMATION = "on_confirmation"
