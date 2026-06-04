import logging

import httpx
from config import LLM_API, OLLAMA_URL

logger = logging.getLogger(__name__)

_TIMEOUT = 60.0


def _openai_content(data: dict) -> str:
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise KeyError("choices[0].message.content is not a string")
    return content


async def _post(
    client: httpx.AsyncClient,
    url: str,
    *,
    path: str,
    model: str,
    **kwargs,
) -> httpx.Response:
    try:
        resp = await client.post(url, **kwargs)
        resp.raise_for_status()
        return resp
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:500]
        logger.error(
            "LLM request failed %s model=%s HTTP %s body=%r",
            path,
            model,
            e.response.status_code,
            body,
        )
        raise
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.error("LLM request failed %s model=%s url=%s: %s", path, model, url, e)
        raise


async def complete_json(system: str, user: str, *, model: str) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if LLM_API == "openai":
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "response_format": {"type": "json_object"},
            }
            url = f"{OLLAMA_URL}/chat/completions"
            resp = await _post(
                client, url, path="/chat/completions", model=model, json=payload,
            )
            return _openai_content(resp.json())

        payload = {
            "model": model,
            "system": system,
            "prompt": user,
            "stream": False,
            "format": "json",
        }
        url = f"{OLLAMA_URL}/api/generate"
        resp = await _post(client, url, path="/api/generate", model=model, json=payload)
        raw = resp.json()["response"]
        if not isinstance(raw, str):
            raise KeyError("response is not a string")
        return raw


async def complete_chat(
    messages: list[dict],
    *,
    model: str,
    max_tokens: int,
    stop: list[str],
) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if LLM_API == "openai":
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "max_tokens": max_tokens,
                "stop": stop,
            }
            url = f"{OLLAMA_URL}/chat/completions"
            resp = await _post(
                client, url, path="/chat/completions", model=model, json=payload,
            )
            return _openai_content(resp.json())

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "stop": stop,
            },
        }
        url = f"{OLLAMA_URL}/api/chat"
        resp = await _post(client, url, path="/api/chat", model=model, json=payload)
        content = resp.json()["message"]["content"]
        if not isinstance(content, str):
            raise KeyError("message.content is not a string")
        return content
