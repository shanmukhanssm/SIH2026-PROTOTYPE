"""vlm_inspect — the DOUBLE-CHECK inspector (context/tool-registry.md).

One OpenAI-compatible VLM call per event that "reads" the snapshot photo and
fills the fixed verdict form in strict JSON. Endpoint settings come from
roadfix.config readers (never inline env reads or model literals); the prompt
lives in roadfix.prompts.inspector. Stub mode (ROADFIX_STUB_MODELS=1) returns
a deterministic verdict from event.kind + evidence — no network, no file reads.

Error contract (never raises past the tool boundary): all retries exhausted /
no endpoint configured / unreadable snapshot -> error verdict with
`verifier="error:<code>"` (codes: no_endpoint, timeout, connection, rate_limit,
server, invalid_json, snapshot_unreadable, request_failed) and
confirmed=False / confidence=0.0, so the gate drops the event honestly.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, ValidationError

from roadfix.config import (
    VLM_MAX_TOKENS,
    VLM_TEMPERATURE,
    vlm_timeout_s,
    stub_models_enabled,
    vlm_api_key,
    vlm_base_url,
    vlm_json_mode,
    vlm_model,
)
from roadfix.prompts.inspector import CORRECTION_TEMPLATE, INSPECTOR_V1
from roadfix.state import Event

if TYPE_CHECKING:
    from openai import OpenAI
    from openai.types.chat import (
        ChatCompletionContentPartParam,
        ChatCompletionUserMessageParam,
    )

logger = logging.getLogger(__name__)

_MAX_MODEL_ATTEMPTS: int = 2  # cap: 2 total model attempts (tool-registry.md)
_MAX_REASON_CHARS: int = 200  # error-verdict causes stay short and greppable


class InspectArgs(BaseModel):
    """Args for vlm_inspect (tool-registry.md contract)."""

    event: Event
    attempt: int


class InspectResult(BaseModel):
    """One inspection verdict — consumed by the gate; error verdicts get dropped."""

    confirmed: bool
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    verifier: str  # model name or "stub" or "error:<code>"


class InspectorVerdict(BaseModel):
    """Schema the endpoint must return (prompt-registry.md inspector v1)."""

    confirmed: bool
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


# Stub mode: deterministic verdict per kind (prompt-registry severities), no network.
_STUB_VERDICTS: dict[str, tuple[bool, Literal["low", "medium", "high"], float]] = {
    "POTHOLE": (True, "medium", 0.85),
    "KIDS_CROSSING": (True, "high", 0.9),
    "TRAFFIC_JAM": (True, "low", 0.8),
}


class _EndpointError(Exception):
    """Terminal endpoint failure, translated to an error verdict at the boundary."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


# --- helpers ------------------------------------------------------------------


def _error_result(code: str, reason: str) -> InspectResult:
    """Honest drop: confidence 0.0 < GATE_PUBLISH so the gate rejects it."""
    return InspectResult(
        confirmed=False,
        severity="low",
        confidence=0.0,
        reason=reason[:_MAX_REASON_CHARS],
        verifier=f"error:{code}",
    )


def _evidence_text(event: Event) -> str:
    """Compact deterministic evidence rendering for the prompt / stub reason."""
    return json.dumps(event.evidence, sort_keys=True, default=str)


def _snapshot_data_url(snapshot_path: str) -> str:
    """Read the snapshot jpg and encode it as a base64 data URL (library-docs.md)."""
    raw = Path(snapshot_path).read_bytes()
    return f"data:image/jpeg;base64,{base64.b64encode(raw).decode('ascii')}"


def _get_client() -> OpenAI:
    """Build a fresh client per call — cheap, stateless. Tests monkeypatch this seam."""
    import openai  # lazy heavy import (code-standards.md)

    return openai.OpenAI(base_url=vlm_base_url(), api_key=vlm_api_key(), timeout=vlm_timeout_s())


def _user_message(data_url: str, text: str) -> ChatCompletionUserMessageParam:
    """user message = [image part, text part] (library-docs.md transport)."""
    parts: list[ChatCompletionContentPartParam] = [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": text},
    ]
    return {"role": "user", "content": parts}


