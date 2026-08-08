"""Tests for agents.notify.telegram_notifier (#1385 AC-D).

telegram_notifier is the injectable ``notifier`` callable orchestrator.dispatch
fires on a critical escalation. It must never raise — a missing config or a
failed send both degrade to a warning + ``False``, since this runs inside a
live wake_driver tick where an exception would abort otherwise-successful
event processing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agents.notify import telegram_notifier
from agents.orchestrator import Decision, Route


def _decision(**overrides) -> Decision:
    base = dict(
        route=Route.ESCALATE,
        event_type="security_alert",
        severity="critical",
        target="repo:Osasuwu/jarvis",
        idempotency_key="key-1",
        priority=10,
        escalated_reason="unknown event",
    )
    base.update(overrides)
    return Decision(**base)


def test_telegram_notifier_noops_when_token_unset(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOW_USER_ID", "12345")
    assert telegram_notifier(_decision()) is False


def test_telegram_notifier_noops_when_chat_id_unset(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_ALLOW_USER_ID", raising=False)
    assert telegram_notifier(_decision()) is False


def test_telegram_notifier_sends_when_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_ALLOW_USER_ID", "12345")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "application/json"}
    fake_response.json.return_value = {"ok": True}

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = fake_response

    with patch("agents.notify.httpx") as fake_httpx:
        fake_httpx.Client.return_value = fake_client
        assert telegram_notifier(_decision()) is True

    sent_url = fake_client.post.call_args.args[0]
    assert "tok" in sent_url
    sent_kwargs = fake_client.post.call_args.kwargs
    assert sent_kwargs["json"]["chat_id"] == "12345"
    assert "security_alert" in sent_kwargs["json"]["text"]


def test_telegram_notifier_returns_false_on_http_failure(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_ALLOW_USER_ID", "12345")

    fake_response = MagicMock()
    fake_response.status_code = 500
    fake_response.headers = {"content-type": "application/json"}
    fake_response.json.return_value = {"ok": False, "description": "boom"}
    fake_response.text = "boom"

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.post.return_value = fake_response

    with patch("agents.notify.httpx") as fake_httpx:
        fake_httpx.Client.return_value = fake_client
        assert telegram_notifier(_decision()) is False


def test_telegram_notifier_swallows_send_exception(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_ALLOW_USER_ID", "12345")

    with patch("agents.notify.httpx") as fake_httpx:
        fake_httpx.Client.side_effect = RuntimeError("network down")
        # Must not raise.
        assert telegram_notifier(_decision()) is False
