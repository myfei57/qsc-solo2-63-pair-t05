"""Custody ledger for bags that move between terminals.

This is the authority that replaces phone-call handshakes. Three facts about
one bag are always kept inside a single journal transaction, so they can never
be observed disagreeing:

* **ownership** - the :class:`CustodyRecord` naming the responsible terminal;
* **the handover sheet** - the :class:`HandoverOrder` with both confirmations;
* **the carousel record** - the latest :class:`CarouselMove` feeding a
  carousel, which names the terminal responsible for that move.

The ownership decision is fixed on purpose (see
:data:`OWNERSHIP_ON_RECEIVER_CONFIRMATION`): responsibility moves only when
the receiving terminal confirms acceptance. Entering the shared transfer area
merely proposes the handover. With the opposite rule responsibility would flip
before the other side had agreed, which inverts who answers for a lost bag
during an interrupted handover. The chosen rule and its reason are written
onto every order.

Because ownership does not move at proposal time, an interruption never has to
undo a transfer: the open order is aborted, the bag is back on a sender-side
carousel and its custody is exactly what it was before the proposal. Both
terminals fold the same committed journal, which is what keeps their views of
one bag identical after the rollback.
"""

from __future__ import annotations

from bagsort.clock import Clock
from bagsort.errors import ConflictError, NotFoundError, ValidationError
from bagsort.ids import IdFactory
from bagsort.model.entities import Actor, CarouselMove, CustodyRecord, HandoverOrder
from bagsort.model.enums import HandoverStatus
from bagsort.model.validate import require_text
from bagsort.store.store import Store

OWNERSHIP_ON_RECEIVER_CONFIRMATION = "receiver_confirmation"

OWNERSHIP_REASON = (
    "responsibility moves only when the receiving terminal confirms acceptance; "
    "entering the transfer area only proposes the handover, so an interrupted "
    "handover stays the sending terminal's responsibility and rolls back to the "
    "ownership held before the proposal"
)

TRANSIT_AREA = "TRANSIT"

PHASE_PROPOSED = "transfer_area_entry"
PHASE_RECEIVED = "receiver_acceptance"
PHASE_RETURNED = "sender_return"

KNOWN_TERMINALS = ("T1", "T2")