# --- endpoint plumbing --------------------------------------------------------


def _raw_call(
    client: OpenAI,
    model_name: str,
    messages: list[ChatCompletionUserMessageParam],
    send_response_format: bool,
) -> str:
    """One endpoint call with configured sampling params; returns reply content."""
    if send_response_format:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=VLM_TEMPERATURE,
            max_tokens=VLM_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
    else:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=VLM_TEMPERATURE,
            max_tokens=VLM_MAX_TOKENS,
        )
    content = response.choices[0].message.content
    return content if content is not None else ""  # empty content -> parse failure path


def _transient_code(exc: Exception) -> str:
    """Greppable error code for one openai transient exception."""
    import openai  # lazy heavy import

    if isinstance(exc, openai.APITimeoutError):  # subclass of APIConnectionError — first
        return "timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "connection"
    if isinstance(exc, openai.RateLimitError):
        return "rate_limit"
    if isinstance(exc, openai.InternalServerError):
        return "server"
    return "request_failed"


def _chat_once(
    client: OpenAI,
    model_name: str,
    messages: list[ChatCompletionUserMessageParam],
    send_response_format: bool,
) -> tuple[str, bool]:
    """One logical model call: json-mode auto-fallback + 1 transient retry.

    Returns (reply_content, json_mode_still_ok); raises _EndpointError when a
    failure class is exhausted (tool-registry.md retry policy).
    """
    import openai  # lazy heavy import

    transient = (
        openai.APITimeoutError,
        openai.APIConnectionError,
        openai.RateLimitError,
        openai.InternalServerError,
    )
    try:
        return _raw_call(client, model_name, messages, send_response_format), True
    except openai.BadRequestError as exc:
        if not send_response_format:
            raise _EndpointError("request_failed", f"endpoint rejected request: {exc}") from exc
        # VLM_JSON_MODE=auto: same call once more WITHOUT response_format (library-docs.md).
        logger.info("[vlm_inspect] endpoint rejected response_format; retrying once without it")
        try:
            return _raw_call(client, model_name, messages, False), False
        except transient as retry_exc:
            raise _EndpointError(
                _transient_code(retry_exc),
                f"transient failure after json-mode fallback: {retry_exc}",
            ) from retry_exc
        except openai.BadRequestError as retry_exc:
            raise _EndpointError(
                "request_failed", f"endpoint rejected fallback request: {retry_exc}"
            ) from retry_exc
    except transient as exc:
        code = _transient_code(exc)
        logger.warning("[vlm_inspect] transient endpoint failure (%s); retrying once", code)
        try:
            return _raw_call(client, model_name, messages, send_response_format), True
        except transient as retry_exc:
            raise _EndpointError(
                _transient_code(retry_exc), f"endpoint unreachable after retry: {retry_exc}"
            ) from retry_exc
        except openai.BadRequestError as retry_exc:
            raise _EndpointError(
                "request_failed", f"endpoint rejected retried request: {retry_exc}"
            ) from retry_exc


def _validate_reply(content: str) -> InspectorVerdict | str:
    """Parse + validate the reply. Returns the verdict, or the field problems."""
    try:
        data: object = json.loads(content)
    except json.JSONDecodeError as exc:
        return f"reply is not valid JSON ({exc.msg} at line {exc.lineno} column {exc.colno})"
    if not isinstance(data, dict):
        return "reply is not a JSON object"
    try:
        return InspectorVerdict.model_validate(data)
    except ValidationError as exc:
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'value'}: {error['msg']}"
            for error in exc.errors()
        )


# --- main paths ---------------------------------------------------------------


