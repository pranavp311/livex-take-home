"""The agent loop: ask the model, run the tools it calls, repeat.

Read tools hit cal.com straight away. Write tools (book, reschedule, cancel)
only create a card; nothing changes in cal.com until the user confirms.
"""

import json
import os
import re

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

SYSTEM = """You manage a busy founder's cal.com calendar through chat.

{date_context}

Rules:
- Times you read and write are local to the founder ({tz}), as 'YYYY-MM-DDTHH:MM'.
- To move or cancel a meeting, call list_bookings first and use the uid it returns. Never guess a uid.
- If a request matches no meeting, say so. If it matches more than one, ask which with show_options,
  one option per meeting, value naming the meeting and its time.
- To book: you need an event type, the attendee's name and email, and a time. If there is only one event type, use it.
  Call find_slots, then show_options with 3-4 of the times it returned. Never offer a time find_slots didn't return.
- book, reschedule and cancel show the founder a card with a confirm button. Don't ask "shall I go ahead?" in text,
  and don't claim anything is booked, moved or cancelled until you are told it was confirmed.
- Be brief. When a card or options are shown, one short line like "Book this?" or "Which time?". Never mention
  cards, tools or options by name. List meetings as '3:00 PM Title', one per line, no bullets. Use 12-hour times.
"""

TOOLS = [
    {"name": "list_event_types", "description": "The founder's meeting types (id, title, length).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "list_bookings", "description": "Upcoming meetings between two local dates, inclusive.",
     "parameters": {"type": "object", "required": ["start_date", "end_date"], "properties": {
         "start_date": {"type": "string", "description": "YYYY-MM-DD"},
         "end_date": {"type": "string", "description": "YYYY-MM-DD"}}}},
    {"name": "find_slots", "description": "Free start times for an event type between two local times. "
                                          "When moving a meeting, pass its uid as reschedule_uid.",
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
            days = self.cal.slots(args["event_type_id"], timeutil.to_utc(args["start"], tz),
                                  timeutil.to_utc(args["end"], tz), tz, args.get("reschedule_uid"))
            starts = [_local(slot["start"], tz) for day in days.values() for slot in day]
            return {"slots": starts[:12]}
        if name == "show_options":
            turn.options = [{"label": o["label"], "value": o["value"]} for o in args["options"]][:6]
            return {"shown": len(turn.options)}
        if name == "book":
            et = next((e for e in self.cal.event_types() if e["id"] == args["event_type_id"]), None)
            if et is None:
                return {"error": "unknown event_type_id; call list_event_types"}
            start = timeutil.to_utc(args["start"], tz)
            change = store.add_change(s, "book", {
                "event_type_id": et["id"], "start": start,
                "name": args["attendee_name"], "email": args["attendee_email"]}, {
                "title": et["title"], "attendee": args["attendee_name"], "minutes": et["lengthInMinutes"],
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

    # ---- loop ----

    def chat(self, s: store.Session, text: str) -> dict:
        trim_history(s)
        s.messages.append({"role": "user", "content": text})
        return self._turn(s)

    def _turn(self, s: store.Session) -> dict:
        system = {"role": "system", "content": SYSTEM.format(
            date_context=timeutil.date_context(s.tz), tz=s.tz)}
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
                for call in msg.tool_calls:
                    result = self._safe_tool(s, turn, call.function.name, call.function.arguments)
                    s.messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
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
        return {"reply": reply, "cards": turn.cards, "options": turn.options, "view": turn.view}

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
