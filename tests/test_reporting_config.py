"""Reporting limits and alert routes in the settings (step 2.5)."""

import pytest
from test_config import DEMO, valid

from reviewproof.config import ConfigError, Thresholds, parse_config


def parse(**extra):
    return parse_config({**valid(), **extra}, base_dir=DEMO.parent)


def test_defaults():
    config = parse()
    assert config.reporting == Thresholds()
    assert config.queues["q"].thresholds == Thresholds()
    assert config.alerts.emails == () and config.alerts.webhook is None


def test_global_limits_apply_and_a_queue_can_override_them():
    data = valid()
    data["reporting"] = {"catch_rate_min": 0.9, "rolling_window": 30}
    data["queues"][0]["thresholds"] = {"catch_rate_min": 0.7, "max_items_per_reviewer_per_day": 80}
    config = parse_config(data, base_dir=DEMO.parent)
    limits = config.queues["q"].thresholds
    assert (limits.catch_rate_min, limits.rolling_window) == (0.7, 30)
    assert limits.max_items_per_reviewer_per_day == 80
    assert config.reporting.catch_rate_min == 0.9


def test_the_individual_minimum_can_only_be_raised():
    assert parse(reporting={"individual_min_planted": 25}).reporting.individual_min_planted == 25
    with pytest.raises(ConfigError, match="cannot be below 10"):
        parse(reporting={"individual_min_planted": 9})
    data = valid()
    data["queues"][0]["thresholds"] = {"individual_min_planted": 1}
    with pytest.raises(ConfigError, match="cannot be below 10"):
        parse_config(data, base_dir=DEMO.parent)


def test_a_cap_can_be_removed():
    assert parse(reporting={"max_items_per_reviewer_per_day": None})


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"catch_rate_min": 80}, "share from 0 to 1"),
        ({"fast_approval_share_max": -0.1}, "share from 0 to 1"),
        ({"min_sample": 0}, "whole number"),
        ({"rolling_window": 2.5}, "whole number"),
        ({"session_gap_minutes": True}, "whole number"),
        ({"catch_rate": 0.5}, "unknown key"),
    ],
)
def test_bad_limits(limits, message):
    with pytest.raises(ConfigError, match=message):
        parse(reporting=limits)


SMTP = {"host": "smtp.example.com", "sender": "rp@example.com"}


def test_a_full_route():
    config = parse(
        alerts={
            "qa_lead": {"email": ["qa@example.com"], "webhook": "https://hooks.example.com/x"},
            "smtp": {**SMTP, "port": 25, "username_env": "U", "password_env": "P"},
        }
    )
    assert config.alerts.emails == ("qa@example.com",)
    assert config.alerts.smtp.port == 25 and config.alerts.smtp.starttls


@pytest.mark.parametrize(
    "address", ["http://localhost:8080/x", "http://127.0.0.1/x", "https://example.com/x"]
)
def test_allowed_webhooks(address):
    assert parse(alerts={"qa_lead": {"webhook": address}}).alerts.webhook == address


@pytest.mark.parametrize(
    ("alerts", "message"),
    [
        ({"line_manager": {}}, "unknown key"),  # there is no other kind of recipient
        ({"qa_lead": {"manager_email": []}}, "unknown key"),
        ({"qa_lead": {"email": "qa@example.com"}, "smtp": SMTP}, "list of addresses"),
        ({"qa_lead": {"email": ["nope"]}, "smtp": SMTP}, "not an email"),
        (
            {"qa_lead": {"email": ["a@b.com\r\nBcc: c@d.com"]}, "smtp": SMTP},
            "not an email",
        ),
        ({"qa_lead": {"email": ["qa@example.com"]}}, "smtp is needed"),
        ({"qa_lead": {"webhook": "http://example.com/x"}}, "https"),
        ({"qa_lead": {"webhook": "ftp://example.com"}}, "https"),
        ({"qa_lead": {"webhook": "https://"}}, "https"),
        ({"smtp": {"host": "h"}}, "missing key"),
        ({"smtp": {**SMTP, "sender": "me"}}, "not an email"),
        ({"smtp": {**SMTP, "port": 0}}, "port number"),
        ({"smtp": {**SMTP, "starttls": "yes"}}, "starttls"),
        ({"smtp": {**SMTP, "username_env": "U"}}, "both username_env"),
        ({"smtp": {**SMTP, "password": "hunter2"}}, "unknown key"),
        (
            {"smtp": {**SMTP, "starttls": False, "username_env": "U", "password_env": "P"}},
            "only sent with starttls",
        ),
    ],
)
def test_bad_routes(alerts, message):
    with pytest.raises(ConfigError, match=message):
        parse(alerts=alerts)
