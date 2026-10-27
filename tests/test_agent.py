"""The agent must never change the calendar without a confirm, and a card runs at most once."""

import json
from datetime import datetime

import httpx
import pytest
from openai.types.chat import ChatCompletion

import store
from agent import YES, Agent
from calcom import CalCom

LA = "America/Los_Angeles"
BOOKING = {
    "uid": "bk_1", "title": "Investor call", "start": "2026-10-28T22:00:00Z", "end": "2026-10-28T22:30:00Z",
    "attendees": [{"name": "Dana", "email": "Dana@Example.com"}], "eventType": {"id": 7},
}


class FakeCal:
    """Fake cal.com that records every request."""

    def __init__(self, fail_writes=False):
        self.requests = []
        self.fail_writes = fail_writes
        self.timeout_writes = False
        self.write_reply = {"uid": "new", "start": "2026-10-29T21:00:00Z"}
        self.timeout_reads = False

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.path))
        path = req.url.path
        if req.method == "POST" and self.timeout_writes or path == "/v2/bookings" and self.timeout_reads:
            raise httpx.ReadTimeout("slow", request=req)
        if req.method == "POST":
            if self.fail_writes:
                return httpx.Response(400, json={"status": "error", "error": {"message": "slot taken"}})
            return httpx.Response(201, json={"status": "success", "data": self.write_reply})
        if path == "/v2/me":
            return httpx.Response(200, json={"data": {"username": "founder"}})
        if path == "/v2/event-types":
            return httpx.Response(200, json={"data": [{"id": 7, "title": "30 min intro", "lengthInMinutes": 30}]})
        if path == "/v2/bookings/bk_1":
            return httpx.Response(200, json={"data": BOOKING})
        if path == "/v2/bookings":
            return httpx.Response(200, json={"data": [BOOKING]})
        if path == "/v2/slots":
            return httpx.Response(200, json={"data": {"2026-10-29": [
                {"start": "2026-10-29T21:00:00.000Z", "end": "2026-10-29T21:30:00.000Z"}]}})
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
            calls = turn if isinstance(turn, list) else [turn]  # a list is several tool calls in one round
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{len(self.turns)}_{i}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}} for i, (name, args) in enumerate(calls)]}
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
    agent, fake, s = make([("reschedule", {"uid": "bk_1", "new_start": "2026-10-28T17:00"}),
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
    # The card and a lookup in one round means another model call is due, and the script has run out.
    agent, fake, s = make([[("book", {"event_type_id": 7, "start": "2026-10-29T14:00",
                                      "attendee_name": "P", "attendee_email": "p@example.com"}),
                            ("list_event_types", {})]])
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


def test_missing_details_open_the_quick_fill_card_with_what_is_known():
    # Typing name, email and time is too much work; the card offers them as taps, pre-filled from the chat.
    agent, fake, s = make([("open_booking_form", {"name": "Priya", "day": "2026-10-29"}), "Who and when?"])
    out = agent.chat(s, "book an intro with Priya thursday")
    form = out["form"]
    assert form["prefill"] == {"name": "Priya", "email": None, "day": "2026-10-29", "event_type_id": 7}
    assert form["event_types"] == [{"id": 7, "title": "30 min intro", "minutes": 30}]
    assert form["slots"] == ["2026-10-29T14:00"]  # local time, not UTC
    assert form["open_days"] == ["2026-10-29"]
    # One entry per person even though they appear on both the upcoming and past lists.
    assert form["contacts"] == [{"name": "Dana", "email": "dana@example.com"}]
    assert fake.writes() == []


def test_filled_card_becomes_a_book_card_without_writing():
    agent, fake, s = make([])
    out = agent.propose(s, 7, "2026-10-29T14:00", "Priya", "priya@example.com")
    [card] = out["cards"]
    assert out["ok"] and card["kind"] == "book" and card["args"]["start"] == "2026-10-29T21:00:00Z"
    assert fake.writes() == []
    assert "filled the booking card" in s.messages[-1]["content"]  # the model knows what happened


def test_filled_card_with_unknown_meeting_type_says_so():
    agent, fake, s = make([])
    out = agent.propose(s, 999, "2026-10-29T14:00", "Priya", "priya@example.com")
    assert not out["ok"] and out["cards"] == [] and s.messages == []


def test_contacts_rank_by_how_often_you_book_them():
    # The people a founder books with most should be one tap away, ahead of a one-off from yesterday.
    def booking(email, start):
        return {**BOOKING, "start": start, "attendees": [{"name": email.split("@")[0], "email": email}]}
    agent, fake, s = make([])
    data = [booking("often@x.com", "2026-09-01T17:00:00Z"), booking("often@x.com", "2026-09-08T17:00:00Z"),
            booking("often@x.com", "2026-09-15T17:00:00Z"), booking("once@x.com", "2026-10-26T17:00:00Z")]
    agent.cal.bookings = lambda lo, hi, status="upcoming", sort="asc": data if status == "past" else []
    assert [c["email"] for c in agent.contacts(LA)] == ["often@x.com", "once@x.com"]


def test_a_known_first_name_resolves_to_an_email():
    agent, fake, s = make([("find_contacts", {"name": "dana"}), "ok"])
    agent.chat(s, "book dana")
    tool = json.loads(next(m for m in s.messages if m["role"] == "tool")["content"])
    assert tool == {"contacts": [{"name": "Dana", "email": "dana@example.com"}]}


def test_contacts_ask_for_past_bookings_newest_first():
    # cal.com returns at most 100 per call; asking oldest-first would drop last week's regulars.
    agent, fake, s = make([])
    seen = []
    agent.cal.bookings = lambda lo, hi, status="upcoming", sort="asc": seen.append((status, sort)) or []
    agent.contacts(LA)
    assert ("past", "desc") in seen


def test_book_card_shows_the_email_so_a_wrong_one_can_be_caught():
    agent, fake, s = make([])
    card = agent.propose(s, 7, "2026-10-29T14:00", "Priya", "priya@example.com")["cards"][0]
    assert card["email"] == "priya@example.com"


def test_full_day_opens_the_card_on_the_next_free_day():
    # "No open times" on a day the founder is booked up is a dead end; land on the next day they're free.
    agent, fake, s = make([])
    form = agent.booking_form(LA, "2026-10-28")
    assert form["asked_day"] == "2026-10-28"
    assert form["day"] == "2026-10-29" and form["slots"] == ["2026-10-29T14:00"]


def test_turn_ends_once_the_card_is_on_screen():
    # Every model round costs the founder a second or two. Once the card and chips are showing, stop.
    agent, fake, s = make([[("book", {"event_type_id": 7, "start": "2026-10-29T14:00", "attendee_name": "P",
                                      "attendee_email": "p@example.com"}),
                            ("show_options", {"options": [{"label": "2:30", "value": "Thursday 2:30 PM"}]})]])
    out = agent.chat(s, "book p thursday 2pm")
    assert agent.llm.turns == []  # one model call, not two
    assert out["reply"] == "Book this?" and len(out["cards"]) == 1 and len(out["options"]) == 1


def test_event_types_are_in_the_prompt_so_the_model_skips_a_lookup():
    agent, fake, s = make(["hi"])
    seen = []
    create = agent.llm.create
    agent.llm.create = lambda **kw: seen.append(kw["messages"][0]["content"]) or create(**kw)
    agent.chat(s, "hello")
    assert "7: 30 min intro, 30" in seen[0]


def test_find_slots_hands_back_the_closest_times_in_one_call():
    # The model shouldn't need to hunt day by day (slow) or guess (wrong): one call gives it the nearest options.
    agent, fake, s = make([("find_slots", {"event_type_id": 7, "start": "2026-10-29T19:00", "end": "2026-10-29T20:00"}),
                           "ok"])
    agent.chat(s, "book thursday 7pm")
    result = json.loads(next(m for m in s.messages if m["role"] == "tool")["content"])
    assert result == {"in_window": [], "closest": ["2026-10-29T14:00"]}
    assert fake.requests.count(("GET", "/v2/slots")) == 1


def test_find_slots_accepts_times_with_an_offset():
    # The model sometimes writes "2026-10-29T19:00:00-07:00"; that must not crash the turn.
    agent, fake, s = make([("find_slots", {"event_type_id": 7, "start": "2026-10-29T19:00:00-07:00",
                                           "end": "2026-10-30T03:00:00Z"}), "ok"])
    agent.chat(s, "thursday 7pm")
    result = json.loads(next(m for m in s.messages if m["role"] == "tool")["content"])
    assert result["closest"] == ["2026-10-29T14:00"]


def test_a_stray_year_never_asks_cal_com_for_years_of_slots():
    agent, fake, s = make([])
    seen = []
    agent.cal.slots = lambda et, lo, hi, tz, uid=None: seen.append((lo, hi)) or {}
    agent.booking_form(LA, "2099-01-01")
    agent.booking_form(LA, "2020-01-01")
    for lo, hi in seen:
        span = datetime.fromisoformat(hi[:-1]) - datetime.fromisoformat(lo[:-1])
        assert span.days <= 15


def test_fast_turn_keeps_its_reply_in_history():
    agent, fake, s = make([("show_options", {"options": [{"label": "a", "value": "a"}]})])
    agent.chat(s, "move a meeting")
    assert s.messages[-1] == {"role": "assistant", "content": "Which one?"}


def test_after_a_move_the_bot_learns_the_new_booking_id():
    # cal.com gives a moved meeting a new uid. "Cancel it" right after must target the new one.
    agent, fake, s = make([("reschedule", {"uid": "bk_1", "new_start": "2026-10-28T17:00"})])
    fake.write_reply = {"uid": "bk_2", "start": "2026-10-29T00:00:00Z"}
    card = agent.chat(s, "move my 3pm to 5pm")["cards"][0]
    agent.confirm(s, card["change_id"])
    assert "uid is now bk_2" in s.messages[-1]["content"]


def test_acting_on_a_moved_meeting_follows_it_to_the_current_booking():
    agent, fake, s = make([("cancel", {"uid": "bk_old"})])
    moved = {**BOOKING, "uid": "bk_old", "status": "cancelled", "rescheduledToUid": "bk_1"}
    real = agent.cal.booking
    agent.cal.booking = lambda uid: moved if uid == "bk_old" else real(uid)
    card = agent.chat(s, "cancel it")["cards"][0]
    assert card["args"]["uid"] == "bk_1"


def test_moving_never_offers_the_meetings_current_time():
    # The booking is at 15:00 local on 10-28; cal.com marks that slot open when rescheduling it.
    agent, fake, s = make([("find_slots", {"event_type_id": 7, "start": "2026-10-28T15:00",
                                           "end": "2026-10-28T18:00", "reschedule_uid": "bk_1"}), "ok"])
    agent.cal.slots = lambda et, lo, hi, tz, uid=None: {"2026-10-28": [
        {"start": "2026-10-28T22:00:00Z"}, {"start": "2026-10-28T22:30:00Z"}]}
    agent.chat(s, "move it")
    result = json.loads(next(m for m in s.messages if m["role"] == "tool")["content"])
    assert "2026-10-28T15:00" not in result["closest"] and result["closest"] == ["2026-10-28T15:30"]
