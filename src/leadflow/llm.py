"""Single wrapper for every LLM call in the project.
Tracing, call/cost limits and provider swaps all happen here (Steps 12-13)."""
import time
from google import genai
from google.genai import types, errors
from . import config

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.require_api_key())
    return _client


def complete(prompt: str, system: str = "", max_tokens: int = 300,
             schema=None, retries: int = 4) -> dict:
    """Returns {'text', 'parsed', 'input_tokens', 'output_tokens'}.
    Pass a Pydantic model as `schema` for structured output (used from Step 5).
    Retries with exponential backoff on rate limits (429) and server errors (5xx)."""
    cfg = types.GenerateContentConfig(
        system_instruction=system or None,
        max_output_tokens=max_tokens,
        # 2.5 Flash 'thinks' by default and spends tokens; turn off for simple steps
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json" if schema else None,
        response_schema=schema,
    )
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            resp = client().models.generate_content(
                model=config.MODEL, contents=prompt, config=cfg)
            usage = resp.usage_metadata
            return {
                "text": resp.text,
                "parsed": getattr(resp, "parsed", None),
                "input_tokens": getattr(usage, "prompt_token_count", 0),
                "output_tokens": getattr(usage, "candidates_token_count", 0),
            }
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (429, 500, 503) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
