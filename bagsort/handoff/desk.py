"""The cross-terminal handoff desk.

Operations, in order:

* :meth:`register_bag` claims a shared-namespace bag id for one terminal and
  writes its first carousel record. A duplicate id is rejected immediately.
* :meth:`open_handoff` is the sender-side hand: the opener confirms the sheet
  at creation, ownership stays put and the bag is recorded in the transfer
  area while the sheet is ``OPEN``.
* :meth:`confirm_handoff` is the receiver-side hand. Only this action moves
  ownership and the carousel record to the other terminal.
* :meth:`interrupt_handoff` rolls an unfinished sheet back to the opener's
  ownership and carousel.

Ownership, sheet status and carousel record are committed in one journal
transaction together with the identity-bearing audit entry, so an interrupted
or failed transaction cannot leave the two terminals disagreeing.
"""

from __future__ import annotations

from typing import Any

from bagsort.audit.chain import AuditChain
from bagsort.clock import Clock
from bagsort.errors import ConflictError, NotFoundError, OrderingError, ValidationError
from bagsort.ids import IdFactory
from bagsort.model.enums import HandoffStatus, OwnershipRule
from bagsort.store.store import Store
from . import OWNERSHIP_REASON
from .consistency import assert_bag, reconcile
from .entities import CarouselRecord, HandoffEvent, HandoffSheet, OwnershipDecision, Principal
from .registry import TERMINALS, OwnershipRegistry, other_terminal, split_bag_id

STAGE_REGISTER = "handoff_register"
STAGE_OPEN = "handoff_open"
STAGE_CONFIRM = "handoff_confirm"
STAGE_INTERRUPT = "handoff_interrupt"

INTERRUPT_REASONS_SENDER = {"cancelled", "timeout"}
INTERRUPT_REASONS_RECEIVER = {"rejected"}


