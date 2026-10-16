"""The AI layer may only veto, and must veto whenever it is unsure."""
from types import SimpleNamespace

from bot.ai_filter import AIConfirmation


class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        create = self._create

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))
        self._response, self._error = response, error

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def reply(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


def make(client, headlines=("Fund sees record inflows",)):
    return AIConfirmation("claude-opus-5-5", lambda s: list(headlines), client=client)


def test_allow():
    v = make(FakeClient(reply('{"verdict": "allow", "reason": "Routine news."}'))).review("SPY", "BUY")
    assert v.allow and v.reason == "Routine news."


def test_veto():
    v = make(FakeClient(reply('{"verdict": "veto", "reason": "Trading halted."}'))).review("SPY", "BUY")
    assert not v.allow


def test_refusal_means_no_trade():
    v = make(FakeClient(SimpleNamespace(stop_reason="refusal", content=[]))).review("SPY", "BUY")
    assert not v.allow


def test_api_error_means_no_trade():
    v = make(FakeClient(error=TimeoutError("slow"))).review("SPY", "BUY")
    assert not v.allow


def test_garbage_output_means_no_trade():
    v = make(FakeClient(reply("not json"))).review("SPY", "BUY")
    assert not v.allow


def test_news_fetch_error_means_no_trade():
    def broken(symbol):
        raise ConnectionError("down")

    v = AIConfirmation("m", broken, client=FakeClient()).review("SPY", "BUY")
    assert not v.allow


def test_no_headlines_skips_the_model_and_allows():
    client = FakeClient()
    v = make(client, headlines=()).review("SPY", "BUY")
    assert v.allow and client.calls == []
