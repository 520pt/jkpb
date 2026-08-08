from __future__ import annotations

import os

import httpx


TUNNEL_PROXY_ENV = "DUTY_REMINDER_TUNNEL_PROXY"


def configured_tunnel_proxy() -> str:
    return str(os.getenv(TUNNEL_PROXY_ENV) or "").strip()


def tunnel_httpx_client(*, timeout: int | float) -> httpx.Client:
    kwargs: dict[str, object] = {"timeout": timeout, "trust_env": False}
    proxy = configured_tunnel_proxy()
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def tunnel_async_httpx_client(*, timeout: int | float) -> httpx.AsyncClient:
    kwargs: dict[str, object] = {"timeout": timeout, "trust_env": False}
    proxy = configured_tunnel_proxy()
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)
