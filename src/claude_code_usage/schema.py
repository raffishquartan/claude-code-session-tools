"""Record classification and schema validation.

Each `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` file mixes several
record types. We only bill on `type == "assistant"` records that carry a
`message.usage` block. This module is the single source of truth for
which records to keep and what shape they must have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


REQUIRED_TOP_LEVEL = {
    "type", "uuid", "sessionId", "timestamp", "cwd", "message",
}
KNOWN_TOP_LEVEL = REQUIRED_TOP_LEVEL | {
    "parentUuid", "isSidechain", "requestId", "userType", "entrypoint",
    "version", "gitBranch", "isMeta", "isCompactSummary",
}
REQUIRED_MESSAGE = {"model", "role", "type", "content", "usage"}
KNOWN_MESSAGE = REQUIRED_MESSAGE | {
    "id", "stop_reason", "stop_sequence", "stop_details",
}
REQUIRED_USAGE = {"input_tokens", "output_tokens"}
KNOWN_USAGE = REQUIRED_USAGE | {
    "cache_creation_input_tokens", "cache_read_input_tokens",
    "service_tier", "server_tool_use", "cache_creation",
    "inference_geo", "iterations", "speed",
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SchemaError(Exception):
    """Raised when a billable record fails validation."""


def is_billable_record(record: dict[str, Any]) -> bool:
    """Return True if `record` is an assistant message with a usage block.

    These are the only records that contribute to token / dollar totals.
    Everything else (user messages, system messages, queue operations,
    tool results) is ignored for billing purposes.
    """
    if record.get("type") != "assistant":
        return False
    message = record.get("message")
    if not isinstance(message, dict):
        return False
    usage = message.get("usage")
    return isinstance(usage, dict)


def validate_record(record: dict[str, Any]) -> ValidationResult:
    """Check a billable record against the expected schema.

    Errors are missing required fields. Warnings are unknown fields,
    which usually mean Anthropic added something new and we should look
    at it.
    """
    result = ValidationResult()
    for key in REQUIRED_TOP_LEVEL:
        if key not in record:
            result.errors.append(f"missing required top-level field: {key}")
    for key in record:
        if key not in KNOWN_TOP_LEVEL:
            result.warnings.append(f"unknown top-level field: {key}")
    message = record.get("message")
    if isinstance(message, dict):
        for key in REQUIRED_MESSAGE:
            if key not in message:
                result.errors.append(f"missing required message field: {key}")
        for key in message:
            if key not in KNOWN_MESSAGE:
                result.warnings.append(f"unknown message field: {key}")
        usage = message.get("usage")
        if isinstance(usage, dict):
            for key in REQUIRED_USAGE:
                if key not in usage:
                    result.errors.append(f"missing required usage field: {key}")
            for key in usage:
                if key not in KNOWN_USAGE:
                    result.warnings.append(f"unknown usage field: {key}")
    return result


def assert_billable(record: dict[str, Any]) -> None:
    """Raise `SchemaError` if `record` is not a valid billable record."""
    if not is_billable_record(record):
        raise SchemaError("record is not an assistant message with a usage block")
    result = validate_record(record)
    if result.errors:
        raise SchemaError("; ".join(result.errors))
