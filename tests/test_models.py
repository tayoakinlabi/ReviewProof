import dataclasses

import pytest

from reviewproof.models import Item, OversightContext, Ticket, new_item_id, new_token

CONTEXT = {
    "system": "s",
    "model_id": "m",
    "model_version": "1",
    "prompt_version": "1",
    "tool_schema_hash": "t",
}


def test_ticket_exposes_only_token_and_payload():
    # Guards the host-facing surface: no planted marker, kind or target ID.
    assert [f.name for f in dataclasses.fields(Ticket)] == ["token", "payload"]


def test_tokens_and_item_ids_are_unique_and_uniform():
    tokens = {new_token() for _ in range(500)}
    item_ids = {new_item_id() for _ in range(500)}
    assert len(tokens) == len(item_ids) == 500
    assert {len(token) for token in tokens} == {43}
    assert {len(item_id) for item_id in item_ids} == {32}


def test_repr_does_not_leak_payload_trace_or_token():
    item = Item(payload={"note": "SENSITIVE"}, target_id="rec-1", trace=[{"x": "SENSITIVE"}])
    assert "SENSITIVE" not in repr(item)
    assert "SENSITIVE" not in repr(Ticket("SECRET", {"note": "SENSITIVE"}))
    assert "SECRET" not in repr(Ticket("SECRET", {}))


@pytest.mark.parametrize("target_id", ["", "  ", None, 5])
def test_item_needs_a_target_id(target_id):
    with pytest.raises(ValueError):
        Item(payload={}, target_id=target_id)


def test_item_payload_must_be_a_mapping():
    with pytest.raises(TypeError):
        Item(payload="text", target_id="rec-1")


@pytest.mark.parametrize("name", list(CONTEXT))
def test_every_identity_field_changes_the_fingerprint(name):
    changed = {**CONTEXT, name: "other"}
    assert OversightContext(**changed).fingerprint != OversightContext(**CONTEXT).fingerprint


def test_intended_use_does_not_change_the_fingerprint():
    base = OversightContext(**CONTEXT)
    reworded = OversightContext(**CONTEXT, intended_use="reworded")
    assert base.fingerprint == reworded.fingerprint


@pytest.mark.parametrize("name", list(CONTEXT))
def test_context_fields_are_required(name):
    with pytest.raises(ValueError):
        OversightContext(**{**CONTEXT, name: " "})


def test_intended_use_must_be_text():
    with pytest.raises(TypeError):
        OversightContext(**CONTEXT, intended_use=None)
