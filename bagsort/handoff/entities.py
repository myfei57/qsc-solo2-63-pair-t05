"""Records of the cross-terminal handoff desk and their JSON codecs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from bagsort.model.enums import HandoffStatus, OwnershipRule
from bagsort.model.validate import require_text


@dataclass(frozen=True)
class Principal:
    """Who calls the handoff desk and why.

    ``terminal`` is the calling terminal, ``operator`` identifies the caller
    (agent id, console account or service name) and ``purpose`` states why the
    call is made. All three are recorded on the audit entry of the call.
    """

    terminal: str
    operator: str
    purpose: str

    def normalized(self) -> "Principal":
        return Principal(
            terminal=self.terminal.strip().upper(),
            operator=self.operator.strip(),
            purpose=self.purpose.strip(),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "terminal": self.terminal,
            "operator": self.operator,
            "purpose": self.purpose,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Principal":
        return cls(
            terminal=str(raw.get("terminal", "")),
            operator=str(raw.get("operator", "")),
            purpose=str(raw.get("purpose", "")),
        )

    def validate(self) -> "Principal":
        principal = self.normalized()
        require_text(principal.terminal, "terminal")
        require_text(principal.operator, "operator")
        require_text(principal.purpose, "purpose")
        return principal


@dataclass(frozen=True)
class HandoffEvent:
    """One append-only step on a handoff sheet."""

    sequence: int
    kind: str
    at: str
    principal: Principal
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "at": self.at,
            "principal": self.principal.as_dict(),
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HandoffEvent":
        detail = raw.get("detail", {})
        return cls(
            sequence=int(raw.get("sequence", 0)),
            kind=str(raw.get("kind", "")),
            at=str(raw.get("at", "")),
            principal=Principal.from_dict(raw.get("principal", {})),
            detail=dict(detail) if isinstance(detail, dict) else {},
        )


@dataclass(frozen=True)
class HandoffSheet:
    """The transfer document two terminals confirm against.

    The opener is the sender-side confirmation; the handoff only becomes
    ``CONFIRMED`` once the receiver records an acknowledgement. Ownership moves
    on that acknowledgement, never before.
    """

    id: str
    bag_id: str
    from_terminal: str
    to_terminal: str
    status: HandoffStatus = HandoffStatus.OPEN
    ownership_rule: OwnershipRule = OwnershipRule.ON_CONFIRMATION
    rule_reason: str = ""
    opened_at: str = ""
    opened_by: Principal | None = None
    confirmed_at: str = ""
    confirmed_by: Principal | None = None
    interrupted_at: str = ""
    interrupted_by: Principal | None = None
    interrupt_reason: str = ""
    events: list[HandoffEvent] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.status is HandoffStatus.CONFIRMED

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bag_id": self.bag_id,
            "from_terminal": self.from_terminal,
            "to_terminal": self.to_terminal,
            "status": self.status.value,
            "ownership_rule": self.ownership_rule.value,
            "rule_reason": self.rule_reason,
            "opened_at": self.opened_at,
            "opened_by": self.opened_by.as_dict() if self.opened_by else None,
            "confirmed_at": self.confirmed_at,
            "confirmed_by": self.confirmed_by.as_dict() if self.confirmed_by else None,
            "interrupted_at": self.interrupted_at,
            "interrupted_by": self.interrupted_by.as_dict() if self.interrupted_by else None,
            "interrupt_reason": self.interrupt_reason,
            "events": [event.as_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HandoffSheet":
        opened_by = raw.get("opened_by")
        confirmed_by = raw.get("confirmed_by")
        interrupted_by = raw.get("interrupted_by")
        return cls(
            id=str(raw["id"]),
            bag_id=str(raw["bag_id"]),
            from_terminal=str(raw["from_terminal"]),
            to_terminal=str(raw["to_terminal"]),
            status=HandoffStatus(raw.get("status", HandoffStatus.OPEN.value)),
            ownership_rule=OwnershipRule(
                raw.get("ownership_rule", OwnershipRule.ON_CONFIRMATION.value)
            ),
            rule_reason=str(raw.get("rule_reason", "")),
            opened_at=str(raw.get("opened_at", "")),
            opened_by=Principal.from_dict(opened_by) if opened_by else None,
            confirmed_at=str(raw.get("confirmed_at", "")),
            confirmed_by=Principal.from_dict(confirmed_by) if confirmed_by else None,
            interrupted_at=str(raw.get("interrupted_at", "")),
            interrupted_by=Principal.from_dict(interrupted_by) if interrupted_by else None,
            interrupt_reason=str(raw.get("interrupt_reason", "")),
            events=[HandoffEvent.from_dict(item) for item in raw.get("events", [])],
        )

    def evolved(self, **changes: Any) -> "HandoffSheet":
        return replace(self, **changes)


@dataclass(frozen=True)
class CarouselRecord:
    """Which terminal's carousel a bag sits on and which handoff put it there."""

    bag_id: str
    terminal: str
    handoff_id: str
    at: str
    active: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "bag_id": self.bag_id,
            "terminal": self.terminal,
            "handoff_id": self.handoff_id,
            "at": self.at,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CarouselRecord":
        return cls(
            bag_id=str(raw["bag_id"]),
            terminal=str(raw["terminal"]),
            handoff_id=str(raw["handoff_id"]),
            at=str(raw.get("at", "")),
            active=bool(raw.get("active", True)),
        )


@dataclass(frozen=True)
class OwnershipDecision:
    """The system's verdict over one bag at one point in time."""

    bag_id: str
    owner_terminal: str
    handoff_id: str
    status: HandoffStatus
    rule: OwnershipRule
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bag_id": self.bag_id,
            "owner_terminal": self.owner_terminal,
            "handoff_id": self.handoff_id,
            "status": self.status.value,
            "rule": self.rule.value,
            "at": self.at,
        }