def _stub_verdict(event: Event) -> InspectResult:
    """Deterministic verdict from event.kind + evidence — no network, no file reads."""
    confirmed, severity, confidence = _STUB_VERDICTS.get(event.kind, (False, "low", 0.0))
    if confirmed:
        logger.info("[vlm_inspect] stub verdict for %s: confirmed=%s", event.event_id, confirmed)
        return InspectResult(
            confirmed=True,
            severity=severity,
            confidence=confidence,
            reason=(
                f"Stub mode: {event.kind} confirmed from detector evidence "
                f"{_evidence_text(event)}; no vision endpoint consulted."
            ),
            verifier="stub",
        )
    logger.warning("[vlm_inspect] stub mode: no deterministic verdict for kind %s", event.kind)
    return InspectResult(
        confirmed=False,
        severity="low",
        confidence=0.0,
        reason=(
            f"Stub mode: {event.kind} has no deterministic stub verdict; "
            "treated as unconfirmed."
        ),
        verifier="stub",
    )


def _inspect_via_endpoint(event: Event, data_url: str) -> InspectResult:
    """Real VLM path: <=2 model attempts, json-mode fallback, 1 transient retry."""
    model_name = vlm_model()
    client = _get_client()
    send_response_format = vlm_json_mode() == "auto"
    field_errors = ""

    for model_attempt in range(_MAX_MODEL_ATTEMPTS):
        if model_attempt == 0:
            user_text = INSPECTOR_V1.format(kind=event.kind, evidence=_evidence_text(event))
        else:
            user_text = CORRECTION_TEMPLATE.format(field_errors=field_errors)
        messages = [_user_message(data_url, user_text)]
        try:
            content, send_response_format = _chat_once(
                client, model_name, messages, send_response_format
            )
        except _EndpointError as exc:
            logger.warning(
                "[vlm_inspect] event %s: endpoint error %s: %s",
                event.event_id,
                exc.code,
                exc.reason,
            )
            return _error_result(exc.code, exc.reason)

        outcome = _validate_reply(content)
        if isinstance(outcome, InspectorVerdict):
            logger.info(
                "[vlm_inspect] event %s: confirmed=%s severity=%s confidence=%.2f",
                event.event_id,
                outcome.confirmed,
                outcome.severity,
                outcome.confidence,
            )
            return InspectResult(
                confirmed=outcome.confirmed,
                severity=outcome.severity,
                confidence=outcome.confidence,
                reason=outcome.reason,
                verifier=model_name,
            )
        field_errors = outcome
        logger.warning(
            "[vlm_inspect] event %s: invalid reply on model attempt %d; re-asking with corrections",
            event.event_id,
            model_attempt + 1,
        )

    logger.warning(
        "[vlm_inspect] event %s: reply invalid after %d model attempts; error verdict",
        event.event_id,
        _MAX_MODEL_ATTEMPTS,
    )
    return _error_result(
        "invalid_json", f"endpoint did not return a valid verdict in {_MAX_MODEL_ATTEMPTS} attempts"
    )


def vlm_inspect(args: InspectArgs) -> InspectResult:
    """DOUBLE-CHECK — one VLM verdict for one event snapshot. Never raises."""
    event = args.event
    logger.debug(
        "[vlm_inspect] event=%s kind=%s attempt=%d", event.event_id, event.kind, args.attempt
    )

    if stub_models_enabled():
        return _stub_verdict(event)

    if not vlm_base_url() or not vlm_api_key():
        logger.warning("[vlm_inspect] no endpoint configured (VLM_BASE_URL/VLM_API_KEY)")
        return _error_result("no_endpoint", "no VLM endpoint configured (VLM_BASE_URL/VLM_API_KEY)")

    try:
        data_url = _snapshot_data_url(event.snapshot_path)
    except OSError as exc:
        logger.warning("[vlm_inspect] event %s: snapshot unreadable: %s", event.event_id, exc)
        return _error_result("snapshot_unreadable", f"snapshot unreadable: {event.snapshot_path}")

    try:
        return _inspect_via_endpoint(event, data_url)
    except Exception as exc:  # boundary — translate, never propagate (code-standards.md)
        logger.warning(
            "[vlm_inspect] event %s: unexpected failure: %s: %s",
            event.event_id,
            type(exc).__name__,
            exc,
        )
        return _error_result("request_failed", f"{type(exc).__name__}: {exc}")
