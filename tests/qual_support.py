"""A qualification pool, correct items and plans for the demo queue."""

import copy

from deploy import ITEMS, QUEUE

from reviewproof.qualification import Criteria, Plan

DEFECTS = ("wrong_classification", "policy_breach", "omission")


def defect_input(number, purpose="qualification"):
    """A distinct planted error; ``number`` picks its class and identifiers."""
    defect = DEFECTS[number % len(DEFECTS)]
    payload = copy.deepcopy(ITEMS[number % len(ITEMS)])
    payload["deviation_id"] = f"DEV-2024-{number:04d}"
    if defect == "wrong_classification":
        field = "classification"
        payload[field] = "minor" if payload[field] != "minor" else "major"
    elif defect == "policy_breach":
        field = "capa_required"
        payload[field] = not payload[field]
    else:
        field = "immediate_actions"
        payload[field] = payload[field][1:]
    return {
        "planted_error_id": f"{'Q' if purpose == 'qualification' else 'L'}-{number:03d}",
        "queue": QUEUE,
        "purpose": purpose,
        "defect_class": defect,
        "defect_field": field,
        "expected_decision": "reject",
        "author": "author-1",
        "payload": payload,
    }


def load_pool(ov, numbers, purpose="qualification"):
    ids = []
    for number in numbers:
        planted = ov.planted.import_data(defect_input(number, purpose))
        ov.planted.approve(planted.planted_error_id, "qa-1")
        ov.planted.approve(planted.planted_error_id, "qa-2")
        ids.append(planted.planted_error_id)
    return ids


def correct_items(count, start=0):
    return tuple(
        {**copy.deepcopy(ITEMS[n % len(ITEMS)]), "deviation_id": f"DEV-2023-{n:04d}"}
        for n in range(start, start + count)
    )


def plan(ids, count=8, min_caught=None, max_false_rejects=1, **changes):
    values = {
        "queue": QUEUE,
        "planted_errors": tuple(ids),
        "correct_items": correct_items(count),
        "criteria": Criteria(len(ids) - 2 if min_caught is None else min_caught, max_false_rejects),
    }
    return Plan(**{**values, **changes})


def is_correct(payload):
    return payload["deviation_id"].startswith("DEV-2023-")


def sit(qualification, session, catch=lambda payload: True, reject_correct=lambda p: False):
    """Answer every item: ``catch`` and ``reject_correct`` choose which to reject."""
    for item in session.items:
        judge = reject_correct if is_correct(item.payload) else catch
        qualification.answered(
            item.token, session.reviewer, "reject" if judge(item.payload) else "approve"
        )
    return qualification.finish(session.session_id)
