"""Optional proxy shim: exposes cloud-registered models via a unified
OpenAI-compatible endpoint.

By default the Orchestrator talks directly to the local ollama daemon at
http://127.0.0.1:11434/v1. If a cloud model (e.g. minimax-m2.7:cloud) is
registered only in ~/.opencode.json and NOT actually pulled into ollama,
requests for it will 404. In that case start this shim and point the
orchestrator at it instead:

    python -m klotho.proxy_shim --port 11435

    # then in models.toml:
    [orchestrator]
    base_url = "http://127.0.0.1:11435/v1"

The shim reads ~/.opencode.json to learn which model ids are "virtual" and
delegates requests for known-local models to ollama, while routing virtual
cloud models through the `opencode` CLI (subprocess) as a fallback.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import uvicorn

from . import config

OLLAMA_V1 = "http://127.0.0.1:11434/v1"

app = FastAPI(title="klotho-proxy-shim")


def _cloud_models() -> set[str]:
    return set(config.load_opencode_models())


async def _passthrough(request: Request, suffix: str) -> Any:
    body = await request.body()
    headers = {
        "Content-Type": "application/json",
        "Authorization": request.headers.get("Authorization", "Bearer ollama"),
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.request(
            request.method,
            f"{OLLAMA_V1}/{suffix}",
            content=body,
            headers=headers,
        )
    return JSONResponse(
        content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        status_code=resp.status_code,
    )


async def _opencode_fallback(model: str, messages: list[dict]) -> dict:
    """Route a chat completion through `opencode` CLI as a last resort."""
    opencode = shutil.which("opencode")
    if not opencode:
        raise RuntimeError("opencode binary not found on PATH")
    prompt = "\n\n".join(
        (m.get("content", "") for m in messages if m.get("role") in ("user", "system"))
    )
    proc = await asyncio.create_subprocess_exec(
        opencode, "run", "--model", model, prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    text = stdout.decode() if stdout else ""
    return {
        "id": "shim-fallback",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = json.loads(await request.body())
    model = body.get("model", "")
    cloud = _cloud_models()
    # If model is a known cloud-only model (not local), try ollama first; on
    # failure fall back to opencode CLI.
    if model in cloud:
        try:
            return await _passthrough(request, "chat/completions")
        except Exception:
            return JSONResponse(
                content=await _opencode_fallback(model, body.get("messages", [])),
                status_code=200,
            )
    return await _passthrough(request, "chat/completions")


@app.get("/v1/models")
async def list_models(request: Request) -> Any:
    return await _passthrough(request, "models")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()