class HandoffDesk:
    """System-owned verdicts over baggage moving between the two terminals."""

    def __init__(self, store: Store, clock: Clock, audit: AuditChain) -> None:
        self._store = store
        self._clock = clock
        self._audit = audit
        self._ids = IdFactory("HO")
        self.crash_on_confirm = False

    # -- read side ---------------------------------------------------------

    def registry(self) -> OwnershipRegistry:
        return OwnershipRegistry(self._store.state())

    def sheets(self) -> list[HandoffSheet]:
        return [
            HandoffSheet.from_dict(payload)
            for payload in self._store.state().handoff_sheets.values()
        ]

    def get(self, handoff_id: str) -> HandoffSheet:
        payload = self._store.state().handoff_sheets.get(handoff_id)
        if payload is None:
            raise NotFoundError(f"handoff {handoff_id!r} does not exist")
        return HandoffSheet.from_dict(payload)

    def history(self, bag_id: str) -> list[HandoffSheet]:
        """Every sheet a bag has been through, oldest first (reused later)."""

        sheets = [sheet for sheet in self.sheets() if sheet.bag_id == bag_id]
        return sorted(sheets, key=lambda sheet: (sheet.opened_at, sheet.id))

    def carousel_records(self, bag_id: str = "") -> list[CarouselRecord]:
        records = [
            CarouselRecord.from_dict(payload)
            for payload in self._store.state().carousel_records
        ]
        if bag_id:
            records = [record for record in records if record.bag_id == bag_id]
        return records

    def active_carousel(self, bag_id: str) -> CarouselRecord | None:
        for record in reversed(self.carousel_records(bag_id)):
            if record.active:
                return record
        return None

    def verdict(self, bag_id: str, at: str = "") -> OwnershipDecision:
        """The system's ownership judgement for one bag right now."""

        registry = self.registry()
        owner = registry.owner_of(bag_id)
        sheets = self.history(bag_id)
        if sheets:
            sheet = sheets[-1]
            status = sheet.status
            handoff_id = sheet.id
        else:
            status = HandoffStatus.CONFIRMED
            handoff_id = ""
        return OwnershipDecision(
            bag_id=bag_id,
            owner_terminal=owner,
            handoff_id=handoff_id,
            status=status,
            rule=OwnershipRule.ON_CONFIRMATION,
            at=at or self._clock.now_iso(),
        )

    def terminal_view(self, terminal: str) -> dict[str, Any]:
        terminal = self._require_terminal(terminal)
        return dict(self._store.state().handoff_views.get(terminal, {}))

    def reconcile(self) -> dict[str, object]:
        return reconcile(self._store.state())

    # -- registration ------------------------------------------------------

    def register_bag(self, bag_id: str, principal: Principal) -> dict[str, Any]:
        principal = self._principal(principal)
        prefix, _suffix = split_bag_id(bag_id)
        at = self._clock.now_iso()
        with self._store.session() as session:
            registry = OwnershipRegistry(session.state)
            entry = registry.register(session, bag_id, principal.terminal, at)
            record = CarouselRecord(
                bag_id=bag_id, terminal=prefix, handoff_id="", at=at, active=True
            )
            session.state.carousel_records.append(record.as_dict())
            session.record("carousel.record", {"record": record.as_dict()}, at)
            self._sync_views(session)
            self._audit_entry(
                session,
                "handoff_register",
                STAGE_REGISTER,
                bag_id,
                principal,
                {"owner_terminal": prefix, "bag_id": bag_id},
                at,
            )
            assert_bag(session.state, bag_id)
        return entry

    # -- handoff lifecycle -------------------------------------------------

    def open_handoff(
        self,
        bag_id: str,
        principal: Principal,
        to_terminal: str = "",
        note: str = "",
    ) -> HandoffSheet:
        principal = self._principal(principal)
        at = self._clock.now_iso()
        with self._store.session() as session:
            sheet = self._build_open(session, bag_id, principal, to_terminal, note, at)
            self._place_carousel(session, bag_id, sheet.from_terminal, sheet.id, at)
            self._store_sheet(session, sheet, at)
            self._sync_views(session)
            self._audit_entry(
                session,
                "handoff_opened",
                STAGE_OPEN,
                bag_id,
                principal,
                {"handoff_id": sheet.id, "to_terminal": sheet.to_terminal},
                at,
            )
            assert_bag(session.state, bag_id)
        return sheet

    def confirm_handoff(self, handoff_id: str, principal: Principal) -> HandoffSheet:
        principal = self._principal(principal)
        at = self._clock.now_iso()
        with self._store.session() as session:
            sheet = self._load(session, handoff_id)
            if sheet.status is not HandoffStatus.OPEN:
                raise OrderingError(
                    f"handoff {handoff_id} is {sheet.status.value}, only open handoffs confirm"
                )
            if principal.terminal != sheet.to_terminal:
                raise ConflictError(
                    f"only receiving terminal {sheet.to_terminal} may confirm {handoff_id}"
                )
            event = self._event(sheet, "receiver_confirmed", principal, at)
            sheet = sheet.evolved(
                status=HandoffStatus.CONFIRMED,
                confirmed_at=at,
                confirmed_by=principal,
                events=sheet.events + [event],
            )
            OwnershipRegistry(session.state).transfer(
                session, sheet.bag_id, sheet.to_terminal, at
            )
            self._place_carousel(session, sheet.bag_id, sheet.to_terminal, sheet.id, at)
            self._store_sheet(session, sheet, at)
            self._sync_views(session)
            self._audit_entry(
                session,
                "handoff_confirmed",
                STAGE_CONFIRM,
                sheet.bag_id,
                principal,
                {
                    "handoff_id": sheet.id,
                    "from_terminal": sheet.from_terminal,
                    "to_terminal": sheet.to_terminal,
                },
                at,
            )
            assert_bag(session.state, sheet.bag_id)
            if self.crash_on_confirm:
                self.crash_on_confirm = False
                raise RuntimeError("injected failure before commit")
        return sheet

    def interrupt_handoff(
        self,
        handoff_id: str,
        principal: Principal,
        reason: str,
    ) -> HandoffSheet:
        principal = self._principal(principal)
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("interrupt reason must not be empty")
        at = self._clock.now_iso()
        with self._store.session() as session:
            sheet = self._load(session, handoff_id)
            if sheet.status is not HandoffStatus.OPEN:
                raise OrderingError(
                    f"handoff {handoff_id} is {sheet.status.value}; only open handoffs interrupt"
                )
            self._authorize_interrupt(sheet, principal, reason)
            event = self._event(
                sheet, "interrupted", principal, at, {"reason": reason}
            )
            sheet = sheet.evolved(
                status=HandoffStatus.INTERRUPTED,
                interrupted_at=at,
                interrupted_by=principal,
                interrupt_reason=reason,
                events=sheet.events + [event],
            )
            # Roll ownership back to the opener and put the bag back on its
            # carousel in the same transaction.
            OwnershipRegistry(session.state).transfer(
                session, sheet.bag_id, sheet.from_terminal, at
            )
            self._place_carousel(session, sheet.bag_id, sheet.from_terminal, sheet.id, at)
            self._store_sheet(session, sheet, at)
            self._sync_views(session)
            self._audit_entry(
                session,
                "handoff_interrupted",
                STAGE_INTERRUPT,
                sheet.bag_id,
                principal,
                {
                    "handoff_id": sheet.id,
                    "reason": reason,
                    "rolled_back_to": sheet.from_terminal,
                },
                at,
            )
            assert_bag(session.state, sheet.bag_id)
        return sheet

    # -- history reuse -----------------------------------------------------

    def reopen_return(self, previous: str, principal: Principal) -> HandoffSheet:
        """Open the reverse handoff reusing a finished sheet as the source."""

        source = self.get(previous)
        if not source.complete:
            raise OrderingError(
                f"source handoff {previous} is {source.status.value}; "
                "only a completed sheet can seed a return"
            )
        if principal.terminal != source.to_terminal:
            raise ConflictError(
                f"only current holder {source.to_terminal} may open the return of {previous}"
            )
        sheet = self.open_handoff(
            source.bag_id,
            principal,
            to_terminal=source.from_terminal,
            note=f"return based on {previous}",
        )
        return sheet

    def replay_ownership(self, bag_id: str) -> list[dict[str, str]]:
        """Rebuild the ownership timeline from append-only sheet history."""

        timeline: list[dict[str, str]] = []
        for sheet in self.history(bag_id):
            if sheet.status is HandoffStatus.CONFIRMED:
                timeline.append(
                    {"handoff_id": sheet.id, "owner": sheet.to_terminal, "at": sheet.confirmed_at}
                )
            elif sheet.status is HandoffStatus.INTERRUPTED:
                timeline.append(
                    {
                        "handoff_id": sheet.id,
                        "owner": sheet.from_terminal,
                        "at": sheet.interrupted_at,
                        "note": "rolled back",
                    }
                )
        if not timeline:
            prefix, _suffix = split_bag_id(bag_id)
            timeline.append({"handoff_id": "", "owner": prefix, "at": ""})
        return timeline

    def rebuild_views(self) -> dict[str, object]:
        """Re-derive both terminal views from the committed journal."""

        with self._store.session() as session:
            self._sync_views(session, force=True)
        report = reconcile(self._store.state())
        if not report["ok"]:
            raise ConflictError("; ".join(report["problems"]))
        return report

    # -- internals ---------------------------------------------------------

    def _build_open(
        self,
        session,
        bag_id: str,
        principal: Principal,
        to_terminal: str,
        note: str,
        at: str,
    ) -> HandoffSheet:
        registry = OwnershipRegistry(session.state)
        owner = registry.owner_of(bag_id)
        if principal.terminal != owner:
            raise ConflictError(
                f"only owner terminal {owner} may open a handoff for {bag_id}"
            )
        for existing in session.state.handoff_sheets.values():
            if existing["bag_id"] == bag_id and existing["status"] == HandoffStatus.OPEN.value:
                raise ConflictError(
                    f"bag {bag_id} already has an open handoff {existing['id']}"
                )
        target = (to_terminal or "").strip().upper() or other_terminal(owner)
        self._require_terminal(target)
        if target == owner:
            raise ValidationError("a handoff must target the other terminal")
        sheet = HandoffSheet(
            id=self._ids.next(),
            bag_id=bag_id,
            from_terminal=owner,
            to_terminal=target,
            status=HandoffStatus.OPEN,
            ownership_rule=OwnershipRule.ON_CONFIRMATION,
            rule_reason=OWNERSHIP_REASON,
            opened_at=at,
            opened_by=principal,
            events=[
                HandoffEvent(
                    sequence=1,
                    kind="sender_opened",
                    at=at,
                    principal=principal,
                    detail={"note": note} if note else {},
                )
            ],
        )
        return sheet

    def _authorize_interrupt(
        self, sheet: HandoffSheet, principal: Principal, reason: str
    ) -> None:
        if principal.terminal == sheet.from_terminal:
            allowed = INTERRUPT_REASONS_SENDER
        elif principal.terminal == sheet.to_terminal:
            allowed = INTERRUPT_REASONS_RECEIVER
        else:
            raise ConflictError(
                f"terminal {principal.terminal} is not party to handoff {sheet.id}"
            )
        if reason not in allowed:
            raise ValidationError(
                f"terminal {principal.terminal} may not interrupt {sheet.id} with reason "
                f"{reason!r}; allowed: {sorted(allowed)}"
            )

    def _load(self, session, handoff_id: str) -> HandoffSheet:
        payload = session.state.handoff_sheets.get(handoff_id)
        if payload is None:
            raise NotFoundError(f"handoff {handoff_id!r} does not exist")
        return HandoffSheet.from_dict(payload)

    def _store_sheet(self, session, sheet: HandoffSheet, at: str) -> None:
        session.state.handoff_sheets[sheet.id] = sheet.as_dict()
        session.record("handoff.sheet.put", {"sheet": sheet.as_dict()}, at)

    def _place_carousel(
        self, session, bag_id: str, terminal: str, handoff_id: str, at: str
    ) -> None:
        # Build fresh record dicts rather than mutating the ones the
        # materialised cache shares with the session, so an exception before
        # commit withdraws the deactivation as well as the new record.
        rebuilt: list[dict[str, Any]] = []
        for record in session.state.carousel_records:
            if record["bag_id"] == bag_id and record.get("active", True):
                inactive = {**record, "active": False}
                rebuilt.append(inactive)
                session.record(
                    "carousel.deactivate",
                    {"bag_id": bag_id, "handoff_id": record["handoff_id"]},
                    at,
                )
            else:
                rebuilt.append(dict(record))
        record = CarouselRecord(
            bag_id=bag_id,
            terminal=terminal,
            handoff_id=handoff_id,
            at=at,
            active=True,
        )
        rebuilt.append(record.as_dict())
        session.state.carousel_records = rebuilt
        session.record("carousel.record", {"record": record.as_dict()}, at)

    def _event(
        self,
        sheet: HandoffSheet,
        kind: str,
        principal: Principal,
        at: str,
        detail: dict[str, Any] | None = None,
    ) -> HandoffEvent:
        return HandoffEvent(
            sequence=len(sheet.events) + 1,
            kind=kind,
            at=at,
            principal=principal,
            detail=dict(detail or {}),
        )

    def _sync_views(self, session, force: bool = False) -> None:
        state = session.state
        registry = OwnershipRegistry(state)
        for terminal in TERMINALS:
            bags = registry.owned_by(terminal)
            existing = state.handoff_views.get(terminal)
            view = {
                "terminal": terminal,
                "bags": bags,
                "updated_at": self._clock.now_iso(),
            }
            if force or existing is None or existing.get("bags") != bags:
                state.handoff_views[terminal] = view
                session.record("handoff.view.put", {"view": dict(view)}, view["updated_at"])

    def _audit_entry(
        self,
        session,
        kind: str,
        stage: str,
        bag_id: str,
        principal: Principal,
        detail: dict[str, Any],
        at: str,
    ) -> None:
        payload = dict(detail)
        payload.update(
            {
                "actor_terminal": principal.terminal,
                "operator": principal.operator,
                "purpose": principal.purpose,
            }
        )
        entry = self._audit.stamp(kind, bag_id, stage, payload, at)
        self._audit.append_to(session, entry)

    def _principal(self, principal: Principal) -> Principal:
        if not isinstance(principal, Principal):
            raise ValidationError("handoff calls require a Principal (terminal, operator, purpose)")
        principal = principal.validate()
        self._require_terminal(principal.terminal)
        return principal

    def _require_terminal(self, terminal: str) -> str:
        terminal = (terminal or "").strip().upper()
        if terminal not in TERMINALS:
            raise ValidationError(f"unknown terminal {terminal!r}; expected T1 or T2")
        return terminal
