"""Inter-terminal handover: ownership, two-party confirmation and rollback.

The story under test: T1 and T2 occasionally route bags across each other and
used to close the loop by phone. The ledger now decides which terminal is
responsible, only completes a handover after both sides confirm, keeps that
decision in step with the carousel records, and audits who called it and why.
"""

from __future__ import annotations

import json

import pytest

from bagsort.app import Plant
from bagsort.clock import Clock
from bagsort.errors import ConflictError, NotFoundError, ValidationError
from bagsort.model.entities import Actor
from bagsort.model.enums import HandoverStatus
from bagsort.store.journal import Journal
from tests.support import make_plant, make_server


def second_terminal(plant: Plant, terminal: str) -> Plant:
    """Another hall's plant folded over the very same committed journal."""

    return Plant.build(plant.store, terminal=terminal, clock=Clock())


def fail_on_journal_kind(monkeypatch, kind: str) -> None:
    """Stage part of a transaction, then fail once so the session must roll it back."""

    original = Journal.append
    armed = {"value": True}

    def broken_append(self, entry_kind, payload, at=""):
        if armed["value"] and entry_kind == kind:
            armed["value"] = False
            raise RuntimeError(f"journal failure while staging {kind}")
        return original(self, entry_kind, payload, at)

    monkeypatch.setattr(Journal, "append", broken_append)


def sender(plant, name="alice", purpose="inter-terminal transfer"):
    return plant.actor(name, "T1", purpose)


def receiver(plant, name="bob", purpose="accept inbound transfer"):
    return plant.actor(name, "T2", purpose)


def registered_bag(plant, barcode="X1", actor=None):
    plant.register_custody(f"T1-{barcode}", actor or sender(plant))
    return f"T1-{barcode}"


def proposed(plant, barcode="X1", actor=None):
    bag_id = registered_bag(plant, barcode, actor)
    order = plant.propose_handover(bag_id, actor or sender(plant))
    return bag_id, order


# -- shared namespace --------------------------------------------------------


def test_a_prefixed_bag_id_is_claimed_only_once(plant):
    bag_id = registered_bag(plant)
    # A second claim from either terminal is stopped on the spot.
    with pytest.raises(ConflictError):
        plant.register_custody(bag_id, sender(plant))
    with pytest.raises(ConflictError):
        plant.register_custody(bag_id, receiver(plant))


def test_a_bag_cannot_be_registered_under_a_foreign_prefix(plant):
    with pytest.raises(ValidationError):
        plant.register_custody("T2-X1", sender(plant))
    with pytest.raises(ValidationError):
        plant.register_custody("X1", sender(plant))


def test_the_actor_must_belong_to_the_terminal_it_claims_for(plant):
    with pytest.raises(ValidationError):
        plant.register_custody("T1-X1", receiver(plant))


def test_two_terminals_share_one_namespace_without_id_collision(tmp_path):
    t1 = make_plant(tmp_path, terminal="T1", name="s.json")
    t2 = second_terminal(t1, "T2")
    t1.register_custody("T1-A", t1.actor("a", "T1", "t1 bag"))
    t2.register_custody("T2-B", t2.actor("b", "T2", "t2 bag"))
    # The shared store is the namespace: T2 cannot re-claim T1's id either.
    with pytest.raises(ConflictError):
        t2.register_custody("T1-A", t2.actor("b", "T2", "duplicate"))


# -- two-party confirmation --------------------------------------------------


def test_proposal_does_not_move_ownership(plant):
    bag_id, order = proposed(plant)
    assert order.status is HandoverStatus.PROPOSED
    assert order.received_by == ""
    assert plant.custody.responsible_for(bag_id) == "T1"
    assert plant.custody.consistent(bag_id) is True


