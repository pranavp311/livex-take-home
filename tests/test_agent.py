"""The agent must never change the calendar without a confirm, and a card runs at most once."""

import json

import httpx
import pytest
from openai.types.chat import ChatCompletion

import store
from agent import YES, Agent
from calcom import CalCom

LA = "America/Los_Angeles"
BOOKING = {
    "uid": "bk_1", "title": "Investor call", "start": "2026-10-28T22:00:00Z", "end": "2026-10-28T22:30:00Z",
    "attendees": [{"name": "Dana"}], "eventType": {"id": 7},
}


class FakeCal:
    """Fake cal.com that records every request."""

    def __init__(self, fail_writes=False):
        self.requests = []
        self.fail_writes = fail_writes
        self.timeout_writes = False
        self.timeout_reads = False

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.path))
        path = req.url.path
        if req.method == "POST" and self.timeout_writes or req.method == "GET" and self.timeout_reads:
            raise httpx.ReadTimeout("slow", request=req)
        if req.method == "POST":
            if self.fail_writes:
                return httpx.Response(400, json={"status": "error", "error": {"message": "slot taken"}})
            return httpx.Response(201, json={"status": "success", "data": {"uid": "new"}})
        if path == "/v2/me":
            return httpx.Response(200, json={"data": {"username": "founder"}})
        if path == "/v2/event-types":
            return httpx.Response(200, json={"data": [{"id": 7, "title": "30 min intro", "lengthInMinutes": 30}]})
        if path == "/v2/bookings/bk_1":
            return httpx.Response(200, json={"data": BOOKING})
        if path == "/v2/bookings":
            return httpx.Response(200, json={"data": [BOOKING]})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    def writes(self):
        return [r for r in self.requests if r[0] == "POST"]


