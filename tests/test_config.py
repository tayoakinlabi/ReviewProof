import copy
from pathlib import Path

import pytest
import yaml

from reviewproof.config import ConfigError, load_config, parse_config, risk_tier
from reviewproof.models import FeedbackTiming, RiskCategory

DEMO = Path(__file__).parents[1] / "examples" / "deviation_triage" / "reviewproof.yaml"


def valid():
    return {
        "systems": [
            {
                "name": "s",
                "model_id": "m",
                "model_version": "1.10",
                "prompt_version": "1",
                "tool_schema_hash": "t",
            }
        ],
        "queues": [
            {
                "name": "q",
                "system": "s",
                "task": "task",
                "vital_base_risk": "A5",
                "min_review_seconds": 30,
                "feedback": "immediate",
                "item_schema": "item.schema.json",
            }
        ],
    }


def parse(data):
    return parse_config(data, base_dir=DEMO.parent)


@pytest.mark.parametrize(
    ("base_risk", "category", "rate"),
    [
        ("A1", None, 0.01),
        ("A4", None, 0.01),
        ("A5", None, 0.02),
        ("A6", None, 0.02),
        ("A7", None, 0.03),
        ("A9", None, 0.03),
        ("A4", RiskCategory.HIGH, 0.02),
        ("A6", RiskCategory.HIGH, 0.03),
        ("A7", RiskCategory.HIGH, 0.03),
        ("A4", RiskCategory.MEDIUM, 0.01),
        ("A4", RiskCategory.LOW, 0.01),
    ],
)
def test_risk_tier_defaults(base_risk, category, rate):
    data = valid()
    data["queues"][0]["vital_base_risk"] = base_risk
    if category:
        data["queues"][0]["vital_adjusted_category"] = category.value
    assert parse(data).queues["q"].planted_rate == rate


#: ADR-007's table: the base risks of each row, and the share with no, Low or Moderate
#: category, with High, and with Very High or Critical.
SHARES = [
    (["A1", "A1.5", "A2", "A2.5", "A3", "A3.5", "A4"], (0.01, 0.02, 0.03)),
    (["A4.5", "A5", "A5.5", "A6"], (0.02, 0.03, 0.03)),
    (["A6.5", "A7", "A7.5", "A8", "A8.5", "A9", "A9.5", "A10"], (0.03, 0.03, 0.03)),
]
CATEGORY_WORDS = [(None, "low", "moderate", "medium"), ("high",), ("very_high", "critical")]


@pytest.mark.parametrize(
    ("base_risk", "category", "rate"),
    [
        (base_risk, category, rate)
        for base_risks, rates in SHARES
        for base_risk in base_risks
        for words, rate in zip(CATEGORY_WORDS, rates, strict=True)
        for category in words
    ],
)
def test_every_vital_rating_has_the_share_in_adr_007(base_risk, category, rate):
    data = valid()
    data["queues"][0]["vital_base_risk"] = base_risk
    if category:
        data["queues"][0]["vital_adjusted_category"] = category
    assert parse(data).queues["q"].planted_rate == rate


def test_moderate_is_another_word_for_medium():
    data = valid()
    data["queues"][0]["vital_adjusted_category"] = "moderate"
    assert parse(data).queues["q"].vital_adjusted_category is RiskCategory.MEDIUM
    assert RiskCategory.MODERATE is RiskCategory.MEDIUM
    assert RiskCategory.MODERATE.value == "medium"  # what is stored


@pytest.mark.req("REQ-044")
@pytest.mark.parametrize(
    "base_risk",
    [
        "A0",
        "A0.5",
        "A10.5",
        "A11",
        "A55",
        "A99",
        "A5.0",
        "A5.25",
        "A5.55",
        "A05",
        "a5",
        "5",
        "A5,5",
        " A5",
        "A5 ",
        "A5\n",
        5.5,
        None,
    ],
)
def test_a_base_risk_that_is_not_on_vitals_ladder_is_refused(base_risk):
    data = valid()
    data["queues"][0]["vital_base_risk"] = base_risk
    with pytest.raises(ConfigError, match="vital_base_risk must be from A1 to A10"):
        parse(data)


@pytest.mark.req("REQ-044")
@pytest.mark.parametrize("category", ["severe", "very high", "Very_High", "CRITICAL", "", 3])
def test_a_category_that_is_not_vitals_is_refused_and_the_choices_listed(category):
    data = valid()
    data["queues"][0]["vital_adjusted_category"] = category
    expected = r"low, moderate \(or medium\), high, very_high, critical"
    with pytest.raises(ConfigError, match=expected):
        parse(data)