def test_handover_is_not_complete_until_the_receiver_confirms(plant):
    bag_id, order = proposed(plant)
    # The sender cannot confirm its own proposal.
    with pytest.raises(ValidationError):
        plant.confirm_handover(order.id, sender(plant))
    # Before confirmation the sheet is still open and ownership is unchanged.
    assert plant.handover_history.open_orders()[0].id == order.id
    assert plant.custody.responsible_for(bag_id) == "T1"

    confirmed = plant.confirm_handover(order.id, receiver(plant))
    assert confirmed.status is HandoverStatus.COMPLETED
    assert confirmed.received_by == "bob"
    assert plant.custody.responsible_for(bag_id) == "T2"


def test_completion_is_idempotently_guarded(plant):
    _bag_id, order = proposed(plant)
    plant.confirm_handover(order.id, receiver(plant))
    with pytest.raises(ConflictError):
        plant.confirm_handover(order.id, receiver(plant))


def test_only_the_named_receiver_may_confirm(plant):
    _bag_id, order = proposed(plant)
    stranger = plant.actor("carol", "T1", "snooping")
    with pytest.raises(ValidationError):
        plant.confirm_handover(order.id, stranger)


def test_a_bag_cannot_have_two_open_handovers(plant):
    bag_id, _order = proposed(plant)
    with pytest.raises(ConflictError):
        plant.propose_handover(bag_id, sender(plant))


# -- ownership point and the written reason ----------------------------------


def test_the_sheet_carries_the_ownership_decision_and_its_reason(plant):
    _bag_id, order = proposed(plant)
    assert order.ownership_point == "receiver_confirmation"
    assert order.from_terminal == "T1"
    assert order.to_terminal == "T2"
    # The reason states the interrupted-handover responsibility explicitly.
    assert "interrupted" in order.ownership_reason
    assert "sending terminal" in order.ownership_reason
    # The reason survives replay as part of the sheet.
    assert plant.handover_history.versions(order.id)[-1].ownership_reason == order.ownership_reason


# -- interruption and rollback -----------------------------------------------


def test_abort_rolls_ownership_back_to_the_sender(plant):
    bag_id, order = proposed(plant)
    aborted = plant.abort_handover(order.id, receiver(plant), reason="receiver gate jammed")
    assert aborted.status is HandoverStatus.ABORTED
    assert plant.custody.responsible_for(bag_id) == "T1"
    assert plant.custody.consistent(bag_id) is True
    # A completed sheet cannot be aborted.
    with pytest.raises(ConflictError):
        plant.abort_handover(order.id, sender(plant))


def test_a_rolled_back_bag_can_be_handed_over_again(plant):
    bag_id, first = proposed(plant)
    plant.abort_handover(first.id, sender(plant))
    second = plant.propose_handover(bag_id, sender(plant))
    assert second.status is HandoverStatus.PROPOSED
    confirmed = plant.confirm_handover(second.id, receiver(plant))
    assert confirmed.status is HandoverStatus.COMPLETED
    assert plant.custody.responsible_for(bag_id) == "T2"


def test_both_terminals_hold_identical_views_after_rollback(tmp_path):
    t1 = make_plant(tmp_path, terminal="T1", name="shared.json")
    t2 = second_terminal(t1, "T2")
    bag_id, order = proposed(t1)
    t1.abort_handover(order.id, t1.actor("alice", "T1", "cancel"))
    # Both halls fold the same journal: same responsible terminal, same epoch.
    r1, r2 = t1.custody.custody_of(bag_id), t2.custody.custody_of(bag_id)
    assert r1.terminal == r2.terminal == "T1"
    assert r1.epoch == r2.epoch
    assert t1.custody.consistent(bag_id) and t2.custody.consistent(bag_id)
    assert t2.handover_history.versions(order.id)[-1].status is HandoverStatus.ABORTED


