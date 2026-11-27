"""The report (step 2.7), and the evidence it is built from (step 2.1)."""

import csv
import dataclasses
import io
import json
import re
import shutil
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser

import golden_log
import pytest
from deploy import QUEUE, item, submit_until_pair
from helpers import triggers_removed

from reviewproof.metrics import EvidenceError, load
from reviewproof.metrics.checks import State
from reviewproof.report import ReportError, build, html, tables
from reviewproof.store import StoreError, open_oversight_db, open_planted_db
from reviewproof.store.eventlog import Head

NOW = datetime(2026, 3, 10, 12, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    return golden_log.build(tmp_path_factory.mktemp("golden-report"))


def make(golden, config=None, **arguments):
    projection = load(golden.db, golden.planted_db)
    return build(projection, config or golden.config, golden.planted_db, now=NOW, **arguments)


class Text(HTMLParser):
    """The words a reader sees."""

    def __init__(self, page):
        super().__init__()
        self.parts, self._hidden = [], False
        self.tags = []
        self.feed(page)
        self.text = " ".join(" ".join(self.parts).split())

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self._hidden = tag in ("style", "title") or self._hidden

    def handle_endtag(self, tag):
        if tag in ("style", "title"):
            self._hidden = False

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)


def rows(report):
    return list(csv.DictReader(io.StringIO(tables.render(report))))


# ----------------------------------------------------------------- contents


def test_the_page_says_what_needs_attention_and_what_cannot_be_judged(golden):
    text = Text(html.render(make(golden))).text
    assert "The evidence checks out." in text
    assert "Needs attention" in text
    assert "Of the latest 16 planted errors, 8 were caught (50%)" in text
    assert "week 10 of 2026: 36% of 14 approvals" in text
    assert "Not enough data to judge yet (this is not a pass)" in text
    assert "50% 8 of 16 planted errors in this report" in text
    assert "Likely true range: 28% to 72%" in text
    assert "38 s" in text  # the median of 37.5 s, rounded
    assert "Repeat sightings (not counted in any score): 1" in text
    assert "Planted errors shown but not yet decided: 1" in text
    assert "Omission 1 of 5 20%" in text
    assert "was not compared with an earlier exported copy" in text


def test_the_page_stands_alone(golden):
    page = html.render(make(golden, individuals=True))
    assert page.startswith("<!doctype html>")
    parsed = Text(page)
    assert not {"script", "img", "link", "iframe"} & set(parsed.tags)
    assert not re.search(r"(src|href)=|url\(|@import", page)


def test_no_reviewer_appears_unless_individual_results_are_asked_for(golden):
    ids = {r.reviewer for r in load(golden.db, golden.planted_db).records if r.reviewer}
    assert ids == {"A", "B"}
    report = make(golden)
    assert report.queues[0].reviewers is None
    text = Text(html.render(report)).text
    assert "Individual reviewers" not in text and "Reviewer ID" not in text
    assert not any(row["scope_type"] == "reviewer" for row in rows(report))


def test_individual_results_need_ten_planted_errors(golden):
    report = make(golden, individuals=True)
    first, second = report.queues[0].reviewers
    assert (first.reviewer, first.planted, first.decided) == ("A", 12, 24)
    assert [f.kind for f in first.flags] == ["catch_below", "fast_approvals"]
    assert (second.reviewer, second.planted, second.summary, second.flags) == ("B", 4, None, ())

    text = Text(html.render(report)).text
    assert "Confidential, for QA only." in text
    assert "58% (7 of 12)" in text
    assert "but this could be chance" in text
    assert "Not shown: fewer than 10 planted errors (4 so far)" in text
    assert "B 10 items on 1 day;" in text  # load is shown beside a hidden score
    assert "25% (1 of 4)" not in text

    of_b = [r for r in rows(report) if r["scope"] == "B"]
    assert {r["metric"] for r in of_b} == {
        "decided",
        "reviewer_days",
        "max_per_day",
        "catch_rate",
    }
    [hidden] = [r for r in of_b if r["metric"] == "catch_rate"]
    assert (hidden["value"], hidden["successes"], hidden["total"]) == ("", "", "4")


def test_nothing_in_a_report_names_an_item_or_a_planted_error(golden):
    report = make(golden, individuals=True)
    everything = html.render(report) + tables.render(report)
    assert not re.search(r"PE-\d\d|DEV-2025|QMS-|rp-reserved", everything)
    for item_id in golden.ids.values():
        assert item_id not in everything


