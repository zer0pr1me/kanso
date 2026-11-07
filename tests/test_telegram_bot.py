import asyncio
from datetime import date, datetime, timedelta

import pytest

from conftest import eventually, sync_over
from kanso.node import Node
from kanso.telegram import bot as telegram
from kanso.telegram.api import BotError
from kanso.telegram.bot import SENT, Bot, Refused, TelegramBot, habits_message, today_message

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
CHAT = 42


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(telegram, "RETRY", 0)


class FakeApi:
    """Telegram as the bot sees it: records calls, raises the errors queued per method, and
    hands out updates as tests put them."""

    def __init__(self, token: str = "1:token"):
        self.token, self.calls, self.errors, self.closed = token, [], {}, False
        self.updates: asyncio.Queue = asyncio.Queue()

    async def call(self, method: str, **params):
        self.calls.append((method, params))
        if self.errors.get(method):
            raise self.errors[method].pop(0)
        match method:
            case "getMe":
                return {"username": "kanso_bot"}
            case "getUpdates":
                return [await self.updates.get()]
            case "sendMessage":
                return {"message_id": len(self.calls)}
        return True

    async def close(self):
        self.closed = True

    def sent(self, method: str = "sendMessage") -> list[dict]:
        return [params for name, params in self.calls if name == method]


@pytest.fixture
def node(store):
    return Node(store)


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def bot(node, api):
    return Bot(node, {"token": "1:token", "chat": CHAT, "at": "09:00"}, api)


def todo(store, todo_list, text, day, done=False, completed=None):
    item = store.add_todo(todo_list.id, text, scheduled_at=day)
    if done:
        item = store.update_todo(item.id, done=True)
        item.completed_at = completed or TODAY
        store._save(item)
    return item


def habit(store, todo_list, text, days_ago=2):
    """A habit started `days_ago`, with a day for each day since."""
    made = store.add_habit(todo_list.id, text)
    made.start = TODAY - timedelta(days=days_ago)
    store._save(made)
    store.spawn()
    return made


def buttons(markup) -> list[list[str]]:
    return [[b["text"] for b in row] for row in (markup or {}).get("inline_keyboard", [])]


def data(markup, text: str) -> str:
    return next(b["callback_data"] for row in markup["inline_keyboard"] for b in row if b["text"] == text)


def run(coroutine):
    return asyncio.run(coroutine)


# Messages


def day_heading(day: date) -> str:
    return f"<b>{telegram.human_date(day)} · {day:%a, %b} {day.day}</b>"


def test_today_lists_todays_and_older_open_todos_of_every_list(store, todo_list):
    todo(store, todo_list, "today", TODAY)
    todo(store, todo_list, "late", TODAY - timedelta(days=3))
    todo(store, todo_list, "done late, today", TODAY - timedelta(days=3), done=True)
    todo(store, todo_list, "done before", TODAY - timedelta(days=3), done=True, completed=YESTERDAY)
    todo(store, todo_list, "tomorrow", TODAY + timedelta(days=1))
    store.add_todo(todo_list.id, "no date", scheduled_at=None)
    work = store.add_list("Work & <play>")
    todo(store, work, "<b>bold</b> & co", TODAY)
    store.add_list("Empty")
    text, markup = today_message(store, TODAY)
    late = telegram.human_date(TODAY - timedelta(days=3))
    assert text.splitlines() == [
        day_heading(TODAY),
        "",
        "<b>📋 Inbox</b>",
        f"1. ✅ <s>done late, today</s> <i>· {late}</i>",
        f"2. ⬜ late <i>· {late}</i>",
        "3. ⬜ today",
        "",
        "<b>📋 Work &amp; &lt;play&gt;</b>",
        "4. ⬜ &lt;b&gt;bold&lt;/b&gt; &amp; co",
    ]
    assert buttons(markup) == [["1 ↩️", "1 ⏭", "1 🗑"], *[[f"{n} ✔️", f"{n} ⏭", f"{n} 🗑"] for n in (2, 3, 4)]]
    assert all(len(b["callback_data"].encode()) <= 64 for row in markup["inline_keyboard"] for b in row)


def test_a_day_with_nothing_to_do_says_so(store, todo_list):
    todo(store, todo_list, "tomorrow", TODAY + timedelta(days=1))
    assert today_message(store, TODAY) == (f"{day_heading(TODAY)}\nNothing to do", None)