def test_a_fault_during_confirm_leaves_ownership_with_the_sender(plant, monkeypatch):
    bag_id, order = proposed(plant)
    fail_on_journal_kind(monkeypatch, "custody.put")
    with pytest.raises(RuntimeError):
        plant.confirm_handover(order.id, receiver(plant))
    # The buffered transaction never committed; sender still owns it and the
    # sheet is still open, so the retry can confirm cleanly.
    assert plant.custody.responsible_for(bag_id) == "T1"
    assert plant.custody.order(order.id).status is HandoverStatus.PROPOSED
    assert plant.store.pending() == []
    assert plant.custody.consistent(bag_id) is True
    plant.confirm_handover(order.id, receiver(plant))
    assert plant.custody.responsible_for(bag_id) == "T2"


def test_a_fault_during_propose_leaves_no_trace(plant, monkeypatch):
    plant.register_custody("T1-Z", sender(plant))
    fail_on_journal_kind(monkeypatch, "carousel.move")
    with pytest.raises(RuntimeError):
        plant.propose_handover("T1-Z", sender(plant))
    assert plant.store.pending() == []
    assert plant.custody.open_order_for("T1-Z") is None
    assert plant.custody.moves_for("T1-Z") == []
    assert plant.custody.consistent("T1-Z") is True


# -- ownership, sheet and carousel stay in step ------------------------------


def test_carousel_records_name_the_responsible_terminal_at_each_step(plant):
    bag_id, order = proposed(plant)
    moves = plant.custody.moves_for(bag_id)
    assert [m.phase for m in moves] == ["transfer_area_entry"]
    assert moves[0].terminal == "T1"
    plant.confirm_handover(order.id, receiver(plant))
    moves = plant.custody.moves_for(bag_id)
    assert [m.phase for m in moves] == ["transfer_area_entry", "receiver_acceptance"]
    assert [m.terminal for m in moves] == ["T1", "T2"]
    assert plant.custody.consistent(bag_id) is True


def test_a_returned_bag_has_a_sender_side_carousel_record(plant):
    bag_id, order = proposed(plant)
    plant.abort_handover(order.id, sender(plant))
    last = plant.custody.last_move_for(bag_id)
    assert last.phase == "sender_return"
    assert last.terminal == "T1"
    assert plant.custody.discrepancies(bag_id) == []


def test_every_operation_takes_effect_in_one_committed_transaction(plant):
    bag_id, order = proposed(plant)
    watermark_before = plant.store.watermark
    plant.confirm_handover(order.id, receiver(plant))
    # Order, custody and carousel move land together; replay rebuilds the same
    # state, so the three facts cannot permanently disagree.
    assert plant.store.watermark >= watermark_before + 3
    rebuilt = plant.store.state().as_dict()
    assert rebuilt["custody"][bag_id]["terminal"] == "T2"
    assert rebuilt["handovers"][order.id]["status"] == "completed"
    assert rebuilt["carousel_moves"][-1]["terminal"] == "T2"


# -- audit: caller identity and purpose --------------------------------------


def test_every_handover_step_is_audited_with_actor_and_purpose(plant):
    bag_id, order = proposed(plant, actor=sender(plant, purpose="late transfer"))
    plant.confirm_handover(order.id, receiver(plant))
    entries = {entry.kind: entry for entry in plant.audit.for_subject(bag_id)}
    assert set(entries) >= {"custody_register", "handover_propose", "handover_confirm"}
    assert entries["handover_propose"].detail["actor"] == "alice"
    assert entries["handover_propose"].detail["purpose"] == "late transfer"
    assert entries["handover_confirm"].detail["actor_terminal"] == "T2"
    assert entries["handover_propose"].detail["stage"] == "handover"
    assert plant.audit.verify() is True


def test_abort_audit_keeps_the_caller_and_reason(plant):
    bag_id, order = proposed(plant)
    plant.abort_handover(order.id, receiver(plant), reason="no staff at gate")
    entry = plant.audit.for_subject(bag_id)[-1]
    assert entry.kind == "handover_abort"
    assert entry.detail["reason"] == "no staff at gate"
    assert entry.detail["actor_terminal"] == "T2"