@pytest.mark.parametrize("base_risk", ["A0", "a5", "5", "A", "A100", " A5", None, 5])
def test_bad_base_risk(base_risk):
    with pytest.raises(ConfigError):
        risk_tier(base_risk)


def test_demo_config_loads():
    config = load_config(DEMO)
    queue = config.queues["deviation-triage"]
    assert queue.planted_rate == 0.02
    assert queue.feedback is FeedbackTiming.BATCHED
    assert queue.item_schema.is_file()
    assert queue.system in config.systems


def test_defaults():
    config = parse(valid())
    queue = config.queues["q"]
    assert queue.gxp is True
    assert queue.site is None
    assert queue.vital_adjusted_category is None
    assert config.reserved_target_prefix == "rp-reserved-"


def test_rate_can_be_overridden():
    data = valid()
    data["queues"][0]["planted_rate"] = 0.2
    assert parse(data).queues["q"].planted_rate == 0.2


def test_loaded_config_is_read_only():
    config = parse(valid())
    with pytest.raises(TypeError):
        config.queues["other"] = config.queues["q"]


@pytest.mark.req("REQ-044")
@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("queues", "planted_rate", 0, "planted_rate"),
        ("queues", "planted_rate", 2, "planted_rate"),
        ("queues", "planted_rate", True, "planted_rate"),
        ("queues", "planted_rate", float("nan"), "planted_rate"),
        ("queues", "min_review_seconds", -1, "min_review_seconds"),
        ("queues", "min_review_seconds", "30", "min_review_seconds"),
        ("queues", "feedback", "later", "immediate, batched"),
        ("queues", "vital_adjusted_category", "severe", "very_high, critical"),
        ("queues", "vital_base_risk", "high", "vital_base_risk"),
        ("queues", "gxp", "yes", "gxp"),
        ("queues", "item_schema", "missing.json", "no such file"),
        ("queues", "system", "nope", "unknown system"),
        ("queues", "site", "", "site"),
        ("queues", "planted_rat", 0.02, "unknown key"),
        ("systems", "model_version", 1.1, "quote numbers and versions"),
        ("systems", "intended_use", 3, "intended_use"),
    ],
)
def test_invalid_values_are_rejected(section, key, value, message):
    data = valid()
    data[section][0][key] = value
    with pytest.raises(ConfigError, match=message):
        parse(data)


@pytest.mark.req("REQ-044")
@pytest.mark.parametrize(("section", "key"), [("queues", "feedback"), ("systems", "model_version")])
def test_missing_keys_are_rejected(section, key):
    data = valid()
    del data[section][0][key]
    with pytest.raises(ConfigError, match=f"missing key.*{key}"):
        parse(data)


@pytest.mark.parametrize("section", ["systems", "queues"])
def test_duplicate_names_are_rejected(section):
    data = valid()
    data[section].append(copy.deepcopy(data[section][0]))
    with pytest.raises(ConfigError, match="duplicate"):
        parse(data)


@pytest.mark.req("REQ-044")
@pytest.mark.parametrize("prefix", ["", "rp-", " rp-reserved-", 7])
def test_weak_reserved_prefix_is_rejected(prefix):
    data = {**valid(), "reserved_target_prefix": prefix}
    with pytest.raises(ConfigError, match="reserved_target_prefix"):
        parse(data)


@pytest.mark.parametrize("data", [None, [], "text", {"systems": [], "queues": []}])
def test_wrong_shapes_are_rejected(data):
    with pytest.raises(ConfigError):
        parse(data)


def test_entry_that_is_not_a_mapping_is_rejected():
    data = valid()
    data["queues"] = ["q"]
    with pytest.raises(ConfigError, match="must be a mapping"):
        parse(data)


def write(tmp_path, text):
    (tmp_path / "item.schema.json").write_text("{}", encoding="utf-8")
    path = tmp_path / "reviewproof.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repeated_yaml_key_is_rejected(tmp_path):
    text = yaml.safe_dump(valid(), sort_keys=False) + "reserved_target_prefix: aaaaaa\n" * 2
    with pytest.raises(ConfigError, match="repeated key"):
        load_config(write(tmp_path, text))


@pytest.mark.parametrize("text", ["systems: [", "? [a]\n: 1\n", "!!python/name:os.system ''"])
def test_bad_or_unsafe_yaml_is_rejected(tmp_path, text):
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path, text))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "absent.yaml")
