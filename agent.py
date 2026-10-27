"""The agent loop: ask the model, run the tools it calls, repeat.

Read tools hit cal.com straight away. Write tools (book, reschedule, cancel)
only create a card; nothing changes in cal.com until the user confirms.
"""

import json
import os
import re
from datetime import date, datetime, timedelta

import httpx
from openai import OpenAI

import store
import timeutil
from calcom import CalCom, CalComError

MODEL = os.environ.get("LIVEX_MODEL", "deepseek/deepseek-v4-pro-0813")
MAX_ROUNDS = 6
MAX_HISTORY = 40  # messages kept between turns; the browser sends them all back each time
MAX_HISTORY_CHARS = 60_000

# Only an explicit yes confirms by text. "ok" can mean "go on", and a cancel can't be taken back.
YES = re.compile(r"^\s*(y|yes)[.!]?\s*$", re.I)

SYSTEM = """You manage a busy founder's cal.com calendar through chat. Every tap and every question costs them
time, so act first and ask last.

{date_context}

Event types (id: title, minutes): {event_types}

Be proactive:
- Never ask permission to look something up or to suggest times. Just do it.
- Never ask a yes/no question you can settle with a tool call.
- Fill gaps yourself: if there is one event type, use it; match a stated length ("30 min") to the event type
  with that length; for a person named without an email, call find_contacts and use the match if there is one.
- Never guess or make up an email. Use one the founder typed or one find_contacts returned. If find_contacts finds
  nobody, or more than one person, call open_booking_form so the founder picks.
- find_slots returns the open times inside the window you asked about ("in_window") and the 4 open times closest
  to its start ("closest"), searching a week ahead for you, so one call is always enough. If you know who and
  what: book the requested time if it's in_window, otherwise the first "closest" time, and in the same message
  call show_options with the other "closest" times (not the one you booked). Say it in one line, e.g.
  "7:00 is taken. 5:30 is the closest."
- When the founder taps a time and you know who and what, call book immediately.
- Only when the person or the time is truly unknown, call open_booking_form with everything you do know.

Rules:
- Times you read and write are local to the founder ({tz}), as 'YYYY-MM-DDTHH:MM'.
- To move or cancel a meeting, call list_bookings first and use the uid it returns. Never guess a uid.
  If the new time for a move is taken, offer the closest open times with show_options straight away.
- If a request matches no meeting, say so. If it matches more than one, ask which with show_options,
  one option per meeting, value naming the meeting and its time.
- "Move a meeting" or "Cancel a meeting" with no meeting named: list the next 7 days and show the upcoming
  meetings with show_options (at most 6, soonest first, label like 'Thu 3:00 PM Investor call'). Reply exactly
  "Which one?" and nothing else.
- Never offer or book a time find_slots didn't return.
- book, reschedule and cancel show the founder a card with a confirm button. Don't ask "shall I go ahead?" in text,
  and don't claim anything is booked, moved or cancelled until you are told it was confirmed.
- When you call book, reschedule, cancel, show_options or open_booking_form, write your one-line reply in the same
  message as the tool calls; that ends your turn. Option labels use 12-hour times without AM/PM, like '1:30'.
- Be brief: one short line. Never mention cards, tools or options by name. List meetings as '3:00 PM Title',
  one per line, no bullets. Use 12-hour times.
"""