def test_handover_calls_without_identity_are_rejected_by_the_api(server):
    response = server.call("POST", "/api/handovers/custody", {"bag_id": "T1-A"})
    assert response.status == 400
    assert "actor" in json.loads(response.body)["error"]


# -- history reuse ------------------------------------------------------------


def test_handover_history_is_reusable_after_the_fact(plant):
    bag_id, first = proposed(plant, barcode="R1")
    plant.confirm_handover(first.id, receiver(plant))
    details = plant.handover_history.reuse_confirmation(first.id)
    assert details["bag_id"] == bag_id
    assert details["from_terminal"] == "T1"
    assert details["received_by"] == "bob"
    # Every sheet version is available, not just the final state.
    assert [o.status for o in plant.handover_history.versions(first.id)] == [
        HandoverStatus.PROPOSED,
        HandoverStatus.COMPLETED,
    ]


def test_only_a_completed_sheet_can_be_reused(plant):
    _bag_id, order = proposed(plant)
    with pytest.raises(ValueError):
        plant.handover_history.reuse_confirmation(order.id)
    with pytest.raises(KeyError):
        plant.handover_history.reuse_confirmation("hnd-999")


def test_history_filters_by_bag_pair_and_status(plant):
    _a, first = proposed(plant, barcode="A1")
    plant.confirm_handover(first.id, receiver(plant))
    bag_b, second = proposed(plant, barcode="B1")
    plant.abort_handover(second.id, sender(plant))
    assert [o.id for o in plant.handover_history.for_bag(bag_b)] == [second.id]
    assert [o.id for o in plant.handover_history.between("T1", "T2")] == [first.id, second.id]
    assert [o.id for o in plant.handover_history.completed()] == [first.id]
    assert [o.id for o in plant.handover_history.aborted()] == [second.id]
    assert plant.handover_history.open_orders() == []


def test_unknown_bag_and_order_lookups_are_not_found(plant):
    with pytest.raises(NotFoundError):
        plant.custody.custody_of("T1-NOPE")
    with pytest.raises(NotFoundError):
        plant.custody.order("hnd-000042")


# -- HTTP surface -------------------------------------------------------------


def test_full_handover_round_trip_over_http(tmp_path):
    server = make_server(tmp_path, terminal="T1")
    created = server.call(
        "POST",
        "/api/handovers/custody",
        {"bag_id": "T1-W1", "actor": "alice", "actor_terminal": "T1", "purpose": "transfer"},
    )
    assert created.status == 201
    proposal = server.call(
        "POST",
        "/api/handovers",
        {"bag_id": "T1-W1", "actor": "alice", "actor_terminal": "T1", "purpose": "transfer"},
    )
    order_id = json.loads(proposal.body)["id"]
    pending = server.call("GET", "/api/handovers/open")
    assert [row["id"] for row in json.loads(pending.body)] == [order_id]
    # Confirming from T2 completes the move.
    confirmed = server.call(
        "POST",
        f"/api/handovers/{order_id}/confirm",
        {"actor": "bob", "actor_terminal": "T2", "purpose": "accept"},
    )
    assert json.loads(confirmed.body)["status"] == "completed"
    custody = server.call("GET", "/api/custody/T1-W1")
    assert json.loads(custody.body)["terminal"] == "T2"
    verified = server.call("GET", "/api/custody/verify")
    assert json.loads(verified.body)["consistent"] is True
    sheet = server.call("GET", f"/api/handovers/{order_id}")
    body = json.loads(sheet.body)
    assert len(body["versions"]) == 2
    assert body["ownership_point"] == "receiver_confirmation"
    moves = server.call("GET", "/api/custody/T1-W1/moves")
    assert [m["terminal"] for m in json.loads(moves.body)["moves"]] == ["T1", "T2"]
