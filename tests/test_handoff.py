"""Cross-terminal handoff: ownership, two-party confirmation and rollback."""

from __future__ import annotations

import pytest

from bagsort.errors import ConflictError, NotFoundError, OrderingError, ValidationError
from bagsort.handoff import OWNERSHIP_RULE
from bagsort.handoff.entities import Principal
from bagsort.store.store import Store
from bagsort.app import Plant
from bagsort.clock import Clock


def p(terminal: str, operator: str, purpose: str) -> Principal:
    return Principal(terminal=terminal, operator=operator, purpose=purpose)


SENDER = p("T1", "agent-lee", "dispatch to the other terminal")
RECEIVER = p("T2", "agent-kim", "receive the transferring bag")


# -- shared namespace --------------------------------------------------------


def test_duplicate_registration_is_rejected_on_the_spot(plant):
    plant.register_handoff_bag("T1-B100", SENDER)
    with pytest.raises(ConflictError):
        plant.register_handoff_bag("T1-B100", SENDER)


def test_prefix_cannot_be_claimed_by_the_other_terminal(plant):
    with pytest.raises(ValidationError):
        plant.register_handoff_bag("T2-B100", SENDER)
    with pytest.raises(ValidationError):
        plant.register_handoff_bag("X9-B100", SENDER)


def test_calls_without_identity_or_purpose_are_rejected(plant):
    with pytest.raises(ValidationError):
        plant.register_handoff_bag("T1-B100", Principal("T1", "", "purpose"))
    with pytest.raises(ValidationError):
        plant.register_handoff_bag("T1-B100", Principal("T1", "agent", "  "))
    with pytest.raises(ValidationError):
        plant.register_handoff_bag("T1-B100", Principal("T9", "agent", "purpose"))


# -- ownership stays until confirmation --------------------------------------


def test_ownership_does_not_move_when_the_bag_enters_transfer(plant):
    plant.register_handoff_bag("T1-B200", SENDER)
    sheet = plant.open_handoff("T1-B200", SENDER)

    verdict = plant.handoff.verdict("T1-B200")
    assert verdict.owner_terminal == "T1"
    assert verdict.handoff_id == sheet.id
    assert verdict.status.value == "open"
    assert verdict.rule.value == OWNERSHIP_RULE

    record = plant.handoff.active_carousel("T1-B200")
    assert record.terminal == "T1"
    assert record.handoff_id == sheet.id
    assert plant.handoff.reconcile()["ok"] is True


def test_only_the_receiving_terminal_can_confirm(plant):
    plant.register_handoff_bag("T1-B201", SENDER)
    sheet = plant.open_handoff("T1-B201", SENDER)
    with pytest.raises(ConflictError):
        plant.confirm_handoff(sheet.id, SENDER)
    assert plant.handoff.get(sheet.id).status.value == "open"


def test_double_open_for_one_bag_is_rejected(plant):
    plant.register_handoff_bag("T1-B202", SENDER)
    plant.open_handoff("T1-B202", SENDER)
    with pytest.raises(ConflictError):
        plant.open_handoff("T1-B202", SENDER)


def test_non_owner_cannot_open_a_handoff(plant):
    plant.register_handoff_bag("T1-B203", SENDER)
    with pytest.raises(ConflictError):
        plant.open_handoff("T1-B203", RECEIVER)


def test_confirmation_moves_ownership_sheet_and_carousel_together(plant):
    plant.register_handoff_bag("T1-B204", SENDER)
    sheet = plant.open_handoff("T1-B204", SENDER)
    confirmed = plant.confirm_handoff(sheet.id, RECEIVER)

    assert confirmed.status.value == "confirmed"
    assert confirmed.complete is True
    assert plant.handoff.verdict("T1-B204").owner_terminal == "T2"
    record = plant.handoff.active_carousel("T1-B204")
    assert record.terminal == "T2"
    assert record.handoff_id == sheet.id
    assert plant.handoff.terminal_view("T1")["bags"] == []
    assert plant.handoff.terminal_view("T2")["bags"] == ["T1-B204"]
    assert plant.handoff.reconcile()["ok"] is True
    # the pre-confirmation carousel record is retired, not deleted
    history = plant.handoff.carousel_records("T1-B204")
    assert [record.active for record in history] == [False, False, True]


def test_a_confirmed_sheet_cannot_confirm_or_interrupt_again(plant):
    plant.register_handoff_bag("T1-B205", SENDER)
    sheet = plant.open_handoff("T1-B205", SENDER)
    plant.confirm_handoff(sheet.id, RECEIVER)
    with pytest.raises(OrderingError):
        plant.confirm_handoff(sheet.id, RECEIVER)
    with pytest.raises(OrderingError):
        plant.interrupt_handoff(sheet.id, SENDER, "cancelled")


