"""The one module that talks to OpenAI, with a fake client: `apps.reports.ai`.

Never the network: tests/conftest.py blocks the real client for every test,
and these tests swap in a fake that answers the way the API does.
"""

from types import SimpleNamespace

import httpx
import openai
import pytest

from apps.reports import ai, schemas


def usage(**extra):
    return SimpleNamespace(
        input_tokens=5411,
        input_tokens_details=SimpleNamespace(cached_tokens=5408, cache_write_tokens=0),
        output_tokens=173,
        output_tokens_details=SimpleNamespace(reasoning_tokens=90),
        **extra,
    )


def response(parsed=None, status="completed", output=(), incomplete=None):
    return SimpleNamespace(
        id="resp_123",
        status=status,
        output_parsed=parsed,
        output=list(output),
        usage=usage(),
        incomplete_details=SimpleNamespace(reason=incomplete) if incomplete else None,
    )


class FakeClient:
    """Stands in for `openai.OpenAI`: remembers how it was built and asked."""

    built = []
    asked = []
    answer = None

    def __init__(self, **options):
        FakeClient.built.append(options)
        self.responses = SimpleNamespace(parse=self.parse)

    def parse(self, **request):
        FakeClient.asked.append(request)
        if isinstance(FakeClient.answer, Exception):
            raise FakeClient.answer
        return FakeClient.answer


@pytest.fixture
def client(monkeypatch, settings):
    FakeClient.built, FakeClient.asked, FakeClient.answer = [], [], None
    settings.AI_BRIEFING_TIMEOUT = 240
    monkeypatch.setattr(ai, "OpenAI", FakeClient)
    return FakeClient


def ask(**overrides):
    request = {
        "model": "gpt-6-luna",
        "effort": "low",
        "preamble": "Rules\n",
        "shared_input": "DATA",
        "task": "TASK",
        "schema": schemas.Briefing,
    }
    request.update(overrides)
    return ai.generate(**request)


BRIEFING = schemas.Briefing(headline="H", summary="S", items=[])


def test_tests_cannot_reach_openai():
    """The safety net in tests/conftest.py, untouched by a fake client."""
    with pytest.raises(RuntimeError, match="must not call the OpenAI API"):
        ask()


def test_the_request_puts_the_shared_prefix_first(client):
    client.answer = response(BRIEFING)
    result = ask()

    assert client.built == [{"api_key": "sk-test-not-a-real-key", "timeout": 240, "max_retries": 1}]
    (request,) = client.asked
    assert request["model"] == "gpt-6-luna"
    assert request["reasoning"] == {"effort": "low"}
    assert request["text_format"] is schemas.Briefing
    assert request["max_output_tokens"] == ai.MAX_OUTPUT_TOKENS
    assert request["input"] == [
        {"role": "developer", "content": "Rules\nDATA"},
        {"role": "user", "content": "TASK"},
    ]
    assert result.parsed == BRIEFING
    assert result.response_id == "resp_123"
    assert result.usage == ai.Usage(
        input_tokens=5411,
        cached_tokens=5408,
        cache_write_tokens=0,
        output_tokens=173,
        reasoning_tokens=90,
    )


def test_a_refusal_is_an_error_that_still_reports_its_cost(client):
    refusal = SimpleNamespace(type="refusal", refusal="Not today.")
    client.answer = response(output=[SimpleNamespace(content=[refusal])])
    with pytest.raises(ai.AiError, match=r"declined: Not today\.") as caught:
        ask()
    assert caught.value.usage.output_tokens == 173


def test_an_answer_cut_off_at_the_limit_is_an_error(client):
    client.answer = response(status="incomplete", incomplete="max_output_tokens")
    with pytest.raises(ai.AiError, match="cut off \\(max_output_tokens\\)"):
        ask()


def test_an_unreadable_answer_is_an_error(client):
    client.answer = response(parsed=None)
    with pytest.raises(ai.AiError, match="could not be read"):
        ask()


def test_api_failures_become_errors(client):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    client.answer = openai.APIConnectionError(request=request)
    with pytest.raises(ai.AiError, match="APIConnectionError"):
        ask()


def test_no_key_no_call(client, settings):
    settings.AI_BRIEFING_API_KEY = ""
    with pytest.raises(ai.AiError, match="not set"):
        ask()
    assert client.built == []