def test_a_long_day_lists_what_fits(store, todo_list, monkeypatch):
    monkeypatch.setattr(telegram, "MAX_TODOS", 2)
    for text in ("a", "b", "c"):
        todo(store, todo_list, text, TODAY)
    todo(store, store.add_list("Work"), "d", TODAY)
    text, markup = today_message(store, TODAY)
    assert text.splitlines()[-3:] == ["2. ⬜ b", "", "…and 2 more in kanso"]
    assert len(markup["inline_keyboard"]) == 2


def test_habits_show_that_days_habits_with_their_marks(store, todo_list):
    run_ = habit(store, todo_list, "Run")
    habit(store, store.add_list("Home"), "Read")
    [yesterday] = [d for d in store.habit_days(todo_list.id) if d.day == YESTERDAY]
    store.mark_habit_day(yesterday.id, "success")
    text, markup = habits_message(store, YESTERDAY)
    assert text == f"<b>Habits</b> · {day_heading(YESTERDAY)}"
    assert buttons(markup) == [["✅ Run", "✗ Run"], ["✓ Read", "✗ Read"]]
    assert data(markup, "✅ Run") == f"h {yesterday.id} {YESTERDAY:%Y%m%d} success"
    assert run_.id == yesterday.habit_id


def test_a_day_without_habits_says_so(store, todo_list):
    assert habits_message(store, YESTERDAY) == (f"<b>Habits</b> · {day_heading(YESTERDAY)}\nNo habits that day", None)


# Buttons


def press(bot, api, markup, text: str, message_id: int = 5, chat: int = CHAT):
    query = {"id": "q", "data": data(markup, text), "message": {"message_id": message_id, "chat": {"id": chat}}}
    return bot.handle({"update_id": 1, "callback_query": query})


def edited(api) -> dict:
    """The message as the last edit left it."""
    return api.sent("editMessageText")[-1]


def answers(api) -> list[str | None]:
    return [a.get("text") for a in api.sent("answerCallbackQuery")]


def test_checking_a_todo_redraws_its_message(bot, api, store, todo_list):
    todo(store, todo_list, "milk", TODAY)
    changes = []
    bot.node.subscribe(changes.append)
    _, markup = today_message(store, TODAY)
    run(press(bot, api, markup, "1 ✔️"))
    assert store.todos(todo_list.id)[0].done and changes == [True]
    edit = edited(api)
    assert (edit["chat_id"], edit["message_id"], edit["parse_mode"]) == (CHAT, 5, "HTML")
    assert (edit["text"], edit["reply_markup"]) == today_message(store, TODAY)
    assert "<s>milk</s>" in edit["text"] and buttons(edit["reply_markup"]) == [["1 ↩️", "1 ⏭", "1 🗑"]]
    assert answers(api) == [None]
    run(press(bot, api, edit["reply_markup"], "1 ↩️"))
    assert not store.todos(todo_list.id)[0].done
    run(press(bot, api, edit["reply_markup"], "1 ↩️"))  # a stale button: the same again
    assert not store.todos(todo_list.id)[0].done


def test_postponing_a_todo_moves_it_to_the_next_day(bot, api, store, todo_list):
    item = todo(store, todo_list, "milk", TODAY - timedelta(days=3))
    todo(store, todo_list, "bread", TODAY)
    _, markup = today_message(store, TODAY)
    run(press(bot, api, markup, "1 ⏭"))
    assert store.todos(todo_list.id)[0].scheduled_at == TODAY + timedelta(days=1)
    assert "milk" not in edited(api)["text"] and buttons(edited(api)["reply_markup"]) == [["1 ✔️", "1 ⏭", "1 🗑"]]
    assert answers(api) == ["Moved to the next day"] and item.id


def test_deleting_a_todo_asks_first(bot, api, store, todo_list):
    todo(store, todo_list, "milk", TODAY)
    todo(store, todo_list, "bread", TODAY)
    changes = []
    bot.node.subscribe(changes.append)
    _, markup = today_message(store, TODAY)
    run(press(bot, api, markup, "2 🗑"))
    assert len(store.todos(todo_list.id)) == 2 and changes == []
    asking = edited(api)["reply_markup"]
    assert buttons(asking) == [["1 ✔️", "1 ⏭", "1 🗑"], ["🗑 Delete 2?", "Keep"]]
    run(press(bot, api, asking, "Keep"))
    assert edited(api)["reply_markup"] == markup and len(store.todos(todo_list.id)) == 2
    run(press(bot, api, asking, "🗑 Delete 2?"))
    assert [t.title for t in store.todos(todo_list.id)] == ["milk"] and changes == [True]
    assert buttons(edited(api)["reply_markup"]) == [["1 ✔️", "1 ⏭", "1 🗑"]]
    assert answers(api) == [None, None, "Deleted"]


