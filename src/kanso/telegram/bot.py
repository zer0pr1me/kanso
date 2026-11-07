"""The Telegram bot, a daemon feature: it sends the day's todos to its owner's chat every day,
and asks about yesterday's habits, with buttons to check, postpone and delete todos and to
mark habits; each press redraws its message in place.

Its config lives in this replica's meta, never synced, so only the device it was started on
runs it (Telegram lets one program at a time poll a bot). It answers only the configured chat;
to anyone else's /start it says which chat that is, which helps find one's chat ID.
"""

import asyncio
import html
import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from uuid import UUID

from kanso.core.models import HabitDay, Todo
from kanso.core.order import done_before, human_date, sections
from kanso.core.store import NotFound, Store
from kanso.node import Node
from kanso.telegram.api import POLL_TIMEOUT, Api, BotError

NAME, TITLE = "telegram-bot", "Telegram bot"
CONFIG, SENT = "telegram_bot", "telegram_bot_sent"
"""Meta keys: the config (token, chat, at), and the last day the daily message went out."""
STARTING = "starting"
SCHEDULE_CHECK = 30
"""Seconds between checks whether the daily message is due."""
RETRY, MAX_BACKOFF = 1, 60
"""Seconds before retrying Telegram at first, and at most."""
COMMANDS = [
    {"command": "today", "description": "Today's todos"},
    {"command": "habits", "description": "Yesterday's habits"},
]
NONE = "-"
"""Stands for no record in callback data."""
MAX_TODOS = 30
"""Todos listed at most: Telegram allows about 100 buttons per message, three per todo."""
TODO_MARKS = {False: "⬜", True: "✅"}
TOGGLES = {False: "✔️", True: "↩️"}
"""The check button of an open todo, and of a done one."""
HABIT_MARKS = {"success": ("✓", "✅"), "failure": ("✗", "❌")}
"""Each state's button: as it reads unset, and set."""


def config(store: Store) -> dict | None:
    saved = store.meta(CONFIG)
    return json.loads(saved) if saved else None


def configure(store: Store, token: str, chat: int, at: str):
    store.set_meta(CONFIG, json.dumps({"token": token, "chat": chat, "at": at}))


def unconfigure(store: Store) -> bool:
    """Remove the config; whether there was one."""
    had = config(store) is not None
    store.set_meta(CONFIG, None)
    return had


# Messages: (text, reply_markup) for a day


def heading(day: date) -> str:
    """Bold, as messages are HTML."""
    return f"<b>{human_date(day)} · {day:%a, %b} {day.day}</b>"


def keyboard(rows: list[list[dict]]) -> dict | None:
    return {"inline_keyboard": rows} if rows else None


def button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def todos_of(store: Store, day: date) -> list[tuple[str, list[Todo]]]:
    """Each list's todos for `day`: scheduled then, or earlier and still open (or done since)."""
    lists = []
    for todo_list in store.lists():
        due = [t for t in store.todos(todo_list.id) if t.scheduled_at and t.scheduled_at <= day and not done_before(t, day)]
        if due:
            lists.append((todo_list.name, [todo for _, group in sections(due) for todo in group]))
    return lists


def today_message(store: Store, day: date, confirm: UUID | None = None) -> tuple[str, dict | None]:
    """The day's todos, numbered, by list; each number's row of buttons checks or unchecks,
    postpones and deletes it, or, for the todo `confirm`, asks to confirm deleting it."""
    lines, rows, left = [heading(day)], [], 0
    for name, todos in todos_of(store, day):
        if len(rows) == MAX_TODOS:
            left += len(todos)
            continue
        lines.append(f"\n<b>📋 {html.escape(name)}</b>")
        for todo in todos:
            if len(rows) == MAX_TODOS:
                left += 1
                continue
            n, tail = len(rows) + 1, f"{todo.id} {day:%Y%m%d}"
            title = html.escape(todo.title)
            late = f" <i>· {human_date(todo.scheduled_at, day)}</i>" if todo.scheduled_at < day else ""
            lines.append(f"{n}. {TODO_MARKS[todo.done]} {f'<s>{title}</s>' if todo.done else title}{late}")
            if todo.id == confirm:
                rows.append([button(f"🗑 Delete {n}?", f"D {tail}"), button("Keep", f"n {NONE} {day:%Y%m%d}")])
            else:
                rows.append([button(f"{n} {TOGGLES[todo.done]}", f"t {tail} {int(not todo.done)}"), button(f"{n} ⏭", f"p {tail}"), button(f"{n} 🗑", f"d {tail}")])
    if left:
        lines.append(f"\n…and {left} more in kanso")
    return ("\n".join(lines) if rows else f"{heading(day)}\nNothing to do"), keyboard(rows)


