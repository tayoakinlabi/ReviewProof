"""Hand-made item records, so that each rule can be tested exactly at its limit."""

from datetime import datetime, timedelta, timezone
from itertools import count

from reviewproof.metrics.projection import ItemRecord, Showing
from reviewproof.models import Decision, DefectClass

QUEUE = "q"
MONDAY = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
_numbers = count(1)


def record(
    *,
    planted=False,
    caught=True,
    decision=None,
    reviewer="r-1",
    minute=None,
    at=None,
    dwell=60,
    defect=DefectClass.OMISSION,
    first_sight=True,
    queue=QUEUE,
):
    """One decided item. For a planted item, ``caught`` picks reject or approve."""
    number = next(_numbers)
    decided_at = at or MONDAY + timedelta(minutes=number if minute is None else minute)
    if decision is None:
        decision = (
            (Decision.REJECT if caught else Decision.APPROVE) if planted else (Decision.APPROVE)
        )
    return ItemRecord(
        item_id=f"item-{number}",
        queue=queue,
        system_id="sys",
        submitted_at=decided_at - timedelta(hours=1),
        planted_error_id=f"PE-{number}" if planted else None,
        defect_class=defect if planted else None,
        defect_field="classification" if planted else None,
        showings=(Showing(number, reviewer, decided_at - timedelta(seconds=dwell)),),
        decision=Decision(decision),
        reviewer=reviewer,
        decided_at=decided_at,
        decided_event=number,
        first_sight=first_sight if planted else None,
    )


def planted_run(outcomes, **arguments):
    """Planted items in order; True is a catch."""
    return [record(planted=True, caught=caught, **arguments) for caught in outcomes]
