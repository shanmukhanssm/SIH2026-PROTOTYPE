"""Feature 06 eval — vlm_inspect: layer-1 cases 4/4, endpoint MOCKED (zero network).

Covers the tool-registry.md `vlm_inspect` contract: scripted-JSON happy path,
corrective re-ask on invalid replies, transient-retry cap, stub determinism,
json-mode auto/off gating, and the no-endpoint / unreadable-snapshot error
verdicts. The endpoint fake is installed by monkeypatching the
`roadfix.tools.vlm._get_client` seam; openai exceptions are constructed
exactly as openai 3.x requires (httpx request/response objects).
"""

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from roadfix import config
from roadfix.state import Event
from roadfix.tools.vlm import InspectArgs, InspectResult, vlm_inspect

# --- endpoint fake (monkeypatches roadfix.tools.vlm._get_client) ---------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """Records create() kwargs; replays a script of contents / exceptions."""

    def __init__(self, script: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._script = list(script)

    def create(self, **kwargs: Any) -> Any:  # fake boundary, no strict typing needed
        self.calls.append(kwargs)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _FakeResponse(step)


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _install_fake(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> _FakeCompletions:
    """Route vlm_inspect to the fake endpoint; returns the recorded-calls handle."""
    completions = _FakeCompletions(script)
    monkeypatch.setattr("roadfix.tools.vlm._get_client", lambda: _FakeClient(completions))
    return completions


# --- builders -------------------------------------------------------------------


def _event(snapshot_path: str = "/nonexistent/missing.jpg", kind: str = "POTHOLE") -> Event:
    return Event(
        event_id=f"run1-{kind}-f00003-1",
        kind=kind,  # type: ignore[arg-type]
        frame_id="f00003",
        t_seconds=1.2,
        snapshot_path=snapshot_path,
        evidence={"consecutive_count": 2},
    )


def _verdict_content(**overrides: Any) -> str:  # test helper, kwargs are free-form
    payload: dict[str, Any] = {
        "confirmed": True,
        "severity": "medium",
        "confidence": 0.9,
        "reason": "Deep pothole visible in the bus lane.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _text_part(call: dict[str, Any]) -> str:
    parts = call["messages"][0]["content"]
    return next(part["text"] for part in parts if part["type"] == "text")


def _bad_request() -> openai.BadRequestError:
    request = httpx.Request("POST", "http://vlm.local/v1/chat/completions")
    return openai.BadRequestError(
        "response_format is not supported",
        response=httpx.Response(400, request=request),
        body=None,
    )


def _timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://vlm.local/v1"))


@pytest.fixture()
def vlm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real-endpoint mode with fake credentials, json mode auto, stub mode off."""
    monkeypatch.setenv("VLM_BASE_URL", "http://vlm.local/v1")
    monkeypatch.setenv("VLM_API_KEY", "test-key")
    monkeypatch.setenv("VLM_JSON_MODE", "auto")
    monkeypatch.delenv("ROADFIX_STUB_MODELS", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)


# --- layer-1 case 1: valid JSON endpoint -----------------------------------------


def test_valid_json_endpoint_returns_scripted_verdict(
    vlm_env: None, make_image: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = make_image("snap-valid.jpg")
    completions = _install_fake(monkeypatch, [_verdict_content()])

    result = vlm_inspect(InspectArgs(event=_event(snapshot_path=snapshot), attempt=0))

    assert result == InspectResult(
        confirmed=True,
        severity="medium",
        confidence=0.9,
        reason="Deep pothole visible in the bus lane.",
        verifier=config.vlm_model(),
    )
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert call["model"] == config.vlm_model()
    assert call["temperature"] == config.VLM_TEMPERATURE
    assert call["max_tokens"] == config.VLM_MAX_TOKENS
    assert call["response_format"] == {"type": "json_object"}
    parts = call["messages"][0]["content"]
    image_parts = [part for part in parts if part["type"] == "image_url"]
    text_parts = [part for part in parts if part["type"] == "text"]
    assert len(image_parts) == 1 and len(text_parts) == 1
    url = image_parts[0]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert len(url) > 100  # the real tiny jpg, actually encoded
    text = text_parts[0]["text"]
    assert "road-event inspector" in text  # INSPECTOR_V1 verbatim body present
    assert "Judge ONLY what is visible in the photo" in text
    assert '"confirmed": true/false' in text  # registry JSON line rendered single-braced
    assert "POTHOLE" in text  # kind filled from the event
    assert "consecutive_count" in text  # evidence filled from the event


# --- layer-1 case 2: malformed JSON endpoint --------------------------------------


def test_malformed_reply_corrective_reask_then_error_verdict(
    vlm_env: None, make_image: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    schema_break = json.dumps(
        {"confirmed": True, "severity": "extreme", "confidence": 5.0, "reason": "nonsense"}
    )
    completions = _install_fake(monkeypatch, ["not json at all", schema_break])
    event = _event(snapshot_path=make_image("snap-bad.jpg"))

    result = vlm_inspect(InspectArgs(event=event, attempt=0))

    assert len(completions.calls) == 2  # corrective re-ask happened, cap 2 total holds
    assert "road-event inspector" in _text_part(completions.calls[0])
    second_text = _text_part(completions.calls[1])
    assert "not valid JSON matching the schema" in second_text  # correction template used
    assert "Expecting value" in second_text  # parse error passed back field-specific
    assert "severity" in second_text and "confidence" in second_text  # pydantic field errors
    assert result.confirmed is False
    assert result.severity == "low"
    assert result.confidence == 0.0
    assert result.verifier == "error:invalid_json"
    assert result.reason  # short cause, no traceback


# --- layer-1 case 3: timeout endpoint ----------------------------------------------


def test_timeout_endpoint_two_attempts_then_error_verdict(
    vlm_env: None, make_image: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    completions = _install_fake(monkeypatch, [_timeout(), _timeout()])
    event = _event(snapshot_path=make_image("snap-timeout.jpg"))

    result = vlm_inspect(InspectArgs(event=event, attempt=0))

    assert len(completions.calls) == 2  # initial + 1 transient retry, then stop
    assert result.confirmed is False
    assert result.confidence == 0.0
    assert result.verifier == "error:timeout"


def test_rate_limit_endpoint_retries_once_then_error_verdict(
    vlm_env: None, make_image: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.Request("POST", "http://vlm.local/v1")
    too_many = openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )
    completions = _install_fake(monkeypatch, [too_many, _verdict_content()])
    event = _event(snapshot_path=make_image("snap-429.jpg"))

    result = vlm_inspect(InspectArgs(event=event, attempt=0))

    assert len(completions.calls) == 2
    assert result.confirmed is True
    assert result.verifier == config.vlm_model()  # transient retry recovered the call


# --- layer-1 case 4: stub mode ------------------------------------------------------


def test_stub_mode_deterministic_verdict_per_kind(
    stub_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    completions = _install_fake(monkeypatch, [])  # any call would pop from empty script
    expected = {
        "POTHOLE": (True, "medium", 0.85),
        "KIDS_CROSSING": (True, "high", 0.9),
        "TRAFFIC_JAM": (True, "low", 0.8),
    }
    for kind, (confirmed, severity, confidence) in expected.items():
        event = _event(snapshot_path="/nonexistent/no-read.jpg", kind=kind)
        first = vlm_inspect(InspectArgs(event=event, attempt=0))
        second = vlm_inspect(InspectArgs(event=event, attempt=1))
        got = (first.confirmed, first.severity, first.confidence)
        assert got == (confirmed, severity, confidence)
        assert first.verifier == "stub"
        assert kind in first.reason  # reason references the kind
        assert first == second  # deterministic: same event -> same verdict
    assert completions.calls == []  # no network, and the nonexistent path proves no file read


# --- json-mode gating ----------------------------------------------------------------


def test_json_mode_auto_falls_back_without_response_format(
    vlm_env: None, make_image: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback = _verdict_content(confirmed=False, severity="low", confidence=0.2)
    completions = _install_fake(monkeypatch, [_bad_request(), fallback])
    event = _event(snapshot_path=make_image("snap-fallback.jpg"))

    result = vlm_inspect(InspectArgs(event=event, attempt=0))

    assert len(completions.calls) == 2
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in completions.calls[1]  # fallback dropped the field
    assert result.confirmed is False
    assert result.verifier == config.vlm_model()


def test_json_mode_off_never_sends_response_format(
    vlm_env: None, make_image: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VLM_JSON_MODE", "off")
    completions = _install_fake(monkeypatch, [_verdict_content()])
    event = _event(snapshot_path=make_image("snap-off.jpg"))

    result = vlm_inspect(InspectArgs(event=event, attempt=0))

    assert "response_format" not in completions.calls[0]
    assert result.confirmed is True
    assert result.verifier == config.vlm_model()


# --- honest error verdicts ------------------------------------------------------------


def test_no_endpoint_configured_error_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROADFIX_STUB_MODELS", raising=False)
    monkeypatch.delenv("VLM_BASE_URL", raising=False)
    monkeypatch.delenv("VLM_API_KEY", raising=False)

    result = vlm_inspect(InspectArgs(event=_event(), attempt=0))

    assert result.verifier == "error:no_endpoint"
    assert result.confirmed is False
    assert result.confidence == 0.0


def test_unreadable_snapshot_error_verdict(vlm_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _install_fake(monkeypatch, [])
    event = _event(snapshot_path="/nonexistent/missing.jpg")

    result = vlm_inspect(InspectArgs(event=event, attempt=0))

    assert result.verifier == "error:snapshot_unreadable"
    assert result.confirmed is False
    assert result.confidence == 0.0
    assert completions.calls == []  # fails before any endpoint call