# -- interruption rolls back to the opener ------------------------------------


def test_sender_timeout_rolls_ownership_and_carousel_back(plant):
    plant.register_handoff_bag("T1-B300", SENDER)
    sheet = plant.open_handoff("T1-B300", SENDER)
    interrupted = plant.interrupt_handoff(sheet.id, SENDER, "timeout")

    assert interrupted.status.value == "interrupted"
    assert interrupted.interrupt_reason == "timeout"
    assert plant.handoff.verdict("T1-B300").owner_terminal == "T1"
    record = plant.handoff.active_carousel("T1-B300")
    assert record.terminal == "T1"
    assert record.handoff_id == sheet.id
    assert plant.handoff.reconcile()["ok"] is True


def test_receiver_rejection_rolls_back_to_the_opener(plant):
    plant.register_handoff_bag("T1-B301", SENDER)
    sheet = plant.open_handoff("T1-B301", SENDER)
    plant.interrupt_handoff(sheet.id, RECEIVER, "rejected")
    assert plant.handoff.verdict("T1-B301").owner_terminal == "T1"


def test_interrupt_reasons_are_authorized_per_party(plant):
    plant.register_handoff_bag("T1-B302", SENDER)
    sheet = plant.open_handoff("T1-B302", SENDER)
    with pytest.raises(ValidationError):
        plant.interrupt_handoff(sheet.id, SENDER, "rejected")
    other_sender_operator = p("T1", "agent-morgan", "covering the dispatch desk")
    # a second T1 operator is still the sender party and is bound by its reasons
    with pytest.raises(ValidationError):
        plant.interrupt_handoff(sheet.id, other_sender_operator, "rejected")
    with pytest.raises(ValidationError):
        plant.interrupt_handoff(sheet.id, SENDER, "  ")


def test_interrupted_sheet_cannot_confirm_afterwards(plant):
    plant.register_handoff_bag("T1-B303", SENDER)
    sheet = plant.open_handoff("T1-B303", SENDER)
    plant.interrupt_handoff(sheet.id, SENDER, "cancelled")
    with pytest.raises(OrderingError):
        plant.confirm_handoff(sheet.id, RECEIVER)


def test_failure_before_commit_leaves_no_partial_handoff(plant):
    plant.register_handoff_bag("T1-B304", SENDER)
    sheet = plant.open_handoff("T1-B304", SENDER)
    before = plant.audit.count()
    plant.handoff.crash_on_confirm = True
    with pytest.raises(RuntimeError):
        plant.confirm_handoff(sheet.id, RECEIVER)

    assert plant.handoff.get(sheet.id).status.value == "open"
    assert plant.handoff.verdict("T1-B304").owner_terminal == "T1"
    assert plant.handoff.active_carousel("T1-B304").terminal == "T1"
    assert plant.audit.count() == before
    assert plant.handoff.reconcile()["ok"] is True
    # the rolled back attempt can be retried and then completes
    plant.confirm_handoff(sheet.id, RECEIVER)
    assert plant.handoff.verdict("T1-B304").owner_terminal == "T2"


# -- the sheet states the rule and the reason ---------------------------------


def test_sheet_records_the_ownership_rule_and_its_reason(plant):
    plant.register_handoff_bag("T1-B400", SENDER)
    sheet = plant.open_handoff("T1-B400", SENDER)
    text = plant.handoff_sheet_text(sheet.id)
    assert "HANDOFF SHEET" in text
    assert OWNERSHIP_RULE in text
    assert "确认" in text and "中断" in text
    payload = sheet.as_dict()
    assert payload["ownership_rule"] == OWNERSHIP_RULE
    assert payload["rule_reason"]
    assert "确认" in payload["rule_reason"] and "中断" in payload["rule_reason"]


def test_sheet_lists_both_confirmations_with_their_actors(plant):
    plant.register_handoff_bag("T1-B401", SENDER)
    sheet = plant.open_handoff("T1-B401", SENDER)
    plant.confirm_handoff(sheet.id, RECEIVER)
    stored = plant.handoff.get(sheet.id)
    kinds = [event.kind for event in stored.events]
    assert kinds == ["sender_opened", "receiver_confirmed"]
    actors = {event.principal.terminal for event in stored.events}
    assert actors == {"T1", "T2"}


# -- audit trail carries the caller identity and purpose ----------------------