def test_one_reviewers_group_score_is_withheld_like_an_individual_one(golden):
    since = datetime(2026, 3, 8, tzinfo=timezone.utc)  # only B worked after this
    report = make(golden, since=since)
    [queue] = report.queues
    assert queue.withheld and queue.summary.catch.total == 4
    states = {c.rule: c for c in queue.checks}
    for rule in ("catch_rate", "defect_class", "agreement", "fatigue"):
        assert states[rule].state is State.NOT_ENOUGH_DATA
        assert "Withheld" in states[rule].detail
    assert all(group.withheld for group in (*report.by_version, *report.by_site))
    text = Text(html.render(report)).text
    assert "Planted errors caught withheld" in text
    assert "25%" not in text and "1 of 4" not in text and "By kind of error" not in text
    catch = [r for r in rows(report) if r["metric"].startswith("catch_rate")]
    assert catch and all(r["value"] == "" and "withheld" in r["note"] for r in catch)


def test_since_keeps_only_later_decisions(golden):
    assert make(golden).queues[0].summary.decided == 34
    later = make(golden, since=datetime(2026, 3, 3, tzinfo=timezone.utc))
    assert later.queues[0].summary.decided == 20
    assert "decisions since 03 Mar 2026" in Text(html.render(later)).text
    future = make(golden, since=datetime(2027, 1, 1, tzinfo=timezone.utc))
    text = Text(html.render(future)).text
    assert "No decisions yet." in text and "No planted errors decided yet" in text


def test_groups_appear_only_when_there_is_something_to_compare(golden):
    report = make(golden)
    [version], [site] = report.by_version, report.by_site
    assert version.summary.catch == report.queues[0].summary.catch
    assert site.label == "demo-site"
    text = Text(html.render(report)).text
    assert "By site" not in text
    assert "AI system version: deviation-classifier" in text


def test_a_second_queue_brings_the_group_tables(golden):
    queue = golden.config.queues[QUEUE]
    second = dataclasses.replace(queue, name="second", site=None)
    config = dataclasses.replace(golden.config, queues={QUEUE: queue, "second": second})
    report = make(golden, config=config, queues=[QUEUE, "second"])
    text = Text(html.render(report)).text
    assert "By version of the AI system" in text and "By site" in text


def test_values_are_escaped_and_cannot_become_formulas(golden):
    queue = dataclasses.replace(
        golden.config.queues[QUEUE], task="<script>alert(1)</script>", site="=HYPERLINK(1)"
    )
    config = dataclasses.replace(golden.config, queues={QUEUE: queue})
    report = make(golden, config=config, individuals=True)
    hostile = dataclasses.replace(report.queues[0].reviewers[1], reviewer="<b>@x</b>")
    section = dataclasses.replace(report.queues[0], reviewers=(hostile,))
    report = dataclasses.replace(report, queues=(section,))
    page = html.render(report)
    assert "<script>" not in page and "<b>@x" not in page
    assert "&lt;script&gt;" in page and "&lt;b&gt;@x" in page
    assert [r["scope"] for r in rows(report) if r["scope_type"] == "site"][0] == ("'=HYPERLINK(1)")


def test_the_csv_matches_the_report(golden):
    found = {(r["scope_type"], r["scope"], r["metric"]): r for r in rows(make(golden))}
    catch = found["queue", QUEUE, "catch_rate"]
    assert (catch["successes"], catch["total"], catch["value"]) == ("8", "16", "0.5")
    assert (catch["ci_low"], catch["ci_high"]) == ("0.28", "0.72")
    assert found["queue", QUEUE, "check_catch_rate"]["value"] == "breach"
    assert found["queue", QUEUE, "programme_repeats"]["value"] == "1"
    assert found["report", "", "events_verified"]["note"].startswith("head ")


def test_bad_queue_requests(golden):
    with pytest.raises(ReportError, match="not in the settings: nope"):
        make(golden, queues=["nope"])
    # The log has a queue that these settings have dropped.
    other = dataclasses.replace(golden.config.queues[QUEUE], name="second")
    dropped = dataclasses.replace(golden.config, queues={"second": other})
    with pytest.raises(ReportError, match="the log has queue"):
        make(golden, config=dropped, queues=["second"])


# ----------------------------------------------------------------- evidence first


@pytest.fixture
def copy(golden, tmp_path):
    for path in (golden.db, golden.planted_db):
        shutil.copy(path, tmp_path / path.name)
    return dataclasses.replace(
        golden, db=tmp_path / "oversight.db", planted_db=tmp_path / "planted.db"
    )


def test_a_tampered_log_gives_no_scores(copy):
    with closing(open_oversight_db(copy.db)) as db, triggers_removed(db):
        db.execute("UPDATE events SET decision = 'reject' WHERE type = 'decided'")
    with pytest.raises(EvidenceError) as error:
        load(copy.db, copy.planted_db)
    page = html.render_failure(error.value.result)
    text = Text(page).text
    assert "so no results are shown" in text
    assert "%" not in text and "Planted errors caught" not in text


