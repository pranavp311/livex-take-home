# Build plan

A chat assistant that manages a founder's cal.com calendar. The founder types "book a 30-min intro with a candidate Thursday afternoon" and the bot books it. Python, DeepSeek V4 Pro through OpenRouter, cal.com API v2.

The diagram is in `architecture.excalidraw` (open it on excalidraw.com) and `architecture.png`. It shows the take-home build and the production version side by side. The UI direction is in `brand-kit.md`.

## Take-home

One FastAPI process serves the web page and runs the agent loop. Chats and pending changes live in memory, so a restart forgets them. That's fine for a demo.

### Model

`deepseek/deepseek-v4-pro-0813` on OpenRouter, pinned so behaviour doesn't drift between runs. It supports `tools` and `tool_choice`. Price is $1.32 in and $3.96 out per million tokens. We send `provider.require_parameters: true` so OpenRouter only falls back to providers that support tool calling.

### Writes need a click

Book, reschedule and cancel never touch cal.com from the model. The tool returns a card instead:

```json
{"change_id": "c_8f2a", "kind": "book", "title": "Intro: Priya", "attendee": "priya@example.com",
 "old_start": null, "new_start": "2026-10-29T14:00:00-07:00"}
```

The UI shows the card with one action button (Book, Move or Cancel it) and Change. `POST /api/confirm {change_id}` runs the cal.com call. Typing "yes" while exactly one card is open does the same, handled by the chat route, not the model. Any other new message drops open cards.

Each `change_id` works once, even if the cal.com call fails, so a double click or a retry after a timeout can't double-book. On failure (slot taken, cal.com down) the bot says what happened and offers fresh slots.

### Time

The browser sends its IANA time zone with every message. The system prompt gets the current local time, the next 14 dates with weekdays, and fixed meanings for vague words: morning 9-12, afternoon 12-5, evening 5-8. The model writes local times. `zoneinfo` converts them to UTC before they go to cal.com.

"My 3pm" means: list today's bookings and match the one starting at 15:00 local. None or two matches, the bot asks.

### Tools

Headers on every call: `Authorization: Bearer $CAL_API_KEY` and a `cal-api-version` that differs per endpoint. Versions come from cal.com's OpenAPI spec (`calcom/cal.com`, `docs/api-reference/v2/openapi.json`), which is what the request and response shapes in `calcom.py` follow. The docs site shows newer bookings versions (`2026-05-01`, `2026-02-25`); we stay on the spec's until a live call says otherwise.

| Tool | cal.com endpoint | `cal-api-version` |
|---|---|---|
| `list_event_types` | `GET /v2/event-types` | `2024-06-14` |
| `list_bookings` | `GET /v2/bookings` | `2024-08-13` |
| `find_slots` | `GET /v2/slots` | `2024-09-04` |
| `book` | `POST /v2/bookings` | `2024-08-13` |
| `reschedule` | `POST /v2/bookings/{uid}/reschedule` | `2024-08-13` |
| `cancel` | `POST /v2/bookings/{uid}/cancel` | `2024-08-13` |

`book`, `reschedule` and `cancel` are the model-facing names. They create the card. `calcom.py` makes the real call on confirm.

For a reschedule, `find_slots` uses the booking's own event type and passes `bookingUidToReschedule`, so the meeting's current time doesn't count as busy.

The today panel calls `GET /api/schedule` after every confirmed change.

### Files

```
app.py        FastAPI: POST /api/chat, POST /api/confirm, GET /api/schedule, static page
agent.py      agent loop (max 6 tool rounds), system prompt, tool schemas
calcom.py     httpx client: headers, retries on 429/5xx
timeutil.py   date context for the prompt, local to UTC
store.py      sessions and pending changes in memory
static/       the UI
tests/
```

Keys come from `.env`: `LIVEX_OPENROUTER_API_KEY`, `CAL_API_KEY`.

### Build order

1. `calcom.py`, checked by hand against a test cal.com account.
2. `timeutil.py` with unit tests.
3. Agent loop with read-only tools, tried from the command line.
4. Write tools, cards and the confirm step.
5. FastAPI routes and the UI.
6. Follow-up questions for missing details, error messages.

### Tests

- Date handling: "Thursday afternoon" across time zones and a daylight-saving change. "My 3pm" with zero and two matches.
- cal.com client against recorded responses.
- The agent never writes without a confirm. A `change_id` can't run twice, and a failed confirm still uses it up.
- Typed "yes" confirms only when exactly one card is open. Any other message drops open cards.
- About 15 scripted chats against a real test cal.com account.
- A browser screenshot pass on the UI.

## Production

What changes when real founders use it:

1. Each user signs in with cal.com OAuth instead of pasting an API key. Tokens are stored encrypted, and the encryption key sits in a secrets manager.
2. App servers hold no state. Postgres stores users, chats, pending changes and a write log. Redis holds rate limits, the schedule cache and the live-update channel.
3. Writes are safe to retry. We record the change as "running" before calling cal.com. On a timeout we list bookings to see if it went through before trying again, because cal.com documents no idempotency key on create.
4. One pinned model everywhere. A new model version ships only after the eval set passes. Traces cover every model and cal.com call.
5. Rate limits per user before calling the model or cal.com (cal.com allows 120 requests a minute per API key). One container image on Fly.io or Cloud Run, with managed Postgres and Redis. The eval set runs before each deploy.
6. cal.com webhooks (`BOOKING_CREATED`, `BOOKING_RESCHEDULED`, `BOOKING_CANCELLED`) refresh the cache, and the server pushes updates so the today panel stays current when something changes outside the chat.

## Open questions

- The docs site and the OpenAPI spec disagree on the bookings `cal-api-version`. Confirm on the first live call.
- Undo after cancel only works if the backend holds the cancel call for a few seconds, because cal.com emails attendees straight away. The take-home skips undo unless there's time.