def test_tapping_a_habit_marks_it_and_again_unmarks_it(bot, api, store, todo_list):
    habit(store, todo_list, "Run")
    _, markup = habits_message(store, YESTERDAY)
    run(press(bot, api, markup, "✓ Run"))
    assert [d.state for d in store.habit_days(todo_list.id) if d.day == YESTERDAY] == ["success"]
    assert edited(api)["parse_mode"] == "HTML" and buttons(edited(api)["reply_markup"]) == [["✅ Run", "✗ Run"]]
    run(press(bot, api, edited(api)["reply_markup"], "✗ Run"))
    assert buttons(edited(api)["reply_markup"]) == [["✓ Run", "❌ Run"]]
    run(press(bot, api, edited(api)["reply_markup"], "❌ Run"))
    assert [d.state for d in store.habit_days(todo_list.id) if d.day == YESTERDAY] == [None]


def test_a_record_deleted_meanwhile_is_reported_and_the_message_redrawn(bot, api, store, todo_list):
    item = todo(store, todo_list, "milk", TODAY)
    habit(store, todo_list, "Run")
    _, todos = today_message(store, TODAY)
    _, asking = today_message(store, TODAY, item.id)
    _, habits = habits_message(store, YESTERDAY)
    store.delete_todo(item.id)
    for day in store.habit_days(todo_list.id):
        store.delete_habit_day(day.id)
    for markup, label in [(todos, "1 ✔️"), (todos, "1 ⏭"), (asking, "🗑 Delete 1?"), (habits, "✓ Run")]:
        run(press(bot, api, markup, label))
    assert answers(api) == ["It's gone: deleted, maybe on another device"] * 4
    assert [e["reply_markup"] for e in api.sent("editMessageText")] == [{"inline_keyboard": []}] * 4


def test_todays_habits_cant_be_marked_yet(bot, api, store, todo_list):
    habit(store, todo_list, "Run")
    _, markup = habits_message(store, TODAY)
    run(press(bot, api, markup, "✓ Run"))
    assert answers(api) == ["Habits can only be marked once the day is over"]


def test_an_unchanged_message_is_fine_but_other_edit_failures_raise(bot, api, store, todo_list):
    todo(store, todo_list, "milk", TODAY)
    _, markup = today_message(store, TODAY)
    api.errors["editMessageText"] = [BotError("Bad Request: message is not modified", 400), BotError("Bad Request: message to edit not found", 400)]
    run(press(bot, api, markup, "1 ✔️"))
    assert len(answers(api)) == 1
    with pytest.raises(BotError, match="not found"):
        run(press(bot, api, markup, "1 ✔️"))


@pytest.mark.parametrize(
    "data",
    ["", "-", "x", "t nope 20260101 1", "t 1 2 3", "h x y z", "t - 20260101 1", "z 6f1c2a4e-9b0d-4f5e-8a37-2d9c1b7e4a60 20260101", "p 6f1c2a4e-9b0d-4f5e-8a37-2d9c1b7e4a60 20260101 1 2"],
)
def test_odd_buttons_are_just_answered(bot, api, data):
    update = {"update_id": 1, "callback_query": {"id": "q", "data": data, "message": {"message_id": 5, "chat": {"id": CHAT}}}}
    run(bot.handle(update))
    assert [name for name, _ in api.calls] == ["answerCallbackQuery"]


def test_other_chats_buttons_are_ignored(bot, api, store, todo_list):
    todo(store, todo_list, "milk", TODAY)
    _, markup = today_message(store, TODAY)
    for label in ("1 ✔️", "1 ⏭", "1 🗑"):
        run(press(bot, api, markup, label, chat=7))
    assert api.calls == [] and [(t.done, t.scheduled_at) for t in store.todos(todo_list.id)] == [(False, TODAY)]


# Commands


def message(text: str, chat: int = CHAT) -> dict:
    return {"update_id": 1, "message": {"text": text, "chat": {"id": chat}}}


def test_today_and_habits_send_their_messages(bot, api, store, todo_list):
    todo(store, todo_list, "milk", TODAY)
    habit(store, todo_list, "Run")
    run(bot.handle(message("/today")))
    run(bot.handle(message("/habits@kanso_bot")))
    today, habits = api.sent()
    text, markup = today_message(store, TODAY)
    assert today == {"chat_id": CHAT, "text": text, "parse_mode": "HTML", "reply_markup": markup}
    assert habits["text"].startswith("<b>Habits</b> · <b>Yesterday") and buttons(habits["reply_markup"]) == [["✓ Run", "✗ Run"]]