def habit_days_of(store: Store, day: date) -> list[HabitDay]:
    return [d for todo_list in store.lists() for d in store.habit_days(todo_list.id) if d.day == day]


def habits_message(store: Store, day: date) -> tuple[str, dict | None]:
    rows = []
    for habit_day in habit_days_of(store, day):
        rows.append(
            [
                button(f"{marks[habit_day.state == state]} {habit_day.title}", f"h {habit_day.id} {day:%Y%m%d} {state}")
                for state, marks in HABIT_MARKS.items()
            ]
        )
    return f"<b>Habits</b> · {heading(day)}" + ("" if rows else "\nNo habits that day"), keyboard(rows)


HELP = (
    "I send you kanso's todos for the day at {at}, and ask how yesterday's habits went. "
    "Under each todo's number: ✔️ checks it (↩️ unchecks it), ⏭ moves it to the next day, "
    "🗑 deletes it. Tap ✓ or ✗ to mark a habit, again to unmark it.\n\n"
    "/today – today's todos\n/habits – yesterday's habits"
)


def parse(data: str) -> tuple[str, UUID | None, date, str | None]:
    """A button's callback data: its action, record, the day its message is of, and its
    argument; raises `ValueError` if it isn't one of ours."""
    kind, record, day, *arg = data.split()  # unpacking too few raises ValueError
    if len(arg) > 1:
        raise ValueError(data)
    return kind, None if record == NONE else UUID(record), datetime.strptime(day, "%Y%m%d").date(), (arg or [None])[0]


class Refused(Exception):
    """Telegram refused the token: retrying won't help."""


