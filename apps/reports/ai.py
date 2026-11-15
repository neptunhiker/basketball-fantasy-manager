"""The one place that talks to OpenAI.

Everything else in the app asks `generate` and gets a `Result` back, so the
provider could change without anything else noticing -- and tests replace
`generate` (and, as a safety net, block the client) so no test ever reaches the
network. See tests/conftest.py.

Uses the Responses API with structured outputs (`responses.parse` with a
Pydantic model), as checked against every approved model in phase 0 of
my_docs/AI_TRADE_REPORTS.md.
"""

from dataclasses import dataclass, field

from django.conf import settings
from openai import OpenAI, OpenAIError

# Room for reasoning and the answer together; OpenAI's reasoning guide
# recommends at least this much when starting out.
MAX_OUTPUT_TOKENS = 25_000


class AiError(Exception):
    """The call failed: no answer to use. The message is for staff."""

    def __init__(self, message, usage=None):
        super().__init__(message)
        # What the failed call still cost, when it got that far.
        self.usage = usage


@dataclass
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class Result:
    parsed: object
    usage: Usage = field(default_factory=Usage)
    response_id: str = ""


def generate(*, model, effort, preamble, shared_input, task, schema):
    """Ask `model` and return its answer parsed into `schema`.

    The developer message is `preamble` and `shared_input` -- identical for
    every call in a run, so it is cached -- and the user message is `task`.
    Raises `AiError` for a refusal, an answer cut off at the output limit, or
    any failure of the API itself.
    """
    if not settings.AI_BRIEFING_API_KEY:
        raise AiError("AI_BRIEFING_API_KEY is not set.")
    client = OpenAI(
        api_key=settings.AI_BRIEFING_API_KEY,
        timeout=settings.AI_BRIEFING_TIMEOUT,
        max_retries=1,
    )
    try:
        response = client.responses.parse(
            model=model,
            reasoning={"effort": effort},
            input=[
                {"role": "developer", "content": preamble + shared_input},
                {"role": "user", "content": task},
            ],
            text_format=schema,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
    except OpenAIError as error:
        raise AiError(f"{type(error).__name__}: {error}") from error

    usage = _usage(response)
    if response.status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) or "unknown"
        raise AiError(f"The answer was cut off ({reason}).", usage)
    refusal = _refusal(response)
    if refusal:
        raise AiError(f"The model declined: {refusal}", usage)
    if response.output_parsed is None:
        raise AiError("The answer could not be read.", usage)
    return Result(parsed=response.output_parsed, usage=usage, response_id=response.id)


def _usage(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return Usage()
    inputs = getattr(usage, "input_tokens_details", None)
    outputs = getattr(usage, "output_tokens_details", None)
    return Usage(
        input_tokens=usage.input_tokens or 0,
        cached_tokens=getattr(inputs, "cached_tokens", 0) or 0,
        cache_write_tokens=getattr(inputs, "cache_write_tokens", 0) or 0,
        output_tokens=usage.output_tokens or 0,
        reasoning_tokens=getattr(outputs, "reasoning_tokens", 0) or 0,
    )


def _refusal(response):
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return getattr(part, "refusal", "") or "no reason given"
    return ""