@pytest.mark.parametrize("text", ["/start", "/help", "hello", ""])
def test_anything_else_gets_the_help(bot, api, text):
    run(bot.handle(message(text)))
    [sent] = api.sent()
    assert sent["text"].startswith("I send you kanso's todos for the day at 09:00") and "reply_markup" not in sent


def test_strangers_learn_only_their_chat_id(bot, api):
    for text in ["/today", "/habits", "hello"]:
        run(bot.handle(message(text, chat=7)))
    assert api.calls == []
    run(bot.handle(message("/start", chat=7)))
    assert api.sent() == [{"chat_id": 7, "text": "This is a private kanso bot. This chat's ID is 7.", "parse_mode": "HTML"}]
    assert bot.status()["note"] == telegram.STARTING  # failing to reach a stranger isn't the bot's trouble
    api.errors["sendMessage"] = [BotError("Forbidden", 403)]
    run(bot.handle(message("/start", chat=7)))
    assert bot.chat_trouble is None


def test_polling_handles_each_update_once_and_survives_refusals(bot, api):
    async def main():
        api.updates.put_nowait({**message("/today"), "update_id": 10})
        api.updates.put_nowait({**message("/help"), "update_id": 11})
        api.errors["sendMessage"] = [BotError("Bad Request: chat not found", 400)]
        task = asyncio.create_task(bot.poll())
        await eventually(lambda: len(api.sent("getUpdates")) == 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        offsets = [p["offset"] for p in api.sent("getUpdates")]
        assert offsets == [None, 11, 12] and len(api.sent()) == 2
        assert api.sent("getUpdates")[0]["allowed_updates"] == ["message", "callback_query"]

    run(main())


# The daily message


def at(hour: int, minute: int = 0, day: date = TODAY):
    return lambda: datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


def test_the_daily_message_is_due_once_a_day_from_its_time(bot, store):
    bot.now = at(8, 59)
    assert bot.due() is None
    bot.now = at(9)
    assert bot.due() == TODAY
    store.set_meta(SENT, TODAY.isoformat())
    assert bot.due() is None
    bot.now = at(0, 0, TODAY + timedelta(days=1))
    assert bot.due() is None
    bot.now = at(23, 0, TODAY + timedelta(days=1))
    assert bot.due() == TODAY + timedelta(days=1)


def test_the_day_brings_todos_and_yesterdays_unmarked_habits(bot, api, store, todo_list):
    todo(store, todo_list, "milk", TODAY)
    habit(store, todo_list, "Run")
    run(bot.send_day(TODAY))
    assert [s["text"].split(" · ")[0] for s in api.sent()] == ["<b>Today", "<b>Habits</b>"]
    assert store.meta(SENT) == TODAY.isoformat()
    [yesterday] = [d for d in store.habit_days(todo_list.id) if d.day == YESTERDAY]
    store.mark_habit_day(yesterday.id, "failure")
    run(bot.send_day(TODAY))
    assert [s["text"].split(" · ")[0] for s in api.sent()] == ["<b>Today", "<b>Habits</b>", "<b>Today"]


@pytest.mark.parametrize("failing", [0, 1])
def test_a_day_that_couldnt_be_sent_is_tried_again(bot, api, store, todo_list, failing):
    habit(store, todo_list, "Run")
    api.errors["sendMessage"] = [BotError("Forbidden: bot was blocked by the user", 403)]
    if failing:  # the habits, after the todos went out
        api.errors["sendMessage"] = []
        original = api.call

        async def second_fails(method, **params):
            if method == "sendMessage" and len(api.sent()) == 1:
                api.calls.append((method, params))
                raise BotError("Forbidden: bot was blocked by the user", 403)
            return await original(method, **params)

        api.call = second_fails
    run(bot.send_day(TODAY))
    assert store.meta(SENT) is None
    assert bot.status() == {
        "title": "Telegram bot",
        "running": False,
        "note": f"can't send to chat {CHAT}: Forbidden: bot was blocked by the user (send /start to the bot first)",
    }
    bot.username = "kanso_bot"
    run(bot.send_day(TODAY))
    assert store.meta(SENT) == TODAY.isoformat() and bot.status()["running"]


def test_the_schedule_sends_once_due(bot, api, store, monkeypatch):
    monkeypatch.setattr(telegram, "SCHEDULE_CHECK", 0.01)
    bot.now = at(9, 30)

    async def main():
        task = asyncio.create_task(bot.keep_schedule())
        await eventually(lambda: store.meta(SENT) == TODAY.isoformat())
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    run(main())
    assert len(api.sent()) == 1


# Reaching Telegram


def test_running_says_who_it_runs_as(bot, api):
    async def main():
        task = asyncio.create_task(bot.run())
        await eventually(lambda: bot.status()["running"])
        assert bot.status()["note"] == f"running as @kanso_bot, sending to chat {CHAT} daily at 09:00"
        assert api.sent("setMyCommands") == [{"commands": telegram.COMMANDS}]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    run(main())


@pytest.mark.parametrize("code", [401, 404])
def test_a_refused_token_stops_the_bot(bot, api, code):
    api.errors["getMe"] = [BotError("Unauthorized", code)]
    run(bot.run())
    assert bot.status()["note"] == "Telegram refused the token: check it with @BotFather"
    with pytest.raises(Refused):
        api.errors["getMe"] = [BotError("Unauthorized", code)]
        run(bot.call("getMe"))


def test_other_refusals_on_starting_stop_the_bot_too(bot, api):
    api.errors["setMyCommands"] = [BotError("Bad Request: odd", 400)]
    run(bot.run())
    assert bot.status() == {"title": "Telegram bot", "running": False, "note": "Telegram refused: Bad Request: odd"}


@pytest.mark.parametrize(
    "error, note",
    [
        (BotError("Can't reach Telegram: down"), "can't reach Telegram, retrying"),
        (BotError("Conflict: terminated by other getUpdates request", 409), "another program uses this bot's token, retrying"),
        (BotError("Too Many Requests", 429), "Too Many Requests, retrying"),
        (BotError("Bad Gateway", 502), "Bad Gateway, retrying"),
    ],
)
def test_passing_trouble_is_retried_and_shown_meanwhile(bot, api, monkeypatch, error, note):
    seen = []
    sleep = asyncio.sleep

    async def note_and_sleep(seconds):
        seen.append((seconds, bot.status()["note"]))
        await sleep(0)

    monkeypatch.setattr(telegram, "RETRY", 1)
    monkeypatch.setattr(telegram.asyncio, "sleep", note_and_sleep)
    api.errors["getMe"] = [error] * 8
    assert run(bot.call("getMe")) == {"username": "kanso_bot"}
    assert seen == [(s, note) for s in (1, 2, 4, 8, 16, 32, 60, 60)]
    assert bot.trouble is None


# The feature


def configured(store, at="09:00"):
    telegram.configure(store, "1:token", CHAT, at)


def test_the_feature_runs_the_bot_while_configured(node, store):
    apis = []

    def make_api(token):
        apis.append(FakeApi(token))
        return apis[-1]

    async def main():
        feature = TelegramBot(node, make_api)
        task = asyncio.create_task(feature.run())
        await asyncio.sleep(0)
        assert feature.status() is None and apis == []
        configured(store)
        feature.reload()
        await eventually(lambda: (feature.status() or {}).get("running"))
        configured(store, "07:30")
        feature.reload()
        await eventually(lambda: "07:30" in (feature.status() or {}).get("note", ""))
        assert len(apis) == 2 and apis[0].closed and apis[1].token == "1:token"
        assert telegram.unconfigure(store) and not telegram.unconfigure(store)
        feature.reload()
        await eventually(lambda: feature.status() is None)
        assert apis[1].closed
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    run(main())


def test_the_daemon_feature_registers_itself_while_it_runs(store, monkeypatch):
    monkeypatch.setattr(telegram, "Api", FakeApi)
    configured(store)

    async def main():
        async with Node(store, sync_over({})) as node:
            task = asyncio.create_task(telegram.serve(node))
            await eventually(lambda: node.feature_status.get(telegram.NAME, {}).get("running"))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert node.features == {} and node.feature_status == {}

    run(main())


def test_a_daemon_that_doesnt_sync_runs_no_bot(node, store):
    configured(store)

    async def main():
        async with node:
            task = asyncio.create_task(telegram.serve(node))
            await asyncio.sleep(0.01)
            assert node.features == {}
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(main())


def test_config_round_trips(store):
    assert telegram.config(store) is None
    configured(store, "06:15")
    assert telegram.config(store) == {"token": "1:token", "chat": CHAT, "at": "06:15"}
