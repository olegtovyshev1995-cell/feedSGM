"""Тесты обёртки ClaudeClient на подставном SDK (без сети и без пакета anthropic)."""

from __future__ import annotations

import pytest

from src.agent.claude_client import AgentError, ClaudeClient
from src.config import AgentConfig


# ── Подставной SDK ─────────────────────────────────────────────────
class Block:
    def __init__(self, type, text=None, content=None):
        self.type = type
        self.text = text
        self.content = content


class Resp:
    def __init__(self, content, stop_reason="end_turn", stop_details=None):
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


class FakeAnthropic:
    class APIStatusError(Exception):
        def __init__(self, message, status_code=500):
            super().__init__(message)
            self.message = message
            self.status_code = status_code

    class APIConnectionError(Exception):
        pass


def _client(responses, **cfg_over):
    cfg = AgentConfig(web_search=False, **cfg_over)
    cc = ClaudeClient(cfg, api_key="test-key")
    cc._client = FakeClient(responses)   # минуем ленивый импорт SDK
    cc._anthropic = FakeAnthropic
    return cc


# ── Тесты ──────────────────────────────────────────────────────────
def test_single_text_response():
    cc = _client([Resp([Block("text", "# Спека\n- Двигатель")])])
    assert cc.complete("sys", "user").strip().startswith("# Спека")


def test_pause_turn_then_finish_concatenates():
    cc = _client([
        Resp([Block("text", "часть1")], stop_reason="pause_turn"),
        Resp([Block("text", "часть2")], stop_reason="end_turn"),
    ])
    out = cc.complete("sys", "user")
    assert "часть1" in out and "часть2" in out
    assert len(cc._client.messages.calls) == 2  # был продолжен


def test_refusal_raises():
    details = type("D", (), {"category": "cyber"})()
    cc = _client([Resp([], stop_reason="refusal", stop_details=details)])
    with pytest.raises(AgentError) as e:
        cc.complete("sys", "user")
    assert "refusal" in str(e.value)


def test_websearch_error_block_is_tolerated():
    cc = _client([
        Resp([
            Block("web_search_tool_result", content={"error_code": "max_uses_exceeded"}),
            Block("text", "итоговый текст"),
        ])
    ])
    assert cc.complete("sys", "user").strip() == "итоговый текст"


def test_empty_text_raises():
    cc = _client([Resp([])])
    with pytest.raises(AgentError):
        cc.complete("sys", "user")


def test_api_status_error_wrapped():
    class Boom(FakeClient):
        pass

    cfg = AgentConfig(web_search=False)
    cc = ClaudeClient(cfg, api_key="k")
    cc._anthropic = FakeAnthropic

    class RaisingMessages:
        def create(self, **kw):
            raise FakeAnthropic.APIStatusError("boom", status_code=503)

    cc._client = type("C", (), {"messages": RaisingMessages()})()
    with pytest.raises(AgentError) as e:
        cc.complete("sys", "user")
    assert "503" in str(e.value)