class CustodyLedger:
    """Ownership, two-party confirmation and carousel moves for one bag."""

    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self._clock = clock
        self._order_ids = IdFactory("hnd")
        self._move_ids = IdFactory("crm")

    # -- registration ------------------------------------------------------

    def register(self, bag_id: str, terminal: str, actor: Actor) -> CustodyRecord:
        """Claim ``bag_id`` once in the shared prefixed namespace.

        Bag ids carry their origin terminal as a prefix (``T1-...``). A
        second claim of the same id, from either terminal, is rejected on the
        spot instead of silently shadowing the first registration.
        """

        bag_id = require_text(bag_id, "bag_id")
        terminal = self._require_terminal(terminal)
        self._require_actor(actor, terminal)
        if bag_id in self._store.state().custody:
            # Duplicate claim in the shared namespace is a conflict whoever
            # raises it; foreign-prefix rejection only applies to new ids.
            raise ConflictError(f"bag {bag_id!r} is already registered in the shared namespace")
        prefix = self._prefix_of(bag_id)
        if prefix != terminal:
            raise ValidationError(
                f"bag {bag_id!r} belongs to terminal {prefix} and cannot be "
                f"registered by {terminal}"
            )
        at = self._clock.now_iso()
        record = CustodyRecord(bag_id=bag_id, terminal=terminal, epoch=1, updated_at=at)
        with self._store.session() as session:
            session.state.custody[bag_id] = record
            session.record("custody.put", {"record": record.as_dict()}, at)
            session.record("counter.add", {"name": "custody.claimed", "delta": 1}, at)
        return record

    # -- handover lifecycle ------------------------------------------------

    def propose(
        self,
        bag_id: str,
        actor: Actor,
        purpose: str = "",
        carousel: str = "",
    ) -> HandoverOrder:
        """Let the bag enter the transfer area without moving responsibility.

        This is the sender's confirmation. The receiver still has to confirm,
        and while the order is open the sending terminal remains responsible.
        """

        bag_id = require_text(bag_id, "bag_id")
        purpose = require_text(purpose or actor.purpose, "purpose")
        record = self._require_custody(bag_id)
        self._require_actor(actor, record.terminal)
        self._reject_open_order(bag_id)
        target = self._other_terminal(record.terminal)
        at = self._clock.now_iso()
        order = HandoverOrder(
            id=self._order_ids.next(),
            bag_id=bag_id,
            from_terminal=record.terminal,
            to_terminal=target,
            status=HandoverStatus.PROPOSED,
            ownership_point=OWNERSHIP_ON_RECEIVER_CONFIRMATION,
            ownership_reason=OWNERSHIP_REASON,
            proposed_by=actor.name,
            proposed_by_terminal=actor.terminal,
            purpose=purpose,
            proposed_at=at,
            custody_epoch=record.epoch,
        )
        move = CarouselMove(
            id=self._move_ids.next(),
            bag_id=bag_id,
            order_id=order.id,
            carousel=carousel or self._transit_carousel(record.terminal),
            terminal=record.terminal,
            phase=PHASE_PROPOSED,
            at=at,
        )
        with self._store.session() as session:
            session.state.handovers[order.id] = order
            session.state.carousel_moves.append(move)
            session.record("handover.put", {"order": order.as_dict()}, at)
            session.record("carousel.move", {"move": move.as_dict()}, at)
            session.record("counter.add", {"name": "handover.proposed", "delta": 1}, at)
        return order

    def confirm(
        self,
        order_id: str,
        actor: Actor,
        carousel: str = "",
    ) -> HandoverOrder:
        """The receiving terminal accepts the bag; only now ownership moves.

        Confirmation is refused unless the caller is the receiving terminal
        and the sheet is still waiting for its second confirmation.
        """

        order_id = require_text(order_id, "order_id")
        order = self._require_order(order_id)
        if order.status is not HandoverStatus.PROPOSED:
            raise ConflictError(
                f"handover {order_id!r} is {order.status.value}, not awaiting confirmation"
            )
        self._require_actor(actor, order.to_terminal)
        record = self._require_custody(order.bag_id)
        if record.terminal != order.from_terminal or record.epoch != order.custody_epoch:
            raise ConflictError(
                f"custody of {order.bag_id!r} changed since handover {order_id!r} was proposed"
            )
        at = self._clock.now_iso()
        updated = order.evolved(
            status=HandoverStatus.COMPLETED,
            received_by=actor.name,
            received_by_terminal=actor.terminal,
            received_at=at,
        )
        moved = record.evolved(terminal=order.to_terminal, epoch=record.epoch + 1, updated_at=at)
        move = CarouselMove(
            id=self._move_ids.next(),
            bag_id=order.bag_id,
            order_id=order.id,
            carousel=carousel or self._receiving_carousel(order.to_terminal),
            terminal=order.to_terminal,
            phase=PHASE_RECEIVED,
            at=at,
        )
        with self._store.session() as session:
            session.state.handovers[order.id] = updated
            session.state.custody[order.bag_id] = moved
            session.state.carousel_moves.append(move)
            session.record("handover.put", {"order": updated.as_dict()}, at)
            session.record("custody.put", {"record": moved.as_dict()}, at)
            session.record("carousel.move", {"move": move.as_dict()}, at)
            session.record("counter.add", {"name": "handover.completed", "delta": 1}, at)
        return updated

    def abort(
        self,
        order_id: str,
        actor: Actor,
        reason: str = "interrupted before receiver confirmation",
    ) -> HandoverOrder:
        """Interrupt an open handover and return responsibility to the sender.

        Ownership never moved, so the bag is restored to exactly the custody
        it had before the proposal. Either terminal may call this while the
        sheet is open; the caller is recorded.
        """

        order_id = require_text(order_id, "order_id")
        reason = reason.strip() or "interrupted before receiver confirmation"
        order = self._require_order(order_id)
        if order.status is not HandoverStatus.PROPOSED:
            raise ConflictError(f"handover {order_id!r} is {order.status.value} and cannot abort")
        if actor.terminal not in (order.from_terminal, order.to_terminal):
            raise ValidationError(
                f"{actor.terminal} is not a party to handover {order_id!r}"
            )
        self._require_actor(actor, actor.terminal)
        record = self._require_custody(order.bag_id)
        if record.terminal != order.from_terminal:
            raise ConflictError(
                f"custody of {order.bag_id!r} no longer matches open handover {order_id!r}"
            )
        at = self._clock.now_iso()
        # The owner was always the sender; the epoch still bumps so a viewer
        # can tell the rollback happened, while terminal/ownership is restored.
        restored = record.evolved(
            terminal=order.from_terminal, epoch=order.custody_epoch + 1, updated_at=at
        )
        updated = order.evolved(
            status=HandoverStatus.ABORTED,
            aborted_by=actor.name,
            aborted_by_terminal=actor.terminal,
            abort_reason=reason,
            aborted_at=at,
        )
        move = CarouselMove(
            id=self._move_ids.next(),
            bag_id=order.bag_id,
            order_id=order.id,
            carousel=self._transit_carousel(order.from_terminal),
            terminal=order.from_terminal,
            phase=PHASE_RETURNED,
            at=at,
        )
        with self._store.session() as session:
            session.state.handovers[order.id] = updated
            session.state.custody[order.bag_id] = restored
            session.state.carousel_moves.append(move)
            session.record("handover.put", {"order": updated.as_dict()}, at)
            session.record("custody.put", {"record": restored.as_dict()}, at)
            session.record("carousel.move", {"move": move.as_dict()}, at)
            session.record("counter.add", {"name": "handover.aborted", "delta": 1}, at)
        return updated

    # -- read side ---------------------------------------------------------

    def custody_of(self, bag_id: str) -> CustodyRecord:
        return self._require_custody(bag_id)

    def responsible_for(self, bag_id: str) -> str:
        return self._require_custody(bag_id).terminal

    def order(self, order_id: str) -> HandoverOrder:
        return self._require_order(order_id)

    def open_order_for(self, bag_id: str) -> HandoverOrder | None:
        for order in self._store.state().handovers.values():
            if order.bag_id == bag_id and order.status.open:
                return order
        return None

    def moves_for(self, bag_id: str) -> list[CarouselMove]:
        return [move for move in self._store.state().carousel_moves if move.bag_id == bag_id]

    def last_move_for(self, bag_id: str) -> CarouselMove | None:
        moves = self.moves_for(bag_id)
        return moves[-1] if moves else None

    def discrepancies(self, bag_id: str = "") -> list[str]:
        """Ownership, sheet and carousel must agree; list every violation."""

        state = self._store.state()
        bag_ids = [bag_id] if bag_id else sorted(state.custody)
        problems: list[str] = []
        for identifier in bag_ids:
            record = state.custody.get(identifier)
            if record is None:
                if identifier:
                    problems.append(f"bag {identifier!r} has no custody record")
                continue
            move = self.last_move_for(identifier)
            if move is not None:
                expected = move.terminal
                open_order = self.open_order_for(identifier)
                if open_order is not None:
                    # While the receiver has not confirmed, the transfer area
                    # stays on the sender's responsibility.
                    expected = open_order.from_terminal
                if expected != record.terminal:
                    problems.append(
                        f"bag {identifier!r}: custody is {record.terminal!r} but latest "
                        f"carousel {move.carousel!r} is {move.terminal!r}'s responsibility"
                    )
            open_order = self.open_order_for(identifier)
            if open_order is not None and open_order.from_terminal != record.terminal:
                problems.append(
                    f"bag {identifier!r}: open handover {open_order.id!r} expects "
                    f"{open_order.from_terminal!r} to remain responsible"
                )
            for order in state.handovers.values():
                if order.bag_id != identifier:
                    continue
                if order.status is HandoverStatus.COMPLETED and not order.received_by:
                    problems.append(
                        f"handover {order.id!r} is completed without receiver confirmation"
                    )
                if order.received_by and order.status is not HandoverStatus.COMPLETED:
                    problems.append(
                        f"handover {order.id!r} carries receiver confirmation but is "
                        f"{order.status.value}"
                    )
        return problems

    def consistent(self, bag_id: str = "") -> bool:
        return not self.discrepancies(bag_id)

    def require_consistent(self, bag_id: str = "") -> None:
        problems = self.discrepancies(bag_id)
        if problems:
            raise ConflictError("custody/handover/carousel mismatch: " + "; ".join(problems))

    # -- helpers -----------------------------------------------------------

    def _require_custody(self, bag_id: str) -> CustodyRecord:
        record = self._store.state().custody.get(bag_id)
        if record is None:
            raise NotFoundError(f"bag {bag_id!r} is not registered in the shared namespace")
        return record

    def _require_order(self, order_id: str) -> HandoverOrder:
        order = self._store.state().handovers.get(order_id)
        if order is None:
            raise NotFoundError(f"handover {order_id!r} does not exist")
        return order

    def _reject_open_order(self, bag_id: str) -> None:
        open_order = self.open_order_for(bag_id)
        if open_order is not None:
            raise ConflictError(
                f"bag {bag_id!r} already has open handover {open_order.id!r}"
            )

    @staticmethod
    def _require_terminal(terminal: str) -> str:
        text = require_text(terminal, "terminal").upper()
        if text not in KNOWN_TERMINALS:
            raise ValidationError(f"unknown terminal {terminal!r}; expected one of {KNOWN_TERMINALS}")
        return text

    @staticmethod
    def _require_actor(actor: Actor, terminal: str) -> Actor:
        if not isinstance(actor, Actor):
            raise ValidationError("handover calls require an Actor identity")
        name = require_text(actor.name, "actor.name")
        actor_terminal = require_text(actor.terminal, "actor.terminal").upper()
        if actor_terminal != terminal:
            raise ValidationError(
                f"actor terminal {actor_terminal!r} may not act for terminal {terminal!r}"
            )
        return Actor(name=name, terminal=actor_terminal, purpose=actor.purpose)

    @staticmethod
    def _prefix_of(bag_id: str) -> str:
        prefix, separator, _suffix = bag_id.partition("-")
        if not separator or prefix not in KNOWN_TERMINALS:
            raise ValidationError(
                f"bag id {bag_id!r} must start with a shared terminal prefix {KNOWN_TERMINALS}"
            )
        return prefix

    @staticmethod
    def _other_terminal(terminal: str) -> str:
        return "T2" if terminal == "T1" else "T1"

    @staticmethod
    def _transit_carousel(sender: str) -> str:
        return f"{sender}-{TRANSIT_AREA}"

    @staticmethod
    def _receiving_carousel(receiver: str) -> str:
        return f"{receiver}-CRIN"