class Bot:
    def __init__(self, node: Node, config: dict, api: Api):
        self.node, self.store, self.api = node, node.store, api
        self.chat, self.at = int(config["chat"]), time.fromisoformat(config["at"])
        self.username: str | None = None
        self.trouble: str | None = None
        """Why Telegram can't be used now, if it can't."""
        self.chat_trouble: str | None = None
        """Why the last message to the chat failed, if it did."""
        self.now = datetime.now

    def status(self) -> dict:
        if problem := self.trouble or self.chat_trouble:
            return {"title": TITLE, "running": False, "note": problem}
        if self.username is None:
            return {"title": TITLE, "running": False, "note": STARTING}
        note = f"running as @{self.username}, sending to chat {self.chat} daily at {self.at:%H:%M}"
        return {"title": TITLE, "running": True, "note": note}

    async def run(self):
        """Serve until cancelled; returns early only if Telegram refuses the token."""
        try:
            me = await self.call("getMe")
            await self.call("setMyCommands", commands=COMMANDS)
            self.username = me.get("username", "")
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.poll())
                tasks.create_task(self.keep_schedule())
        except* Refused:
            self.trouble = "Telegram refused the token: check it with @BotFather"
        except* BotError as errors:
            self.trouble = f"Telegram refused: {errors.exceptions[0]}"

    async def call(self, method: str, **params):
        """`method`'s result, retrying while Telegram can't be reached; raises `Refused` for a
        bad token, `BotError` for other refusals."""
        backoff = RETRY
        while True:
            try:
                result = await self.api.call(method, **params)
            except BotError as error:
                if error.code in (401, 404):
                    raise Refused from None
                if error.code is not None and error.code < 500 and error.code not in (409, 429):
                    raise
                self.trouble = (
                    "another program uses this bot's token, retrying"
                    if error.code == 409
                    else f"{error}, retrying"
                    if error.code
                    else "can't reach Telegram, retrying"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue
            self.trouble = None
            return result

    async def send(self, text: str, markup: dict | None, chat: int | None = None) -> bool:
        """Send to the owner's chat (or `chat`); whether that worked, noting why not if it didn't."""
        try:
            await self.call(
                "sendMessage",
                chat_id=self.chat if chat is None else chat,
                text=text,
                parse_mode="HTML",
                **({"reply_markup": markup} if markup else {}),
            )
        except BotError as error:
            if chat is None:
                self.chat_trouble = f"can't send to chat {self.chat}: {error} (send /start to the bot first)"
            return False
        if chat is None:
            self.chat_trouble = None
        return True

    # Updates

    async def poll(self):
        offset = None
        while True:
            updates = await self.call("getUpdates", offset=offset, timeout=POLL_TIMEOUT, allowed_updates=["message", "callback_query"])
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await self.handle(update)
                except BotError:
                    pass  # one update Telegram refused to act on; the next ones may work

    async def handle(self, update: dict):
        if query := update.get("callback_query"):
            message = query.get("message") or {}
            if message.get("chat", {}).get("id") == self.chat:
                await self.press(query["id"], message["message_id"], str(query.get("data", "")))
        elif message := update.get("message"):
            chat = message.get("chat", {}).get("id")
            command = str(message.get("text", "")).split(maxsplit=1)[:1]
            command = command[0].split("@")[0] if command else ""
            if chat != self.chat:
                if command == "/start":
                    await self.send(f"This is a private kanso bot. This chat's ID is {chat}.", None, chat)
            elif command == "/today":
                await self.send(*today_message(self.store, self.now().date()))
            elif command == "/habits":
                await self.send(*habits_message(self.store, self.now().date() - timedelta(days=1)))
            else:
                await self.send(HELP.format(at=f"{self.at:%H:%M}"), None)

    async def press(self, query_id: str, message_id: int, data: str):
        """A button pressed: act on it, and show the message afresh."""
        answer, render, confirm = "", None, None
        try:
            kind, record, day, arg = parse(data)
            if record is None and kind != "n":
                raise ValueError(data)
            render = habits_message if kind == "h" else today_message
            match kind, arg:
                case "t", "0" | "1":
                    self.store.update_todo(record, done=arg == "1")
                case "p", None:
                    self.store.update_todo(record, scheduled_at=day + timedelta(days=1))
                    answer = "Moved to the next day"
                case "d", None:
                    confirm = record
                case "D", None:
                    self.store.delete_todo(record)
                    answer = "Deleted"
                case "n", None:
                    pass
                case "h", state if state in HABIT_MARKS:
                    habit_day = next((d for d in habit_days_of(self.store, day) if d.id == record), None)
                    if habit_day is None:
                        raise NotFound(record)
                    self.store.mark_habit_day(habit_day.id, None if habit_day.state == state else state)
                case _:
                    render = None
            if render and kind not in ("d", "n"):
                self.node.notify(True)
        except NotFound:
            answer = "It's gone: deleted, maybe on another device"
        except ValueError as error:  # bad button data, or marking a day not over yet
            answer = str(error) if render else ""
        if render:
            text, markup = render(self.store, day, confirm) if confirm else render(self.store, day)
            try:
                await self.call(
                    "editMessageText",
                    chat_id=self.chat,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=markup or {"inline_keyboard": []},
                )
            except BotError as error:
                if "not modified" not in str(error):
                    raise
        await self.call("answerCallbackQuery", callback_query_id=query_id, **({"text": answer} if answer else {}))

    # The daily message

    def due(self) -> date | None:
        """Today, if its daily message is due and not sent yet."""
        now = self.now()
        if now.time() >= self.at and self.store.meta(SENT) != now.date().isoformat():
            return now.date()
        return None

    async def keep_schedule(self):
        while True:
            if day := self.due():
                await self.send_day(day)
            await asyncio.sleep(SCHEDULE_CHECK)

    async def send_day(self, day: date):
        """Send `day`'s todos, and yesterday's habits if some are unmarked; then it's sent."""
        yesterday = day - timedelta(days=1)
        if not await self.send(*today_message(self.store, day)):
            return
        if any(d.state is None for d in habit_days_of(self.store, yesterday)):
            if not await self.send(*habits_message(self.store, yesterday)):
                return
        self.store.set_meta(SENT, day.isoformat())


class TelegramBot:
    """The feature in the daemon: runs the bot while it's configured, afresh on `reload`."""

    def __init__(self, node: Node, api: Callable[[str], Api] | None = None):
        """`api(token)` makes the client; Telegram's by default."""
        self.node, self.make_api = node, api or Api
        self.bot: Bot | None = None
        self.changed = asyncio.Event()

    def status(self) -> dict | None:
        return self.bot.status() if self.bot else None

    def reload(self):
        self.changed.set()

    async def run(self):
        while True:
            self.changed.clear()
            task = None
            if saved := config(self.node.store):
                api = self.make_api(saved["token"])
                self.bot = Bot(self.node, saved, api)
                task = asyncio.create_task(self.bot.run())
            try:
                await self.changed.wait()
            finally:
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await api.close()
                self.bot = None


async def serve(node: Node):
    """The daemon feature: the bot, in the daemon that owns the data dir."""
    if node.sync is None:
        await asyncio.Event().wait()  # another daemon runs it; this one is about to stop
    feature = node.features[NAME] = TelegramBot(node)
    try:
        await feature.run()
    finally:
        del node.features[NAME]
