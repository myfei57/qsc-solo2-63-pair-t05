"""Immutable-by-convention domain records and their JSON codecs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .enums import BagState, FlightState, HandoverStatus


def _clean(raw: Any) -> Any:
    """Normalise nested containers so codecs round-trip deterministically."""

    if isinstance(raw, dict):
        return {str(key): _clean(value) for key, value in raw.items()}
    if isinstance(raw, (list, tuple)):
        return [_clean(item) for item in raw]
    return raw


@dataclass(frozen=True)
class Flight:
    id: str
    code: str
    terminal: str = "T1"
    carousel: str = ""
    chute: str = ""
    state: FlightState = FlightState.OPEN
    batch: int = 0
    schedule_generation: int = 1
    cutoff_at: str = ""
    closed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "terminal": self.terminal,
            "carousel": self.carousel,
            "chute": self.chute,
            "state": self.state.value,
            "batch": self.batch,
            "schedule_generation": self.schedule_generation,
            "cutoff_at": self.cutoff_at,
            "closed_at": self.closed_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Flight":
        return cls(
            id=str(raw["id"]),
            code=str(raw.get("code", "")),
            terminal=str(raw.get("terminal", "T1")),
            carousel=str(raw.get("carousel", "")),
            chute=str(raw.get("chute", "")),
            state=FlightState(raw.get("state", FlightState.OPEN.value)),
            batch=int(raw.get("batch", 0)),
            schedule_generation=int(raw.get("schedule_generation", 1)),
            cutoff_at=str(raw.get("cutoff_at", "")),
            closed_at=str(raw.get("closed_at", "")),
        )

    def evolved(self, **changes: Any) -> "Flight":
        return replace(self, **changes)


@dataclass(frozen=True)
class Bag:
    id: str
    barcode: str
    sequence: int
    epoch: int = 0
    flight_id: str = ""
    chute_id: str = ""
    state: BagState = BagState.CHECKED_IN

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "barcode": self.barcode,
            "sequence": self.sequence,
            "epoch": self.epoch,
            "flight_id": self.flight_id,
            "chute_id": self.chute_id,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Bag":
        return cls(
            id=str(raw["id"]),
            barcode=str(raw.get("barcode", "")),
            sequence=int(raw.get("sequence", 0)),
            epoch=int(raw.get("epoch", 0)),
            flight_id=str(raw.get("flight_id", "")),
            chute_id=str(raw.get("chute_id", "")),
            state=BagState(raw.get("state", BagState.CHECKED_IN.value)),
        )

    def evolved(self, **changes: Any) -> "Bag":
        return replace(self, **changes)


@dataclass(frozen=True)
class Reading:
    barcode: str
    bag_id: str
    sequence: int
    epoch: int
    committed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "barcode": self.barcode,
            "bag_id": self.bag_id,
            "sequence": self.sequence,
            "epoch": self.epoch,
            "committed": self.committed,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Reading":
        return cls(
            barcode=str(raw.get("barcode", "")),
            bag_id=str(raw.get("bag_id", "")),
            sequence=int(raw.get("sequence", 0)),
            epoch=int(raw.get("epoch", 0)),
            committed=bool(raw.get("committed", False)),
        )


@dataclass(frozen=True)
class ChuteAssignment:
    flight_id: str
    chute_id: str
    generation: int = 1
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "flight_id": self.flight_id,
            "chute_id": self.chute_id,
            "generation": self.generation,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ChuteAssignment":
        return cls(
            flight_id=str(raw["flight_id"]),
            chute_id=str(raw.get("chute_id", "")),
            generation=int(raw.get("generation", 1)),
            updated_at=str(raw.get("updated_at", "")),
        )


@dataclass(frozen=True)
class SortRecord:
    bag_id: str
    flight_id: str
    chute_id: str
    sequence: int
    epoch: int
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bag_id": self.bag_id,
            "flight_id": self.flight_id,
            "chute_id": self.chute_id,
            "sequence": self.sequence,
            "epoch": self.epoch,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SortRecord":
        return cls(
            bag_id=str(raw["bag_id"]),
            flight_id=str(raw.get("flight_id", "")),
            chute_id=str(raw.get("chute_id", "")),
            sequence=int(raw.get("sequence", 0)),
            epoch=int(raw.get("epoch", 0)),
            at=str(raw.get("at", "")),
        )


@dataclass(frozen=True)
class TransferCommand:
    id: str
    flight_id: str
    carousel: str
    batch: int
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "flight_id": self.flight_id,
            "carousel": self.carousel,
            "batch": self.batch,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TransferCommand":
        return cls(
            id=str(raw["id"]),
            flight_id=str(raw.get("flight_id", "")),
            carousel=str(raw.get("carousel", "")),
            batch=int(raw.get("batch", 0)),
            created_at=str(raw.get("created_at", "")),
        )


@dataclass(frozen=True)
class LoadRecord:
    bag_id: str
    carousel: str
    batch: int
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bag_id": self.bag_id,
            "carousel": self.carousel,
            "batch": self.batch,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LoadRecord":
        return cls(
            bag_id=str(raw["bag_id"]),
            carousel=str(raw.get("carousel", "")),
            batch=int(raw.get("batch", 0)),
            at=str(raw.get("at", "")),
        )


@dataclass(frozen=True)
class Sequence:
    epoch: int = 0
    next_value: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"epoch": self.epoch, "next": self.next_value}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Sequence":
        return cls(epoch=int(raw.get("epoch", 0)), next_value=int(raw.get("next", 0)))


@dataclass(frozen=True)
class AuditEntry:
    sequence: int
    kind: str
    subject: str
    detail: dict[str, Any] = field(default_factory=dict)
    prev_digest: str = ""
    digest: str = ""
    at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "subject": self.subject,
            "detail": _clean(self.detail),
            "prev_digest": self.prev_digest,
            "digest": self.digest,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AuditEntry":
        return cls(
            sequence=int(raw.get("sequence", 0)),
            kind=str(raw.get("kind", "")),
            subject=str(raw.get("subject", "")),
            detail=_clean(raw.get("detail", {})),
            prev_digest=str(raw.get("prev_digest", "")),
            digest=str(raw.get("digest", "")),
            at=str(raw.get("at", "")),
        )


@dataclass(frozen=True)
class CustodyRecord:
    """Which terminal is responsible for one bag right now.

    ``epoch`` starts at one when the bag is first claimed and is bumped on
    every completed handover and every rollback, so two terminals comparing
    their views can tell the responsible party apart from a stale one.
    """

    bag_id: str
    terminal: str
    epoch: int = 1
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "bag_id": self.bag_id,
            "terminal": self.terminal,
            "epoch": self.epoch,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CustodyRecord":
        return cls(
            bag_id=str(raw["bag_id"]),
            terminal=str(raw.get("terminal", "")),
            epoch=int(raw.get("epoch", 1)),
            updated_at=str(raw.get("updated_at", "")),
        )

    def evolved(self, **changes: Any) -> "CustodyRecord":
        return replace(self, **changes)


@dataclass(frozen=True)
class CarouselMove:
    """One carousel record tied to a handover step.

    The responsible terminal is denormalised on every move so a carousel
    record can be compared directly against the custody record of the same
    bag: the latest active move must name the responsible terminal.
    """

    id: str
    bag_id: str
    order_id: str
    carousel: str
    terminal: str
    phase: str
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bag_id": self.bag_id,
            "order_id": self.order_id,
            "carousel": self.carousel,
            "terminal": self.terminal,
            "phase": self.phase,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CarouselMove":
        return cls(
            id=str(raw["id"]),
            bag_id=str(raw["bag_id"]),
            order_id=str(raw.get("order_id", "")),
            carousel=str(raw.get("carousel", "")),
            terminal=str(raw.get("terminal", "")),
            phase=str(raw.get("phase", "")),
            at=str(raw.get("at", "")),
        )


@dataclass(frozen=True)
class HandoverOrder:
    """The inter-terminal handover sheet for one bag.

    Both sides confirm explicitly: ``proposed_by`` is the sender letting the
    bag into the transfer area, ``received_by`` is the receiver accepting it.
    A handover only becomes :data:`HandoverStatus.COMPLETED` once both fields
    are present; a proposal with no receiver confirmation stays open and must
    never be treated as success.

    ``ownership_point`` records the policy decision written onto every sheet:
    responsibility moves on receiver confirmation, not on entering the
    transfer area. ``ownership_reason`` explains why, so a dispute over an
    interrupted handover reads the rule off the sheet instead of someone's
    memory of a phone call.
    """

    id: str
    bag_id: str
    from_terminal: str
    to_terminal: str
    status: HandoverStatus
    ownership_point: str
    ownership_reason: str
    proposed_by: str = ""
    proposed_by_terminal: str = ""
    purpose: str = ""
    received_by: str = ""
    received_by_terminal: str = ""
    aborted_by: str = ""
    aborted_by_terminal: str = ""
    abort_reason: str = ""
    proposed_at: str = ""
    received_at: str = ""
    aborted_at: str = ""
    custody_epoch: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bag_id": self.bag_id,
            "from_terminal": self.from_terminal,
            "to_terminal": self.to_terminal,
            "status": self.status.value,
            "ownership_point": self.ownership_point,
            "ownership_reason": self.ownership_reason,
            "proposed_by": self.proposed_by,
            "proposed_by_terminal": self.proposed_by_terminal,
            "purpose": self.purpose,
            "received_by": self.received_by,
            "received_by_terminal": self.received_by_terminal,
            "aborted_by": self.aborted_by,
            "aborted_by_terminal": self.aborted_by_terminal,
            "abort_reason": self.abort_reason,
            "proposed_at": self.proposed_at,
            "received_at": self.received_at,
            "aborted_at": self.aborted_at,
            "custody_epoch": self.custody_epoch,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HandoverOrder":
        return cls(
            id=str(raw["id"]),
            bag_id=str(raw["bag_id"]),
            from_terminal=str(raw.get("from_terminal", "")),
            to_terminal=str(raw.get("to_terminal", "")),
            status=HandoverStatus(raw.get("status", HandoverStatus.PROPOSED.value)),
            ownership_point=str(raw.get("ownership_point", "")),
            ownership_reason=str(raw.get("ownership_reason", "")),
            proposed_by=str(raw.get("proposed_by", "")),
            proposed_by_terminal=str(raw.get("proposed_by_terminal", "")),
            purpose=str(raw.get("purpose", "")),
            received_by=str(raw.get("received_by", "")),
            received_by_terminal=str(raw.get("received_by_terminal", "")),
            aborted_by=str(raw.get("aborted_by", "")),
            aborted_by_terminal=str(raw.get("aborted_by_terminal", "")),
            abort_reason=str(raw.get("abort_reason", "")),
            proposed_at=str(raw.get("proposed_at", "")),
            received_at=str(raw.get("received_at", "")),
            aborted_at=str(raw.get("aborted_at", "")),
            custody_epoch=int(raw.get("custody_epoch", 1)),
        )

    def evolved(self, **changes: Any) -> "HandoverOrder":
        return replace(self, **changes)


@dataclass(frozen=True)
class Actor:
    """The caller identity every handover command is audited under."""

    name: str
    terminal: str
    purpose: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "terminal": self.terminal, "purpose": self.purpose}