TOOLS = [
    {"name": "list_event_types", "description": "The founder's meeting types (id, title, length).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_bookings", "description": "Upcoming meetings between two local dates, inclusive.",
     "parameters": {"type": "object", "required": ["start_date", "end_date"], "properties": {
         "start_date": {"type": "string", "description": "YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD"}}}},
    {"name": "find_slots", "description": "Open start times for an event type: those inside [start, end) and the 4 "
                                          "closest to start within a week. When moving a meeting, pass its uid as "
                                          "reschedule_uid.",
     "parameters": {"type": "object", "required": ["event_type_id", "start", "end"], "properties": {
         "event_type_id": {"type": "integer"},
         "start": {"type": "string", "description": "local YYYY-MM-DDTHH:MM"},
         "end": {"type": "string", "description": "local YYYY-MM-DDTHH:MM"},
         "reschedule_uid": {"type": "string"}}}},
    {"name": "show_options", "description": "Show tappable choices (times, or which meeting). "
                                            "A tap sends the option's value as the founder's next message.",
     "parameters": {"type": "object", "required": ["options"], "properties": {
         "options": {"type": "array", "maxItems": 6, "items": {
             "type": "object", "required": ["label", "value"], "properties": {
                 "label": {"type": "string", "description": "short, e.g. '2:00' or '3:00 Investor call'"},
                 "value": {"type": "string", "description": "full message, e.g. 'Thursday 2:00 PM'"}}}}}}},
    {"name": "find_contacts", "description": "People the founder has booked with, most frequent first, with emails. "
                                             "Pass a name or part of one.",
     "parameters": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "open_booking_form", "description": "Show a quick-fill booking card: meeting type, recent contacts, "
                                                 "day and open times as taps. Pass what you already know.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}, "email": {"type": "string"},
         "day": {"type": "string", "description": "YYYY-MM-DD"},
         "event_type_id": {"type": "integer"}}}},
    {"name": "book", "description": "Propose a new booking. Shows a confirm card.",
     "parameters": {"type": "object", "required": ["event_type_id", "start", "attendee_name", "attendee_email"],
                    "properties": {
                        "event_type_id": {"type": "integer"},
                        "start": {"type": "string", "description": "local YYYY-MM-DDTHH:MM"},
                        "attendee_name": {"type": "string"},
                        "attendee_email": {"type": "string"}}}},
    {"name": "reschedule", "description": "Propose moving a booking. Shows a confirm card.",
     "parameters": {"type": "object", "required": ["uid", "new_start"], "properties": {
         "uid": {"type": "string"},
         "new_start": {"type": "string", "description": "local YYYY-MM-DDTHH:MM"}}}},
    {"name": "cancel", "description": "Propose cancelling a booking. Shows a confirm card.",
     "parameters": {"type": "object", "required": ["uid"], "properties": {
         "uid": {"type": "string"},
         "reason": {"type": "string"}}}},
]


SHOWS_UI = {"book", "reschedule", "cancel", "show_options", "open_booking_form"}


def _default_line(turn: "Turn") -> str:
    if turn.cards:
        return {"book": "Book this?", "reschedule": "Move it?", "cancel": "Cancel it?"}[turn.cards[-1]["kind"]]
    if turn.form:
        return "Who and when?"
    return "Which one?"


def _local(utc: str, tz: str) -> str:
    return timeutil.to_local(utc, tz).strftime("%Y-%m-%dT%H:%M")


def summarize_booking(b: dict, tz: str) -> dict:
    return {
        "uid": b["uid"],
        "title": b["title"],
        "start": _local(b["start"], tz),
        "end": _local(b["end"], tz),
        "attendees": [a["name"] for a in b.get("attendees", [])],
        "event_type_id": (b.get("eventType") or {}).get("id") or b.get("eventTypeId"),
    }


