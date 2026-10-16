"""The AI layer may only veto, and must veto whenever it is unsure."""
from types import SimpleNamespace

from bot.ai_filter import AIConfirmation


class FakeGroq:
    """Mimics groq.Groq().chat.completions.create."""

    def __init__(self, content=None, error=None):
        self.calls = []
        self._content, self._error = content, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def make(client, headlines=("Fund sees record inflows",)):
    return AIConfirmation("openai/gpt-oss-20b", lambda s: list(headlines), client=client)


def test_allow():
    client = FakeGroq('{"verdict": "allow", "reason": "Routine news."}')
    v = make(client).review("SPY", "BUY")
    assert v.allow and v.reason == "Routine news."
    fmt = client.calls[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True


def test_veto():
    v = make(FakeGroq('{"verdict": "veto", "reason": "Trading halted."}')).review("SPY", "BUY")
    assert not v.allow


def test_api_error_means_no_trade():
    v = make(FakeGroq(error=TimeoutError("slow"))).review("SPY", "BUY")
    assert not v.allow


def test_garbage_output_means_no_trade():
    assert not make(FakeGroq("not json")).review("SPY", "BUY").allow
    assert not make(FakeGroq(None)).review("SPY", "BUY").allow
    assert not make(FakeGroq('{"verdict": "maybe", "reason": ""}')).review("SPY", "BUY").allow


def test_news_fetch_error_means_no_trade():
    def broken(symbol):
        raise ConnectionError("down")

    v = AIConfirmation("m", broken, client=FakeGroq()).review("SPY", "BUY")
    assert not v.allow


def test_no_headlines_skips_the_model_and_allows():
    client = FakeGroq()
    v = make(client, headlines=()).review("SPY", "BUY")
    assert v.allow and client.calls == []
