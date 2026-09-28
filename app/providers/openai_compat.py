"""Minimal client for OpenAI-compatible chat-completions endpoints.

DeepSeek (and most other hosted LLMs) speak this protocol, so both the
text and vision providers share this one HTTP call.
"""

import httpx

from app.llm.base import LLMError

REQUEST_TIMEOUT_SECONDS = 60.0


async def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.0,
) -> str:
    """POST /chat/completions and return the assistant message text."""
    if not api_key:
        raise LLMError("LLM API key is not configured")
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            body = response.json()
        return body["choices"][0]["message"]["content"]
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"Unexpected LLM response shape: {exc}") from exc