def _minutes(b: dict) -> int:
    start, end = (timeutil.to_local(b[k], "UTC") for k in ("start", "end"))
    return int((end - start).total_seconds() // 60)


def trim_history(s: store.Session):
    """Keeps the newest messages within MAX_HISTORY and MAX_HISTORY_CHARS, cut at a user message
    so no tool reply loses its call."""
    def too_big():
        return len(s.messages) > MAX_HISTORY or len(json.dumps(s.messages)) > MAX_HISTORY_CHARS

    while s.messages and too_big():
        cut = 1
        while cut < len(s.messages) and s.messages[cut].get("role") != "user":
            cut += 1
        del s.messages[:cut]


class Turn:
    """What one chat turn shows the UI besides the reply text."""

    def __init__(self):
        self.cards: list[dict] = []
        self.options: list[dict] = []
        self.view: dict | None = None
        self.form: dict | None = None


class Agent:
    def __init__(self, cal: CalCom, llm: OpenAI | None = None):
        self.cal = cal
        self.llm = llm or OpenAI(base_url="https://openrouter.ai/api/v1",
                                 api_key=os.environ["LIVEX_OPENROUTER_API_KEY"], timeout=40, max_retries=1)

    # ---- tools ----

    def run_tool(self, s: store.Session, turn: Turn, name: str, args: dict) -> dict:
        tz = s.tz
        if name == "list_event_types":
            return {"event_types": [{"id": e["id"], "title": e["title"], "minutes": e["lengthInMinutes"]}
                                    for e in self.cal.event_types()]}
        if name == "list_bookings":
            lo, hi = timeutil.day_range_utc(args["start_date"], args["end_date"], tz)
            if args["start_date"] == args["end_date"]:
                turn.view = {"start_date": args["start_date"], "end_date": args["end_date"]}
            return {"bookings": [summarize_booking(b, tz) for b in self.cal.bookings(lo, hi)]}
        if name == "find_slots":
            # One call covers the asked window's day and the week after, and hands back the closest open times.
            # Without this the model hunts day by day, which costs rounds and tempts it to guess times.
            # Normalise to naive local time so a model-supplied offset or Z compares with the slot strings.
            want_lo, want_hi = (datetime.fromisoformat(_local(timeutil.to_utc(args[k], tz), tz))
                                for k in ("start", "end"))
            day = want_lo.date().isoformat()
            lo, hi = timeutil.day_range_utc(day, (want_lo.date() + timedelta(days=7)).isoformat(), tz)
            days = self.cal.slots(args["event_type_id"], lo, hi, tz, args.get("reschedule_uid"))
            starts = sorted(_local(slot["start"], tz) for d in days.values() for slot in d)
            in_window = [x for x in starts if want_lo <= datetime.fromisoformat(x) < want_hi]
            closest = sorted(starts, key=lambda x: abs(datetime.fromisoformat(x) - want_lo))[:4]
            return {"in_window": in_window[:12], "closest": closest}
        if name == "show_options":
            turn.options = [{"label": o["label"], "value": o["value"]} for o in args["options"]][:6]
            return {"shown": len(turn.options)}
        if name == "find_contacts":
            q = args["name"].strip().lower()
            matches = [c for c in self.contacts(tz, limit=50) if q in c["name"].lower() or q in c["email"]]
            return {"contacts": matches[:5]}
        if name == "open_booking_form":
            day = args.get("day") or timeutil.now_local(tz).date().isoformat()
            form = self.booking_form(tz, day, args.get("event_type_id"))
            turn.form = {"prefill": {"name": args.get("name"), "email": args.get("email"), "day": day,
                                     "event_type_id": form["event_type_id"]}, **form}
            return {"form_shown": True}
        if name == "book":
            et = next((e for e in self.cal.event_types() if e["id"] == args["event_type_id"]), None)
            if et is None:
                return {"error": "unknown event_type_id; call list_event_types"}
            start = timeutil.to_utc(args["start"], tz)
            change = store.add_change(s, "book", {
                "event_type_id": et["id"], "start": start,
                "name": args["attendee_name"], "email": args["attendee_email"]}, {
                "title": et["title"], "attendee": args["attendee_name"], "email": args["attendee_email"],
                "minutes": et["lengthInMinutes"],
                # Shown from the converted time, so a wall time skipped by DST shows what will really be booked.
                "old_start": None, "new_start": _local(start, tz)})
        elif name in ("reschedule", "cancel"):
            raw = self.cal.booking(args["uid"])
            b = summarize_booking(raw, tz)
            common = {"title": b["title"], "attendee": ", ".join(b["attendees"]), "minutes": _minutes(raw),
                      "old_start": b["start"]}
            if name == "reschedule":
                start = timeutil.to_utc(args["new_start"], tz)
                change = store.add_change(s, "reschedule", {"uid": b["uid"], "start": start},
                                          {**common, "new_start": _local(start, tz)})
            else:
                change = store.add_change(s, "cancel", {
                    "uid": b["uid"], "reason": args.get("reason") or "Cancelled by host"},
                    {**common, "new_start": None})
        else:
            return {"error": f"unknown tool {name}"}
        turn.cards.append(change.card)
        return {"card_shown": change.card}

    # ---- quick-fill booking ----

    def booking_form(self, tz: str, day: str, event_type_id: int | None = None) -> dict:
        """Everything the quick-fill card offers as taps: meeting types, recent contacts, and the founder's open
        times. One slots call covers the next 14 days, so the card can grey out full days and open on a free one."""
        types = [{"id": e["id"], "title": e["title"], "minutes": e["lengthInMinutes"]} for e in self.cal.event_types()]
        if event_type_id not in {t["id"] for t in types}:
            event_type_id = types[0]["id"] if types else None
        by_day: dict[str, list[str]] = {}
        if event_type_id is not None:
            # Always one 14-day window: from today when the asked day falls inside it (so every day chip is
            # covered), otherwise from the asked day. A past day is clamped to today; a stray year can't
            # turn into a years-long request.
            today = timeutil.now_local(tz).date()
            asked_d = max(date.fromisoformat(day), today)
            first = today if asked_d <= today + timedelta(days=13) else asked_d
            lo, hi = timeutil.day_range_utc(first.isoformat(), (first + timedelta(days=13)).isoformat(), tz)
            for d in self.cal.slots(event_type_id, lo, hi, tz).values():
                for slot in d:
                    start = _local(slot["start"], tz)
                    by_day.setdefault(start[:10], []).append(start)
        open_days = sorted(by_day)
        asked = day
        if not by_day.get(day):
            # A full day is a dead end. Land on the next day the founder is free instead.
            day = next((d for d in open_days if d >= asked), asked)
        return {"event_types": types, "event_type_id": event_type_id, "contacts": self.contacts(tz),
                "day": day, "asked_day": asked, "open_days": open_days, "slots": by_day.get(day, [])}

    def contacts(self, tz: str, limit: int = 8) -> list[dict]:
        """People the founder books with, most often first, then most recent. cal.com's booking history is
        the memory here, so there's nothing extra to store."""
        today = timeutil.now_local(tz).date()
        past_lo, _ = timeutil.day_range_utc((today - timedelta(days=180)).isoformat(), today.isoformat(), tz)
        _, next_hi = timeutil.day_range_utc(today.isoformat(), (today + timedelta(days=30)).isoformat(), tz)
        now = timeutil.to_utc(timeutil.now_local(tz).strftime("%Y-%m-%dT%H:%M"), tz)
        # Past bookings newest first: cal.com returns at most 100, and the recent ones matter most.
        bookings = self.cal.bookings(now, next_hi) + self.cal.bookings(past_lo, now, status="past", sort="desc")
        people: dict[str, dict] = {}
        for b in bookings:
            when = b["start"]
            for a in b.get("attendees", []):
                email = (a.get("email") or "").lower()
                if not email:
                    continue
                p = people.setdefault(email, {"name": a.get("name") or email, "email": email, "count": 0, "last": ""})
                p["count"] += 1
                p["last"] = max(p["last"], when)
        ranked = sorted(people.values(), key=lambda p: (p["count"], p["last"]), reverse=True)
        return [{"name": p["name"], "email": p["email"]} for p in ranked[:limit]]

    def propose(self, s: store.Session, event_type_id: int, start: str, name: str, email: str) -> dict:
        """Builds a Book card from the quick-fill card, with no model call."""
        trim_history(s)
        turn = Turn()
        result = self._safe_tool(s, turn, "book", json.dumps(
            {"event_type_id": event_type_id, "start": start, "attendee_name": name, "attendee_email": email}))
        if "error" in result:
            return {"ok": False, "message": result["error"], "cards": []}
        s.messages.append({"role": "user", "content": f"[system] The founder filled the booking card: {name} "
                                                      f"<{email}> at {start}. A Book card is showing."})
        return {"ok": True, "cards": turn.cards}

    # ---- loop ----

    def chat(self, s: store.Session, text: str) -> dict:
        trim_history(s)
        s.messages.append({"role": "user", "content": text})
        return self._turn(s)

    def _turn(self, s: store.Session) -> dict:
        # Event types go in up front: it saves the model a round trip on almost every booking.
        types = "; ".join(f"{e['id']}: {e['title']}, {e['lengthInMinutes']}" for e in self.cal.event_types()) or "none"
        system = {"role": "system", "content": SYSTEM.format(
            date_context=timeutil.date_context(s.tz), tz=s.tz, event_types=types)}
        turn = Turn()
        mark = len(s.messages)
        try:
            for _ in range(MAX_ROUNDS):
                resp = self.llm.chat.completions.create(
                    model=MODEL,
                    messages=[system, *s.messages],
                    tools=[{"type": "function", "function": t} for t in TOOLS],
                    max_tokens=1000,
                    # Only fall back to providers that support tool calling. Thinking is off: with it on,
                    # DeepSeek rejects tool turns unless its reasoning is passed back, and it's slower.
                    extra_body={"provider": {"require_parameters": True}, "reasoning": {"enabled": False}},
                )
                msg = resp.choices[0].message
                s.messages.append(msg.model_dump(exclude_none=True, include={"role", "content", "tool_calls"}))
                if not msg.tool_calls:
                    return self._out(msg.content or "", turn)
                results = []
                for call in msg.tool_calls:
                    result = self._safe_tool(s, turn, call.function.name, call.function.arguments)
                    results.append((call.function.name, result))
                    s.messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
                # Once everything this round put something on screen for the founder, the turn is done.
                # Another model call would only write a line the founder doesn't need, and cost a second or two.
                if all(n in SHOWS_UI and "error" not in r for n, r in results):
                    reply = (msg.content or "").strip() or _default_line(turn)
                    s.messages.append({"role": "assistant", "content": reply})  # so the next turn sees what was said
                    return self._out(reply, turn)
            return self._out("That took too many steps. Can you say it another way?", turn)
        except Exception:
            # Roll the turn back so a half-written tool exchange can't poison every later request.
            del s.messages[mark:]
            for card in turn.cards:
                s.pending.pop(card["change_id"], None)
            raise

    def _safe_tool(self, s: store.Session, turn: Turn, name: str, raw_args: str | None) -> dict:
        """Turns errors the model can act on into a tool result. Anything else propagates."""
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                return {"error": "arguments must be a JSON object"}
            return self.run_tool(s, turn, name, args)
        except CalComError as e:
            return {"error": e.message}
        except httpx.HTTPError:
            return {"error": "cal.com didn't answer. Try again in a moment."}
        except (ValueError, KeyError) as e:
            return {"error": f"bad arguments: {e}"}

    @staticmethod
    def _out(reply: str, turn: Turn) -> dict:
        return {"reply": reply.strip(), "cards": turn.cards, "options": turn.options, "view": turn.view, "form": turn.form}

    # ---- confirm ----

    def confirm(self, s: store.Session, change_id: str) -> dict:
        """Runs a card's change. The caller holds the session lock. For a cancel, the browser has
        already waited out the undo window."""
        trim_history(s)
        change = store.take_change(s, change_id)
        if change is None:
            return {"ok": False, "message": "That card has expired or was already used."}
        title = change.card["title"]
        a = change.args
        try:
            if change.kind == "book":
                self.cal.create_booking(a["event_type_id"], a["start"], a["name"], a["email"], s.tz)
            elif change.kind == "reschedule":
                self.cal.reschedule(a["uid"], a["start"])
            else:
                self.cal.cancel(a["uid"], a["reason"])
        except (CalComError, httpx.HTTPError) as e:
            # A timeout may still have gone through, so never offer a blind retry.
            reason = e.message if isinstance(e, CalComError) else \
                "cal.com didn't answer. Check your calendar before trying again."
            s.messages.append({"role": "user", "content":
                               f"[system] Confirming {change.kind} '{title}' failed: {reason} "
                               "Tell the founder briefly and offer fresh times if it was a slot problem."})
            return {"ok": False, "message": reason, "followup": self._turn(s)}
        done = {"book": "Booked", "reschedule": "Moved", "cancel": "Cancelled"}[change.kind]
        s.messages.append({"role": "user", "content": f"[system] The founder confirmed. {done}: {title}."})
        return {"ok": True, "message": f"{done}.", "card": change.card}
