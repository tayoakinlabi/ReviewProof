"""The report as one self-contained HTML page, written for a QA lead (step 2.7).

No scripts, no external files, and it prints. Every value from the databases or settings is
escaped.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from html import escape

from reviewproof.metrics.checks import CLASS_NAMES, Check, State, percent
from reviewproof.metrics.stats import Rate
from reviewproof.report.model import Group, QueueReport, Report, ReviewerResult
from reviewproof.store.eventlog import VerifyResult

_STYLE = """
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1a1f24;background:#fff;
max-width:60rem;margin:0 auto;padding:1.5rem}
h1{font-size:1.6rem;margin:0}
h2{font-size:1.25rem;margin:2.2rem 0 .3rem;border-top:3px solid #1a1f24;padding-top:.8rem}
h3{font-size:1rem;margin:1.5rem 0 .4rem}
.muted{color:#56606b}.small{font-size:.85rem}
.banner{border-left:6px solid;padding:.6rem .9rem;margin:.8rem 0}
.banner ul{margin:.3rem 0;padding-left:1.1rem}
.banner.ok{border-color:#1b7f4b;background:#eef8f2}
.banner.bad{border-color:#b3261e;background:#fdf0ef}
.banner.grey{border-color:#8a929b;background:#f3f4f5}
.tiles{display:flex;flex-wrap:wrap;gap:.7rem;margin:1rem 0}
.tile{flex:1 1 11rem;border:1px solid #cfd5db;border-radius:6px;padding:.6rem .8rem}
.tile.lead{flex-basis:16rem;border:2px solid #1a1f24}
.big{font-size:1.9rem;font-weight:650;line-height:1.2}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;margin:.3rem 0}
th,td{border-bottom:1px solid #dde1e5;padding:.3rem .5rem;text-align:left;vertical-align:top}
th{font-size:.85rem;color:#56606b}.n{text-align:right}
.tag{display:inline-block;border-radius:3px;padding:0 .35rem;font-size:.8rem;font-weight:600}
.tag.red{background:#b3261e;color:#fff}.tag.amber{background:#ffe9b0;color:#3d2f00}
.cols{columns:2 20rem;column-gap:2rem}.cols>div{break-inside:avoid}
dt{font-weight:600}dd{margin:0 0 .4rem}code{font-size:.8rem;word-break:break-all}
@page{margin:9mm}
@media print{
body{padding:0;font-size:8.8px;line-height:1.3;max-width:none}
h1{font-size:15px}h2{font-size:12px;margin:8px 0 2px;padding-top:4px;border-top-width:2px}
h3{font-size:10px;margin:6px 0 1px}p{margin:2px 0}
.small,th{font-size:8px}.banner{margin:4px 0;padding:3px 8px}.banner ul{margin:1px 0}
.tiles{margin:4px 0;gap:5px;flex-wrap:nowrap}.tile{flex-basis:0;padding:3px 6px}
.tile.lead{flex:1.4 1 0}.big{font-size:15px}
table{margin:1px 0}th,td{padding:0 4px}.tag{font-size:7.5px}code{font-size:7.5px}
.read dl{columns:2;column-gap:18px;margin:2px 0}.read dl>div{break-inside:avoid}dd{margin:0 0 2px}
.limits ul{margin:1px 0}
.tile,.banner,tr{break-inside:avoid}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def render(report: Report) -> str:
    period = (
        f"decisions since {report.since:%d %b %Y %H:%M} UTC"
        if report.since
        else "all recorded history"
    )
    names = ", ".join(queue.name for queue in report.queues)
    versions = (
        f'<p class="muted">AI system version: {escape(report.by_version[0].label)}</p>'
        if len(report.by_version) == 1
        else ""
    )
    anchored = (
        " It also matches the head hash exported earlier."
        if report.anchored
        else " It was not compared with an earlier exported copy, so a rebuilt log or removed "
        "latest entries would not show here."
    )
    parts = [
        _page_start("Review oversight report"),
        "<h1>Review oversight report</h1>",
        f'<p class="muted">Queue: {escape(names)} &middot; {escape(period)} &middot; made '
        f"{report.generated_at:%d %b %Y %H:%M} UTC</p>",
        versions,
        '<div class="banner ok"><strong>The evidence checks out.</strong> The review log is '
        f"unbroken and matches the item records.{anchored}</div>",
        *(_queue(queue) for queue in report.queues),
        _groups("By version of the AI system", report.by_version, report),
        _groups("By site", report.by_site, report),
        _how_to_read(),
        _footer(report),
        "</body></html>\n",
    ]
    return "\n".join(parts)


def render_failure(result: VerifyResult) -> str:
    """A report for evidence that did not verify: what failed, and no scores at all."""
    problems = [p for p in (result.chain_error, result.anchor_error) if p]
    problems += list(result.registry_errors[:20])
    return "\n".join(
        [
            _page_start("Review oversight report: evidence check failed"),
            "<h1>Review oversight report</h1>",
            '<div class="banner bad"><strong>The evidence did not pass its checks, so no '
            "results are shown.</strong><p>Records may have been changed, removed or damaged. "
            "Do not rely on earlier reports until this is explained. Keep copies of both "
            "databases as they are now, and tell whoever is responsible for data integrity."
            f"</p>{_list(problems)}</div>",
            "<p class='small muted'>Technical detail: run <code>rp verify</code>.</p>",
            "</body></html>\n",
        ]
    )


def _page_start(title: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head><body>"
    )


def _list(items: Iterable[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def _table(head: str, rows: Iterable[str]) -> str:
    return f'<div class="scroll"><table>{head}{"".join(rows)}</table></div>'


def _pairs(rows: Sequence[tuple[str, str]]) -> str:
    """A two-column table. Both columns must already be safe HTML."""
    return _table("", (f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in rows))


# ----------------------------------------------------------------- one queue


def _queue(queue: QueueReport) -> str:
    where = f" &middot; site {escape(queue.site)}" if queue.site else ""
    parts = [
        f"<h2>{escape(queue.name)}</h2>",
        f'<p class="muted">{escape(queue.task)}{where}</p>',
        _attention(queue.checks),
        _headline(queue),
        _classes(queue),
        _workload(queue),
    ]
    if queue.reviewers is not None:
        parts.append(_reviewers(queue, queue.reviewers))
    parts.append(_programme(queue))
    return "\n".join(parts)


def _attention(checks: Sequence[Check]) -> str:
    breaches = [c for c in checks if c.state is State.BREACH]
    unjudged = [c for c in checks if c.state is State.NOT_ENOUGH_DATA]
    fine = [c for c in checks if c.state is State.OK]
    parts = []
    if breaches:
        items = "".join(
            f"<li><strong>{escape(c.title)}.</strong> {escape(c.detail)}</li>" for c in breaches
        )
        parts.append(
            f'<div class="banner bad"><strong>Needs attention</strong><ul>{items}</ul></div>'
        )
    elif unjudged:
        parts.append(
            '<div class="banner grey"><strong>Nothing is outside its limit among the measures '
            "that can be judged.</strong></div>"
        )
    else:
        parts.append('<div class="banner ok"><strong>Nothing is outside its limit.</strong></div>')
    if unjudged:
        items = "".join(f"<li>{escape(c.title)}: {escape(c.detail)}</li>" for c in unjudged)
        parts.append(
            '<div class="banner grey"><strong>Not enough data to judge yet</strong> (this is '
            f"not a pass)<ul>{items}</ul></div>"
        )
    if fine:
        names = "; ".join(c.title for c in fine)
        parts.append(f'<p class="small muted">Within limits: {escape(names)}.</p>')
    return "\n".join(parts)


def _share(rate: Rate) -> str:
    return "&ndash;" if rate.value is None else percent(rate.value)


def _range(rate: Rate) -> str:
    interval = rate.interval
    return "" if interval is None else f"{percent(interval[0])} to {percent(interval[1])}"


def _out_of(rate: Rate) -> str:
    return f"{rate.successes} of {rate.total}"


def _tile(title: str, value: str, lines: Sequence[str], lead: bool = False) -> str:
    detail = "".join(f'<div class="small muted">{line}</div>' for line in lines if line)
    return (
        f'<div class="tile{" lead" if lead else ""}"><div class="small">{title}</div>'
        f'<div class="big">{value}</div>{detail}</div>'
    )


def _dwell(milliseconds: float | None) -> str:
    if milliseconds is None:
        return "&ndash;"
    seconds = milliseconds / 1000
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


def _headline(queue: QueueReport) -> str:
    summary = queue.summary
    target = f"Target: at least {percent(queue.thresholds.catch_rate_min)}"
    if queue.withheld:
        catch = _tile(
            "Planted errors caught", "withheld", ["Too few for one person alone"], lead=True
        )
    else:
        rate = summary.catch
        lines = (
            [
                f"{_out_of(rate)} planted errors in this report",
                f"Likely true range: {_range(rate)}",
                target,
            ]
            if rate.total
            else ["No planted errors decided yet", target]
        )
        catch = _tile("Planted errors caught", _share(rate), lines, lead=True)
    fast, agreement = summary.fast, summary.agreement
    tiles = [
        catch,
        _tile(
            "Very fast approvals",
            _share(fast),
            [
                f"{_out_of(fast)} approvals under {queue.min_review_seconds:g} s",
                f"Likely range: {_range(fast)}" if fast.total else "",
            ],
        ),
        _tile(
            "Typical review time",
            _dwell(summary.dwell_ms),
            [f"Median over {summary.decided} items"],
        ),
        _tile(
            "Accepted unchanged",
            _share(agreement),
            [
                f"{_out_of(agreement)} real AI proposals",
                f"Likely range: {_range(agreement)}" if agreement.total else "",
            ],
        ),
    ]
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _classes(queue: QueueReport) -> str:
    if queue.withheld:
        return ""
    if not queue.summary.classes:
        return "<h3>By kind of error</h3><p class='muted'>No planted errors decided yet.</p>"
    limit = queue.thresholds.class_catch_rate_min
    rows = []
    for defect, rate in queue.summary.classes.items():
        if rate.total < queue.thresholds.min_sample:
            note = '<span class="small muted">too few to judge</span>'
        elif rate.value < limit:
            note = '<span class="tag red">below limit</span>'
        else:
            note = ""
        rows.append(
            f"<tr><td>{escape(CLASS_NAMES[defect.value].capitalize())}</td>"
            f'<td class="n">{_out_of(rate)}</td><td class="n">{_share(rate)}</td>'
            f'<td class="n">{_range(rate)}</td><td>{note}</td></tr>'
        )
    head = (
        "<tr><th>Kind</th><th class='n'>Caught</th><th class='n'>Rate</th>"
        "<th class='n'>Likely range</th><th></th></tr>"
    )
    return "<h3>By kind of error</h3>" + _table(head, rows)


def _split(rates: dict[str, Rate]) -> str:
    return "; ".join(f"{escape(name)}: {_share(r)} of {r.total}" for name, r in rates.items())


def _workload(queue: QueueReport) -> str:
    load, fatigue = queue.summary.load, queue.summary.fatigue
    if not load.reviewer_days:
        return "<h3>Workload and attention</h3><p class='muted'>No decisions yet.</p>"
    rows = [
        ("Reviewers who decided items", str(load.reviewers)),
        (
            "Items per reviewer per day",
            f"typically {load.median_per_day:g}, most {load.max_per_day}",
        ),
        (
            "Items per session",
            f"typically {load.median_per_session:g}, most {load.max_per_session} "
            f"({load.sessions} sessions)",
        ),
    ]
    if not queue.withheld and fatigue.early.total + fatigue.late.total:
        rows += [
            (
                "Planted errors caught early and late in a session",
                f"first half {_share(fatigue.early)} of {fatigue.early.total}; second half "
                f"{_share(fatigue.late)} of {fatigue.late.total}",
            ),
            ("By position in the session", _split(fatigue.by_position)),
            ("By hour of the session", _split(fatigue.by_hour)),
        ]
    return (
        "<h3>Workload and attention</h3>"
        '<p class="small muted">Read these before any individual result. A low catch rate is '
        "often caused by workload, the review screen or the design of the task.</p>" + _pairs(rows)
    )


def _flags(person: ReviewerResult) -> str:
    if not person.flags:
        return '<span class="muted">Nothing flagged</span>'
    return "<br>".join(
        f'<span class="tag {"amber" if flag.kind == "catch_below" else "red"}">'
        f"{escape(flag.text)}</span>"
        for flag in person.flags
    )


def _reviewers(queue: QueueReport, reviewers: Sequence[ReviewerResult]) -> str:
    minimum = queue.thresholds.individual_min_planted
    rows = []
    for person in reviewers:
        days = "day" if person.days == 1 else "days"
        load = f"{person.decided} items on {person.days} {days}; most in a day {person.max_per_day}"
        cells = f"<td>{escape(person.reviewer)}</td><td>{load}</td>"
        if person.summary is None:
            cells += (
                f'<td colspan="4" class="muted">Not shown: fewer than {minimum} planted errors '
                f"({person.planted} so far)</td>"
            )
        else:
            summary = person.summary
            cells += (
                f'<td class="n">{_share(summary.catch)} ({_out_of(summary.catch)})<br>'
                f'<span class="small muted">{_range(summary.catch)}</span></td>'
                f'<td class="n">{_share(summary.fast)}</td>'
                f'<td class="n">{_dwell(summary.dwell_ms)}</td>'
                f"<td>{_flags(person)}</td>"
            )
        rows.append(f"<tr>{cells}</tr>")
    head = (
        "<tr><th>Reviewer ID</th><th>Workload</th><th class='n'>Caught</th>"
        "<th class='n'>Very fast</th><th class='n'>Typical time</th><th>For QA to check</th></tr>"
    )
    return (
        "<h3>Individual reviewers</h3>"
        '<div class="banner grey"><strong>Confidential, for QA only.</strong> Use these for '
        "qualification, training and planning workload. Do not use them for discipline without "
        "investigating the cause. A flag is a reason to look, not a finding: check the "
        "workload beside it. With few planted errors, a careful reviewer can fall below target "
        "by chance.</div>" + _table(head, rows)
    )


def _programme(queue: QueueReport) -> str:
    health = queue.programme
    refused = sum(health.refusals.values())
    reasons = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(health.refusals.items()))
    rows = [
        ("Planted errors ready to use", str(health.usable_planted)),
        ("Approved but held back (no longer fit the item format)", str(health.held_back)),
        (
            "Times a planted error was due but none could be used",
            f"{health.shortfalls}" + (" &ndash; add more" if health.shortfalls else ""),
        ),
        ("Repeat sightings (not counted in any score)", str(health.repeats)),
        ("Planted errors shown but not yet decided", str(health.pending)),
        (
            "Execution refused for other reasons",
            f"{refused}" + (f" ({escape(reasons)})" if refused else ""),
        ),
        ("Real executions that failed", str(health.failed_executions)),
    ]
    lines = "".join(f"<div>{name}: <strong>{value}</strong></div>" for name, value in rows)
    return (
        "<h3>Health of the planted-error programme</h3>"
        f'<div class="cols">{lines}</div>'
        + "<p class='small muted'>Ready, held-back and shortage figures come from the "
        "restricted database and settings, not from the verified log.</p>"
    )


# ----------------------------------------------------------------- the rest


def _group_row(group: Group) -> str:
    summary = group.summary
    if group.withheld:
        caught = '<td class="n muted" colspan="2">withheld</td>'
    else:
        caught = (
            f'<td class="n">{_share(summary.catch)} ({_out_of(summary.catch)})</td>'
            f'<td class="n">{_range(summary.catch)}</td>'
        )
    return (
        f"<tr><td>{escape(group.label)}</td>{caught}"
        f'<td class="n">{_share(summary.fast)}</td><td class="n">{summary.decided}</td></tr>'
    )


def _groups(title: str, groups: Sequence[Group], report: Report) -> str:
    # With one queue and one group, the table would only repeat the figures above.
    if not groups or (len(groups) == 1 and len(report.queues) == 1):
        return ""
    head = (
        "<tr><th></th><th class='n'>Planted errors caught</th><th class='n'>Likely range</th>"
        "<th class='n'>Very fast approvals</th><th class='n'>Items decided</th></tr>"
    )
    return f"<h2>{escape(title)}</h2>" + _table(head, (_group_row(g) for g in groups))


def _how_to_read() -> str:
    terms = [
        (
            "Planted error",
            "An item written by your team with one known mistake, mixed into the real queue and "
            "never carried out.",
        ),
        (
            "Planted errors caught",
            "Of the planted errors reviewers decided on, the share they rejected, or edited in "
            "the field that was wrong. An edit is not checked for being right. Only the first "
            "time someone sees a given planted error counts.",
        ),
        (
            "Likely range (95% interval)",
            "With a limited number of examples the true figure could be higher or lower. It is "
            "very likely within this range. Few examples give a wide range.",
        ),
        (
            "Very fast approvals",
            "Approvals made in less than the queue's minimum review time, counted from when the "
            "item first appeared on the reviewer's screen to their decision.",
        ),
        (
            "Accepted unchanged",
            "Of real AI proposals, the share approved without edits. High agreement is not wrong "
            "in itself.",
        ),
        (
            "Session",
            "A run of one reviewer's decisions without a long pause. It stands in for a shift. "
            "Days are UTC.",
        ),
    ]
    items = "".join(f"<div><dt>{escape(t)}</dt><dd>{escape(d)}</dd></div>" for t, d in terms)
    return (
        f"<div class='read'><h2>How to read this report</h2><dl>{items}</dl>"
        "<p class='small muted'>These results show how review behaved on planted errors. They "
        "do not show that approved real items were correct.</p></div>"
    )


def _footer(report: Report) -> str:
    limits = []
    for queue in report.queues:
        t = queue.thresholds
        limits.append(
            f"{queue.name}: catch rate at least {percent(t.catch_rate_min)} over the latest "
            f"{t.rolling_window}; each kind at least {percent(t.class_catch_rate_min)}; very "
            f"fast approvals at most {percent(t.fast_approval_share_max)} in any week; "
            f"agreement above {percent(t.agreement_rate_max)} with a falling catch rate; "
            f"late-session drop under {percent(t.fatigue_drop_max)}; rules judged from "
            f"{t.min_sample} examples; individual results from {t.individual_min_planted}; "
            f"planted rate {queue.planted_rate * 100:g}%."
        )
    return (
        "<h2>Record of this report</h2>"
        f"<p class='small'>Log entries verified: {report.head.event_count}<br>"
        f"Head hash: <code>{escape(report.head.head_hash)}</code></p>"
        "<div class='limits small muted'>Limits used (proposed defaults unless your settings "
        "change "
        f"them):{_list(limits)}</div>"
        "<p class='small muted'>Sign this report in your quality system. ReviewProof is not "
        "pre-validated.</p>"
    )
