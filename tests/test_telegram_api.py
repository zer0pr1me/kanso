import asyncio
import socket

import pytest
from aiohttp import web

from kanso.telegram.api import Api, BotError


def with_telegram(answer, check):
    """Run `check(api, requests)` against a local server standing in for Telegram, where
    `answer(method, body)` gives each request's (status, json) answer."""

    async def main():
        requests = []

        async def handle(request):
            body = await request.json()
            requests.append((request.path, body))
            status, reply = answer(request.match_info["method"], body)
            return web.json_response(reply, status=status)

        app = web.Application()
        app.router.add_post("/bot{token}/{method}", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        api = Api("1:secret", f"http://127.0.0.1:{port}")
        try:
            await check(api, requests)
        finally:
            await api.close()
            await runner.cleanup()

    asyncio.run(main())


def test_a_call_posts_its_params_and_gives_the_result():
    async def check(api, requests):
        assert await api.call("sendMessage", chat_id=7, text="hi") == {"message_id": 1}
        assert requests == [("/bot1:secret/sendMessage", {"chat_id": 7, "text": "hi"})]
        await api.call("getMe")  # one session for every call
        assert len(requests) == 2

    with_telegram(lambda method, body: (200, {"ok": True, "result": {"message_id": 1}}), check)


@pytest.mark.parametrize(
    "status, reply, description, code",
    [
        (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}, "Unauthorized", 401),
        (403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}, "Forbidden: bot was blocked", 403),
        (502, "Bad gateway", "Telegram refused", 502),
        (200, {"ok": False}, "Telegram refused", 200),
    ],
)
def test_a_refusal_raises_its_description_and_code(status, reply, description, code):
    async def check(api, requests):
        with pytest.raises(BotError, match=description) as error:
            await api.call("getMe")
        assert error.value.code == code

    with_telegram(lambda method, body: (status, reply), check)


def test_an_unreachable_telegram_raises_without_a_code():
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]

    async def main():
        api = Api("1:secret", f"http://127.0.0.1:{port}")
        with pytest.raises(BotError, match="Can't reach Telegram") as error:
            await api.call("getMe")
        assert error.value.code is None
        await api.close()
        await api.close()  # twice is fine

    asyncio.run(main())