def test_every_handoff_call_is_audited_with_terminal_operator_and_purpose(plant):
    plant.register_handoff_bag("T1-B500", SENDER)
    sheet = plant.open_handoff("T1-B500", SENDER)
    plant.confirm_handoff(sheet.id, RECEIVER)

    entries = plant.audit.for_subject("T1-B500")
    assert [entry.kind for entry in entries] == [
        "handoff_register",
        "handoff_opened",
        "handoff_confirmed",
    ]
    opener = entries[1]
    assert opener.detail["actor_terminal"] == "T1"
    assert opener.detail["operator"] == "agent-lee"
    assert opener.detail["purpose"] == "dispatch to the other terminal"
    confirmer = entries[2]
    assert confirmer.detail["actor_terminal"] == "T2"
    assert confirmer.detail["operator"] == "agent-kim"
    assert confirmer.detail["purpose"] == "receive the transferring bag"
    assert plant.audit.verify() is True


def test_interrupt_audit_records_the_rollback_target(plant):
    plant.register_handoff_bag("T1-B501", SENDER)
    sheet = plant.open_handoff("T1-B501", SENDER)
    plant.interrupt_handoff(sheet.id, RECEIVER, "rejected")
    entry = plant.audit.for_subject("T1-B501")[-1]
    assert entry.kind == "handoff_interrupted"
    assert entry.detail["reason"] == "rejected"
    assert entry.detail["rolled_back_to"] == "T1"


# -- history reuse -------------------------------------------------------------


def test_handoff_history_is_reusable_for_a_return(plant):
    plant.register_handoff_bag("T1-B600", SENDER)
    outbound = plant.open_handoff("T1-B600", SENDER)
    plant.confirm_handoff(outbound.id, RECEIVER)

    returning = plant.handoff.reopen_return(outbound.id, RECEIVER)
    assert returning.to_terminal == "T1"
    assert returning.from_terminal == "T2"
    plant.confirm_handoff(returning.id, SENDER)
    assert plant.handoff.verdict("T1-B600").owner_terminal == "T1"

    ids = [sheet.id for sheet in plant.handoff.history("T1-B600")]
    assert ids == [outbound.id, returning.id]
    timeline = plant.handoff.replay_ownership("T1-B600")
    assert [item["owner"] for item in timeline] == ["T2", "T1"]


def test_a_rolled_back_sheet_can_feed_a_new_attempt(plant):
    plant.register_handoff_bag("T1-B601", SENDER)
    first = plant.open_handoff("T1-B601", SENDER)
    plant.interrupt_handoff(first.id, SENDER, "timeout")
    second = plant.open_handoff("T1-B601", SENDER)
    plant.confirm_handoff(second.id, RECEIVER)
    assert [sheet.status.value for sheet in plant.handoff.history("T1-B601")] == [
        "interrupted",
        "confirmed",
    ]
    assert plant.handoff.verdict("T1-B601").owner_terminal == "T2"


# -- restart replay -------------------------------------------------------------


def test_handoff_state_survives_restart_from_the_journal(tmp_path):
    path = str(tmp_path / "handoff.json")
    store = Store(path)
    plant = Plant.build(store, terminal="T1", clock=Clock())
    plant.register_handoff_bag("T1-B700", SENDER)
    outbound = plant.open_handoff("T1-B700", SENDER)
    plant.confirm_handoff(outbound.id, RECEIVER)
    returning = plant.handoff.reopen_return(outbound.id, RECEIVER)
    plant.interrupt_handoff(returning.id, SENDER, "rejected")
    store.persist()

    reopened = Plant.build(Store.open(path), terminal="T1", clock=Clock())
    assert reopened.handoff.verdict("T1-B700").owner_terminal == "T2"
    assert reopened.handoff.active_carousel("T1-B700").terminal == "T2"
    assert len(reopened.handoff.sheets()) == 2
    assert reopened.handoff.reconcile()["ok"] is True
    assert reopened.verify() is True
    owners = [item["owner"] for item in reopened.handoff.replay_ownership("T1-B700")]
    assert owners == ["T2", "T2"]


def test_rebuild_views_rederives_both_terminals_from_the_journal(tmp_path):
    path = str(tmp_path / "views.json")
    store = Store(path)
    plant = Plant.build(store, terminal="T1", clock=Clock())
    plant.register_handoff_bag("T1-B701", SENDER)
    sheet = plant.open_handoff("T1-B701", SENDER)
    plant.confirm_handoff(sheet.id, RECEIVER)
    store.persist()

    reopened = Plant.build(Store.open(path), terminal="T1", clock=Clock())
    report = reopened.handoff.rebuild_views()
    assert report["ok"] is True
    assert reopened.handoff.terminal_view("T2")["bags"] == ["T1-B701"]


def test_unknown_bag_and_handoff_are_not_found(plant):
    with pytest.raises(NotFoundError):
        plant.handoff.verdict("T1-MISSING")
    with pytest.raises(NotFoundError):
        plant.handoff.get("HO-999999")
