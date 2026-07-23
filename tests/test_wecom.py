import asyncio
import base64
import hashlib
import json

import httpx
import pytest

from app.wecom import LightAgentNotifyClient, WeComClient, WeComError, WeComWebhookClient


def test_send_text_requests_token_and_posts_message_payload():
    asyncio.run(_send_text_requests_token_and_posts_message_payload())


async def _send_text_requests_token_and_posts_message_payload():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/gettoken"):
            assert request.url.params["corpid"] == "corp-id"
            assert request.url.params["corpsecret"] == "secret-value"
            return httpx.Response(200, json={"errcode": 0, "access_token": "token-1"})
        if request.url.path.endswith("/message/send"):
            body = json.loads(request.content.decode("utf-8"))
            assert request.url.params["access_token"] == "token-1"
            assert body == {
                "touser": "sqh",
                "msgtype": "text",
                "agentid": 1000001,
                "text": {"content": "示例甲 2025-09-16（08:00至16:00)是你的中班\n@示例甲"},
                "enable_duplicate_check": 1,
                "duplicate_check_interval": 1800,
            }
            return httpx.Response(200, json={"errcode": 0})
        raise AssertionError(request.url)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WeComClient(
        corp_id="corp-id",
        corp_secret="secret-value",
        agent_id=1000001,
        http_client=http_client,
    )

    await client.send_text("sqh", "示例甲 2025-09-16（08:00至16:00)是你的中班\n@示例甲")
    await http_client.aclose()

    assert len(requests) == 2


def test_token_error_does_not_include_secret():
    asyncio.run(_token_error_does_not_include_secret())


async def _token_error_does_not_include_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 40013, "errmsg": "invalid corpid"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WeComClient(
        corp_id="corp-id",
        corp_secret="secret-value",
        agent_id=1000001,
        http_client=http_client,
    )

    with pytest.raises(WeComError) as error:
        await client.send_text("sqh", "test")
    await http_client.aclose()

    assert "secret-value" not in str(error.value)
    assert "invalid corpid" in str(error.value)


def test_webhook_text_mentions_configured_mobile():
    asyncio.run(_webhook_text_mentions_configured_mobile())


def test_webhook_image_posts_base64_and_md5_payload():
    asyncio.run(_webhook_image_posts_base64_and_md5_payload())


def test_lightagent_text_posts_gateway_payload_with_token():
    asyncio.run(_lightagent_text_posts_gateway_payload_with_token())


def test_wecom_clients_ignore_environment_proxy_by_default():
    app_client = WeComClient(corp_id="corp", corp_secret="secret", agent_id=1)
    webhook_client = WeComWebhookClient(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")

    assert app_client.http_client.trust_env is False
    assert webhook_client.http_client.trust_env is False


async def _webhook_text_mentions_configured_mobile():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "msgtype": "text",
            "text": {
                "content": "示例甲 2025-09-16（08:00至16:00)是你的中班",
                "mentioned_mobile_list": ["10000000000"],
            },
        }
        return httpx.Response(200, json={"errcode": 0})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WeComWebhookClient(
        webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test",
        http_client=http_client,
    )

    await client.send_text("示例甲 2025-09-16（08:00至16:00)是你的中班", ["10000000000"])
    await http_client.aclose()

    assert len(requests) == 1


async def _webhook_image_posts_base64_and_md5_payload():
    image_bytes = b"fake-png-bytes"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "msgtype": "image",
            "image": {
                "base64": base64.b64encode(image_bytes).decode("ascii"),
                "md5": hashlib.md5(image_bytes).hexdigest(),
            },
        }
        return httpx.Response(200, json={"errcode": 0})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WeComWebhookClient(
        webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test",
        http_client=http_client,
    )

    await client.send_image(image_bytes)
    await http_client.aclose()

    assert len(requests) == 1


async def _lightagent_text_posts_gateway_payload_with_token():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer push-token"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "channel": "wechat_group",
            "target": "room-1",
            "msgtype": "text",
            "text": {
                "content": "提醒内容",
                "mention_ids": ["@wechat-member-1"],
            },
        }
        return httpx.Response(200, json={"success": True})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LightAgentNotifyClient(
        endpoint_url="https://lightagent.test/api/push/send",
        target="room-1",
        token="push-token",
        http_client=http_client,
    )

    await client.send_text("提醒内容", ["@wechat-member-1"])
    await http_client.aclose()

    assert len(requests) == 1


def test_lightagent_text_uses_runtime_member_ids_as_mentions():
    asyncio.run(_lightagent_text_uses_runtime_member_ids_as_mentions())


async def _lightagent_text_uses_runtime_member_ids_as_mentions():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content.decode("utf-8"))
        assert body["text"] == {
            "content": "提醒内容",
            "mention_ids": ["wxid_member_1"],
        }
        assert "mentioned_mobile_list" not in body["text"]
        return httpx.Response(200, json={"success": True})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LightAgentNotifyClient(
        endpoint_url="https://lightagent.test/api/push/send",
        target="room-1",
        token="push-token",
        http_client=http_client,
    )

    await client.send_text("提醒内容", ["wxid_member_1"])
    await http_client.aclose()

    assert len(requests) == 1


def test_webhook_non_json_response_raises_readable_error():
    asyncio.run(_webhook_non_json_response_raises_readable_error())


async def _webhook_non_json_response_raises_readable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = WeComWebhookClient(
        webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test",
        http_client=http_client,
    )

    with pytest.raises(WeComError) as error:
        await client.send_text("test", ["10000000000"])
    await http_client.aclose()

    assert "企业微信机器人返回异常" in str(error.value)