class FakeLLM:
    """Plays back a script of model turns. Each turn is a tool call or a final text."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.chat = self
        self.completions = self

    def create(self, **kw):
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            msg = {"role": "assistant", "content": turn}
        else:
            name, args = turn
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{len(self.turns)}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}
        return ChatCompletion.model_validate({
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})


def make(turns, fail_writes=False):
    fake = FakeCal(fail_writes)
    agent = Agent(CalCom("test", transport=httpx.MockTransport(fake.handler)), llm=FakeLLM(turns))
    return agent, fake, store.Session(tz=LA)


def test_book_shows_card_and_writes_nothing():
    agent, fake, s = make([
        ("book", {"event_type_id": 7, "start": "2026-10-29T14:00",
                  "attendee_name": "Priya", "attendee_email": "priya@example.com"}),
        "Here's the booking."])
    out = agent.chat(s, "book a 30 min intro with Priya thursday 2pm")
    assert fake.writes() == []
    [card] = out["cards"]
    assert card["kind"] == "book" and card["new_start"] == "2026-10-29T14:00"
    # The real call carries UTC, not the local time the model wrote.
    assert s.pending[card["change_id"]].args["start"] == "2026-10-29T21:00:00Z"


def test_confirm_runs_once():
    agent, fake, s = make([("reschedule", {"uid": "bk_1", "new_start": "2026-10-28T17:00"}), "Move it?"])
    card = agent.chat(s, "move my 3pm to 5pm")["cards"][0]
    assert agent.confirm(s, card["change_id"])["ok"]
    assert not agent.confirm(s, card["change_id"])["ok"]  # double click
    assert fake.writes() == [("POST", "/v2/bookings/bk_1/reschedule")]


def test_cancel_goes_out_only_on_confirm():
    # The browser holds the 8s undo window; the server must not cancel until confirm arrives.
    agent, fake, s = make([("cancel", {"uid": "bk_1"}), "Cancel it?"])
    card = agent.chat(s, "cancel my 3pm tomorrow")["cards"][0]
    assert fake.writes() == []
    assert agent.confirm(s, card["change_id"])["ok"]
    assert fake.writes() == [("POST", "/v2/bookings/bk_1/cancel")]


def test_failed_confirm_burns_card_and_bot_follows_up():
    # Retrying a write that may have half-succeeded risks a double booking, so the card is spent.
    agent, fake, s = make([("reschedule", {"uid": "bk_1", "new_start": "2026-10-28T17:00"}), "Move it?",
                           "5:00 was just taken. Want 5:30?"], fail_writes=True)
    card = agent.chat(s, "move my 3pm to 5pm")["cards"][0]
    assert card["old_start"] == "2026-10-28T15:00" and card["minutes"] == 30
    result = agent.confirm(s, card["change_id"])
    assert not result["ok"] and "slot taken" in result["message"]
    assert result["followup"]["reply"].startswith("5:00 was just taken")
    assert card["change_id"] not in s.pending


def test_write_timeout_is_not_reported_as_failure_to_retry():
    # A timed-out write may have gone through. The founder must check, not blindly try again.
    agent, fake, s = make([("book", {"event_type_id": 7, "start": "2026-10-29T14:00",
                                     "attendee_name": "Priya", "attendee_email": "p@example.com"}),
                           "Book it?", "Check your calendar."])
    fake.timeout_writes = True
    card = agent.chat(s, "book priya thursday 2pm")["cards"][0]
    result = agent.confirm(s, card["change_id"])
    assert not result["ok"] and "Check your calendar" in result["message"]


def test_cal_error_goes_back_to_model_not_user():
    agent, fake, s = make([("cancel", {"uid": "nope"}), "I couldn't find that meeting."])
    out = agent.chat(s, "cancel the thing")
    assert out["cards"] == [] and fake.writes() == []
    tool_msg = next(m for m in s.messages if m["role"] == "tool")
    assert "error" in json.loads(tool_msg["content"])


def test_read_timeout_becomes_tool_error_so_history_stays_valid():
    agent, fake, s = make([("list_bookings", {"start_date": "2026-10-28", "end_date": "2026-10-28"}),
                           "cal.com is slow right now."])
    fake.timeout_reads = True
    out = agent.chat(s, "what's on tomorrow")
    assert out["reply"] == "cal.com is slow right now."
    assert [m["role"] for m in s.messages] == ["user", "assistant", "tool", "assistant"]


def test_unexpected_error_rolls_turn_back():
    # A crash mid-turn must not leave an unanswered tool call, which would make every later request fail.
    agent, fake, s = make([("book", {"event_type_id": 7, "start": "2026-10-29T14:00",
                                     "attendee_name": "P", "attendee_email": "p@example.com"})])
    s.messages.append({"role": "user", "content": "earlier"})
    with pytest.raises(IndexError):  # the script runs out: the model call itself blows up
        agent.chat(s, "book it")
    assert s.messages == [{"role": "user", "content": "earlier"}, {"role": "user", "content": "book it"}]
    assert s.pending == {}  # the card the user never saw can't be confirmed by a typed yes


def test_options_and_day_view_reach_the_ui():
    agent, fake, s = make([
        ("list_bookings", {"start_date": "2026-10-29", "end_date": "2026-10-29"}),
        ("show_options", {"options": [{"label": "3:00 Investor call", "value": "the investor call at 3"},
                                      {"label": "3:00 Debrief", "value": "the debrief at 3"}]}),
        "Which one?"])
    out = agent.chat(s, "cancel my 3pm thursday")
    assert out["view"] == {"start_date": "2026-10-29", "end_date": "2026-10-29"}
    assert [o["value"] for o in out["options"]] == ["the investor call at 3", "the debrief at 3"]


def test_card_shows_the_time_that_will_really_be_booked():
    # 02:30 doesn't exist in LA on 2026-03-08 (clocks jump to 03:00). The card must not claim 02:30.
    agent, fake, s = make([("book", {"event_type_id": 7, "start": "2026-03-08T02:30",
                                     "attendee_name": "P", "attendee_email": "p@example.com"}), "ok"])
    card = agent.chat(s, "book")["cards"][0]
    assert card["new_start"] == "2026-03-08T03:30"


def test_only_explicit_yes_confirms_by_text():
    assert YES.match("yes") and YES.match("Y") and YES.match("yes!")
    for text in ("ok", "okay", "go ahead", "yes but later", "no"):
        assert not YES.match(text)


def test_history_trim_never_orphans_a_tool_reply():
    # Cutting between a tool call and its reply makes the model API reject every later turn.
    import agent as agent_mod
    s = store.Session(tz=LA)
    for i in range(30):
        s.messages += [{"role": "user", "content": f"u{i}"},
                       {"role": "assistant", "tool_calls": [{"id": f"t{i}"}]},
                       {"role": "tool", "tool_call_id": f"t{i}", "content": "{}"}]
    agent_mod.trim_history(s)
    assert len(s.messages) <= agent_mod.MAX_HISTORY
    assert s.messages[0]["role"] == "user"


def test_long_chat_keeps_working_instead_of_locking_the_user_out():
    # The browser resends the whole history; if the server refused long ones, every request would fail
    # until a reload. It trims (by count and size) instead, on confirm as well as chat.
    import agent as agent_mod
    agent, fake, s = make(["ok"])
    s.messages = [{"role": "user", "content": "y" * 5000} for _ in range(30)]
    agent.chat(s, "hi")
    assert len(json.dumps(s.messages)) <= agent_mod.MAX_HISTORY_CHARS + 5000
    assert s.messages[0]["role"] == "user"
