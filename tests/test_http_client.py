import app.http_client as http_client


def test_tunnel_proxy_ignores_global_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://global-proxy.invalid:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://global-proxy.invalid:8080")
    monkeypatch.delenv("DUTY_REMINDER_TUNNEL_PROXY", raising=False)

    assert http_client.configured_tunnel_proxy() == ""


def test_tunnel_proxy_uses_only_dedicated_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://global-proxy.invalid:8080")
    monkeypatch.setenv("DUTY_REMINDER_TUNNEL_PROXY", "socks5h://127.0.0.1:10808")

    assert http_client.configured_tunnel_proxy() == "socks5h://127.0.0.1:10808"


def test_tunnel_async_client_passes_dedicated_proxy(monkeypatch):
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("DUTY_REMINDER_TUNNEL_PROXY", "socks5h://127.0.0.1:10808")
    monkeypatch.setattr(http_client.httpx, "AsyncClient", FakeAsyncClient)

    http_client.tunnel_async_httpx_client(timeout=15)

    assert captured == {"timeout": 15, "trust_env": False, "proxy": "socks5h://127.0.0.1:10808"}
