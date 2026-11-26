"""Step traces and review context (ADR-002).

A **trace** is the agent's record of how it produced an item. Only each step's ID, type, time and
salted hashes of its inputs and outputs are kept. A **review context** is the host's list of what
the reviewer's screen showed: names of sources, steps and fields, never their content.

Both are stored in the restricted database. Only real items have traces, so in ``oversight.db``
their presence would show which items are planted. The log carries a salted hash of each, for
every item alike.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from reviewproof.canonical import canonical_json, format_timestamp, sha256_hex
from reviewproof.errors import OversightError

#: Where OpenTelemetry's conventions for AI calls put a step's inputs and outputs. These names
#: have changed between versions: check them against your instrumentation.
_INPUT_ATTRIBUTES = ("gen_ai.input.messages", "gen_ai.tool.call.arguments")
_OUTPUT_ATTRIBUTES = ("gen_ai.output.messages", "gen_ai.tool.call.result")

CONTEXT_KEYS = ("sources", "steps", "fields")
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def trace_records(
    trace: Sequence[Mapping[str, Any]] | None, salt: bytes
) -> list[dict[str, str | None]]:
    """Reduce a host's trace to what is stored.

    Each step is a mapping with ``step_id`` and ``step_type``, and optionally ``inputs``,
    ``outputs`` and ``ts_utc``. An OpenTelemetry-style span also works: ``span_id``, ``name``,
    ``attributes`` and ``start_time_unix_nano``. Other keys are ignored.
    """
    if trace is None:
        return []
    if isinstance(trace, (str, bytes, Mapping)) or not isinstance(trace, Sequence):
        raise OversightError("trace must be a list of steps")
    records: dict[str, dict[str, str | None]] = {}
    for number, step in enumerate(trace):
        where = f"trace[{number}]"
        if not isinstance(step, Mapping):
            raise OversightError(f"{where} must be a mapping")
        attributes = step.get("attributes") or {}
        if not isinstance(attributes, Mapping):
            raise OversightError(f"{where}.attributes must be a mapping")
        context = step.get("context")
        span_id = context.get("span_id") if isinstance(context, Mapping) else None
        step_id = step.get("step_id") or step.get("span_id") or span_id
        step_type = (
            step.get("step_type") or attributes.get("gen_ai.operation.name") or step.get("name")
        )
        for name, value in (("step_id", step_id), ("step_type", step_type)):
            if not isinstance(value, str) or not value:
                raise OversightError(f"{where} needs a text {name}")
        if step_id in records:
            raise OversightError(f"{where}: step_id is used twice")
        records[step_id] = {
            "step_id": step_id,
            "step_type": step_type,
            "inputs_hash": _value_hash(step, attributes, "inputs", _INPUT_ATTRIBUTES, salt, where),
            "outputs_hash": _value_hash(
                step, attributes, "outputs", _OUTPUT_ATTRIBUTES, salt, where
            ),
            "ts_utc": _timestamp(step, where),
        }
    return list(records.values())


def trace_digest(records: Sequence[Mapping[str, Any]], salt: bytes) -> str:
    """One hash over an item's stored trace records, whatever order they are read in."""
    rows = (
        sorted(
            [r["step_id"], r["step_type"], r["inputs_hash"], r["outputs_hash"], r["ts_utc"]]
            for r in records
        )
        if records
        else []
    )
    return sha256_hex(canonical_json([salt.hex(), "trace", rows]))


def review_manifest(context: Mapping[str, Any] | None) -> dict[str, list[str]] | None:
    """Check a host's account of what was on screen."""
    if context is None:
        return None
    if not isinstance(context, Mapping):
        raise OversightError("context must be a mapping")
    unknown = sorted(str(key) for key in set(context) - set(CONTEXT_KEYS))
    if unknown:
        raise OversightError(
            f"context takes only {', '.join(CONTEXT_KEYS)}; unknown: {', '.join(unknown)}"
        )
    manifest = {}
    for key in CONTEXT_KEYS:
        names = context.get(key, [])
        if not isinstance(names, (list, tuple)) or any(
            not isinstance(name, str) or not name for name in names
        ):
            raise OversightError(f"context[{key!r}] must be a list of names")
        manifest[key] = list(names)
    return manifest


def context_digest(manifest: Mapping[str, list[str]] | None, salt: bytes) -> str:
    listed = None if manifest is None else [manifest[key] for key in CONTEXT_KEYS]
    return sha256_hex(canonical_json([salt.hex(), "context", listed]))


def _value_hash(
    step: Mapping[str, Any],
    attributes: Mapping[str, Any],
    key: str,
    attribute_names: tuple[str, ...],
    salt: bytes,
    where: str,
) -> str | None:
    if key in step:
        value = step[key]
    else:
        found = [attributes[name] for name in attribute_names if name in attributes]
        if not found:
            return None
        value = found[0]
    try:
        return sha256_hex(canonical_json([salt.hex(), "trace-value", value]))
    except (TypeError, ValueError, RecursionError) as error:
        raise OversightError(f"{where}.{key} may hold only JSON values: {error}") from error


def _timestamp(step: Mapping[str, Any], where: str) -> str | None:
    text = step.get("ts_utc")
    if text is not None:
        try:
            datetime.strptime(text, _TIMESTAMP_FORMAT)
        except (TypeError, ValueError):
            raise OversightError(f"{where}.ts_utc must look like {_TIMESTAMP_FORMAT}") from None
        if len(text) != 27:
            raise OversightError(f"{where}.ts_utc needs six decimal places")
        return text
    nanoseconds = step.get("start_time_unix_nano")
    if nanoseconds is None:
        return None
    try:
        seconds, rest = divmod(int(nanoseconds), 1_000_000_000)
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return format_timestamp(moment.replace(microsecond=rest // 1000))
    except (TypeError, ValueError, OverflowError, OSError):
        raise OversightError(f"{where}.start_time_unix_nano is not a time") from None