def test_a_relabelled_item_gives_no_scores(copy):
    with (
        closing(open_planted_db(copy.planted_db)) as db,
        triggers_removed(db, "item_registry"),
    ):
        db.execute(
            "UPDATE item_registry SET kind = 'real', planted_error_id = NULL, "
            "assigned_reviewer = NULL WHERE planted_error_id = 'PE-02'"
        )
    with pytest.raises(EvidenceError):
        load(copy.db, copy.planted_db)


def test_an_exported_head_is_checked(golden):
    head = load(golden.db, golden.planted_db).head
    assert load(golden.db, golden.planted_db, head).head == head
    with pytest.raises(EvidenceError):
        load(golden.db, golden.planted_db, Head(head.event_count + 5, head.head_hash))
    report = build(
        load(golden.db, golden.planted_db, head),
        golden.config,
        golden.planted_db,
        anchored=True,
        now=NOW,
    )
    assert "matches the head hash exported earlier" in Text(html.render(report)).text


def test_loading_changes_nothing(golden):
    before = {p.name: p.read_bytes() for p in (golden.db, golden.planted_db)}
    load(golden.db, golden.planted_db)
    make(golden)
    assert {p.name: p.read_bytes() for p in (golden.db, golden.planted_db)} == before


def test_unreadable_evidence_is_a_store_error(copy):
    with closing(open_planted_db(copy.planted_db)) as db:
        db.execute("DROP TABLE gate_shortfalls")
    with pytest.raises(StoreError, match="gate"):
        build(load(copy.db, copy.planted_db), copy.config, copy.planted_db)


# ----------------------------------------------------------------- the guard's events


def test_executions_and_refusals_reach_the_report(deployment, ov):
    real, planted = submit_until_pair(ov)
    for ticket in (real, planted):
        ov.shown(ticket.token, "r-1")
        ov.decided(ticket.token, "r-1", "approve")
    with pytest.raises(Exception, match="Nothing was executed"):
        ov.execute(real.token, {**real.payload, "description": "changed"}, lambda *a: None)

    def broken(target, payload):
        raise ConnectionError

    with pytest.raises(ConnectionError):
        ov.execute(real.token, real.payload, broken)
    ov.execute(planted.token, planted.payload, broken)
    [third, *_] = ov.submit(item(15))
    ov.shown(third.token, "r-1")
    ov.decided(third.token, "r-1", "approve")
    ov.execute(third.token, third.payload, lambda *a: None)

    projection = load(deployment.db, deployment.planted_db)
    records = [r for r in projection.records if r.decided]
    assert sum(r.executed for r in records) == 1
    assert sum(r.execute_failed for r in records) == 1
    [blocked] = [r for r in records if r.planted]
    assert blocked.blocked_reasons == ("planted",)
    assert [refusal.reason for refusal in projection.refusals] == ["payload_changed"]

    report = build(projection, deployment.config, deployment.planted_db)
    programme = report.queues[0].programme
    assert programme.refusals == {"payload_changed": 1}
    assert programme.failed_executions == 1
    assert "payload changed: 1" in html.render(report)


def test_a_planted_decision_counts_without_execute(deployment, ov):
    _, planted = submit_until_pair(ov)
    ov.shown(planted.token, "r-1")
    ov.decided(planted.token, "r-1", "approve")
    projection = load(deployment.db, deployment.planted_db)
    [record] = [r for r in projection.records if r.planted and r.decided]
    assert record.outcome.value == "miss" and record.first_sight


def test_seeing_it_first_elsewhere_makes_a_later_decision_a_repeat(make):
    deployment = make()
    ov = deployment.open()
    path = sorted((golden_log.DEMO / "planted").glob("*.yaml"))[0]
    imported = ov.planted.import_file(path)
    ov.planted.approve(imported.planted_error_id, "qa-1")
    ov.planted.approve(imported.planted_error_id, "qa-2")
    _, first = submit_until_pair(ov)
    ov.shown(first.token, "r-1")  # r-1 only looks; r-2 decides
    ov.shown(first.token, "r-2")
    ov.decided(first.token, "r-2", "reject")
    _, second = submit_until_pair(ov, start=8)
    ov.shown(second.token, "r-1")
    ov.decided(second.token, "r-1", "reject")
    records = [r for r in load(deployment.db, deployment.planted_db).records if r.planted]
    sight = {r.reviewer: r.first_sight for r in records if r.decided}
    assert sight == {"r-2": True, "r-1": False}


def test_a_report_on_an_empty_deployment(deployment):
    deployment.open()
    projection = load(deployment.db, deployment.planted_db)
    report = build(projection, deployment.config, deployment.planted_db)
    text = Text(html.render(report)).text
    assert "Nothing is outside its limit among the measures that can be judged." in text
    assert "No decisions yet." in text
    assert json.dumps(rows(report))
