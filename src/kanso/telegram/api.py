"""The part of Telegram's Bot API the bot uses: https://core.telegram.org/bots/api"""

import asyncio

import aiohttp

POLL_TIMEOUT = 50
"""Seconds Telegram holds a `getUpdates` open while nothing comes in."""


class BotError(Exception):
    def __init__(self, description: str, code: int | None = None):
        """`code` is Telegram's error code, or `None` if Telegram couldn't be reached."""
        super().__init__(description)
        self.code = code


class Api:
    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self.url = f"{base}/bot{token}"
        self.session: aiohttp.ClientSession | None = None

    async def call(self, method: str, **params) -> object:
        """The `result` of `method`; raises `BotError` if Telegram refuses or can't be reached."""
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=POLL_TIMEOUT + 20))
        try:
            async with self.session.post(f"{self.url}/{method}", json=params) as response:
                answer = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise BotError(f"Can't reach Telegram: {error or type(error).__name__}") from None
        if not isinstance(answer, dict) or not answer.get("ok"):
            answer = answer if isinstance(answer, dict) else {}
            raise BotError(str(answer.get("description", "Telegram refused")), answer.get("error_code", response.status))
        return answer.get("result")

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
