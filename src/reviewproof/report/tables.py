"""The report as CSV: one row per figure, for spreadsheets (step 2.7)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator

from reviewproof.metrics.measures import Summary
from reviewproof.metrics.stats import Rate
from reviewproof.report.model import Report, Voided

COLUMNS = (
    "scope_type",
    "scope",
    "queue",
    "metric",
    "successes",
    "total",
    "value",
    "ci_low",
    "ci_high",
    "note",
)

#: A spreadsheet treats a cell starting with one of these as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def render(report: Report) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in _rows(report):
        writer.writerow({key: _safe(value) for key, value in row.items()})
    return output.getvalue()


def _safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


_WITHHELD = "withheld: would identify one reviewer"


def _rate(metric: str, rate: Rate) -> dict:
    if rate.withheld:
        return {"metric": metric, "note": _WITHHELD}
    interval = rate.interval
    return {
        "metric": metric,
        "successes": rate.successes,
        "total": rate.total,
        "value": "" if rate.value is None else round(rate.value, 4),
        "ci_low": "" if interval is None else round(interval[0], 4),
        "ci_high": "" if interval is None else round(interval[1], 4),
    }


def _number(metric: str, value: float | None) -> dict:
    return {"metric": metric, "value": "" if value is None else value}


def _leaving_out(row: dict, voided: Voided | None) -> dict:
    """Say on the catch rate's own row that it leaves results out, in case it is copied."""
    if voided is None:
        return row
    note = f"leaves out {voided.results} voided result(s)"
    return {**row, "note": f"{row['note']}; {note}" if row.get("note") else note}


def _voided(voided: Voided | None) -> Iterator[dict]:
    if voided is None:
        return
    yield _number("voided_planted_errors", voided.planted_errors)
    yield _number("voided_results", voided.results)
    # successes are the catches left out; the rest of the total are misses.
    yield _rate("voided_results_caught", voided.left_out)
    yield _rate("catch_rate_with_voided", voided.with_them)
    for reason, count in voided.reasons.items():
        yield _number(f"voided_reason_{reason}", count)


def _summary(summary: Summary, voided: Voided | None) -> Iterator[dict]:
    yield _number("decided", summary.decided)
    yield _leaving_out(_rate("catch_rate", summary.catch), voided)
    yield from _voided(voided)
    if not summary.catch.withheld:
        for defect, rate in summary.classes.items():
            yield _rate(f"catch_rate_{defect.value}", rate)
        yield _rate("catch_rate_early_in_session", summary.fatigue.early)
        yield _rate("catch_rate_late_in_session", summary.fatigue.late)
        for label, rate in summary.fatigue.by_position.items():
            yield _rate(f"catch_rate_position_{label}", rate)
        for label, rate in summary.fatigue.by_hour.items():
            yield _rate(f"catch_rate_{label}", rate)
    yield _rate("fast_approval_share", summary.fast)
    if summary.dwell_withheld:
        yield {"metric": "median_dwell_ms", "note": _WITHHELD}
    else:
        yield _number("median_dwell_ms", summary.dwell_ms)
    yield _rate("agreement_rate", summary.agreement)
    for name, value in vars(summary.load).items():
        yield _number(f"load_{name}", value)


def _rows(report: Report) -> Iterator[dict]:
    yield {
        "scope_type": "report",
        "metric": "events_verified",
        "value": report.head.event_count,
        "note": f"head {report.head.head_hash}",
    }
    for queue in report.queues:
        base = {"scope_type": "queue", "scope": queue.name, "queue": queue.name}
        for row in _summary(queue.summary, queue.voided):
            yield {**base, **row}
        for check in queue.checks:
            yield {
                **base,
                "metric": f"check_{check.rule}",
                "value": check.state.value,
                "note": check.detail,
            }
        for name, value in vars(queue.programme).items():
            if name == "refusals":
                value = sum(value.values())
            yield {**base, **_number(f"programme_{name}", value)}
        for person in queue.reviewers or ():
            row_base = {"scope_type": "reviewer", "scope": person.reviewer, "queue": queue.name}
            yield {**row_base, **_number("decided", person.decided)}
            yield {**row_base, **_number("reviewer_days", person.days)}
            yield {**row_base, **_number("max_per_day", person.max_per_day)}
            for row in _voided(person.voided):
                yield {**row_base, **row}
            if person.summary is None:
                row = {
                    "metric": "catch_rate",
                    "total": person.planted,
                    "note": "not shown: too few planted errors",
                }
                yield {**row_base, **_leaving_out(row, person.voided)}
                continue
            catch = _rate("catch_rate", person.summary.catch)
            yield {**row_base, **_leaving_out(catch, person.voided)}
            for flag in person.flags_with_voided or ():
                yield {**row_base, "metric": f"flag_with_voided_{flag.kind}", "note": flag.text}
            yield {**row_base, **_rate("fast_approval_share", person.summary.fast)}
            yield {**row_base, **_number("median_dwell_ms", person.summary.dwell_ms)}
            yield {**row_base, **_rate("agreement_rate", person.summary.agreement)}
            for flag in person.flags:
                yield {**row_base, "metric": f"flag_{flag.kind}", "note": flag.text}
    for kind, groups in (("system_version", report.by_version), ("site", report.by_site)):
        for group in groups:
            for row in _summary(group.summary, group.voided):
                yield {"scope_type": kind, "scope": group.label, **row}
