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


class HandoverStatus(str, Enum):
    """Lifecycle of one inter-terminal baggage handover.

    A handover is *proposed* as soon as the sending terminal lets a bag enter
    the shared transfer area, but responsibility does not move at that point.
    It only moves when the receiving terminal confirms acceptance, which is
    also the only way the handover reaches *completed*. An interrupted
    handover is *aborted* and the bag returns to the sender's responsibility,
    exactly as before the proposal.
    """

    PROPOSED = "proposed"
    COMPLETED = "completed"
    ABORTED = "aborted"

    @property
    def open(self) -> bool:
        """Whether the handover still waits for the receiving terminal."""

        return self is HandoverStatus.PROPOSED
