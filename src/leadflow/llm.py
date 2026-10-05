"""Single wrapper for every LLM call in the project.
Handles: retries/backoff on rate limits, structured output, a disk cache, and a fallback for schema errors.
Tracing and call limits get added here in Steps 12-13."""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from typing import Callable

from google import genai
from google.genai import errors, types

from . import config

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.require_api_key())
    return _client


def _gen_config(system: str, max_tokens: int, schema, mode: str) -> types.GenerateContentConfig:
    sys_text = system
    kwargs: dict = {}
    if schema is not None:
        kwargs["response_mime_type"] = "application/json"
        if mode == "native":
            kwargs["response_schema"] = schema
        else:  # fallback: describe the schema in the prompt instead of the API parameter
            sys_text += "\n\nReturn ONLY JSON matching this JSON Schema:\n" + json.dumps(schema.model_json_schema())
    return types.GenerateContentConfig(
        system_instruction=sys_text or None,
        max_output_tokens=max_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=0),  # 2.5 Flash otherwise spends tokens 'thinking'
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # we never pass tools
        **kwargs,
    )


def _call_model(prompt: str, system: str, max_tokens: int, schema, retries: int) -> dict:
    mode = "native" if schema is not None else "plain"
    delay, attempt = 4.0, 0
    while True:
        try:
            resp = client().models.generate_content(
                model=config.MODEL, contents=prompt, config=_gen_config(system, max_tokens, schema, mode))
            usage = resp.usage_metadata
            return {"text": resp.text or "",
                    "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                    "output_tokens": getattr(usage, "candidates_token_count", 0) or 0}
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code == 400 and mode == "native":      # SDK/model rejected the schema -> describe it in the prompt
                mode = "prompt"
                continue
            if code in (429, 500, 503) and attempt < retries:
                time.sleep(delay)
                delay *= 2
                attempt += 1
                continue
            raise


def _cache_key(prompt: str, system: str, max_tokens: int, schema) -> str:
    schema_sig = json.dumps(schema.model_json_schema(), sort_keys=True) if schema is not None else ""
    blob = json.dumps([config.MODEL, system, prompt, max_tokens, schema_sig], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def complete(prompt: str, system: str = "", max_tokens: int = 300, schema=None, retries: int = 5,
             cache: bool = True, accept: Callable[[str], bool] | None = None) -> dict:
    """Returns {'text', 'input_tokens', 'output_tokens', 'cached'}.

    schema:  a Pydantic model class -> JSON output constrained to it.
    cache:   identical (model, system, prompt, schema) calls are served from disk. Prompt or schema edits
             change the key automatically, so you never read stale results.
    accept:  optional validator; responses it rejects are NOT cached (so bad outputs never stick)."""
    use_cache = cache and config.USE_CACHE
    path = Path(config.CACHE_DIR) / f"{_cache_key(prompt, system, max_tokens, schema)}.json"
    if use_cache and path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        return {**d, "cached": True}
    out = _call_model(prompt, system, max_tokens, schema, retries)
    if use_cache and out["text"] and (accept is None or accept(out["text"])):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out), encoding="utf-8")
    return {**out, "cached": False}
