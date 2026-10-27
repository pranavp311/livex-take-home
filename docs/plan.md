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

Headers on every call: `Authorization: Bearer <visitor's OAuth access token>` and a `cal-api-version` that differs per endpoint. Versions come from cal.com's OpenAPI spec (`calcom/cal.com`, `docs/api-reference/v2/openapi.json`), which is what the request and response shapes in `calcom.py` follow. The docs site shows newer bookings versions (`2026-05-01`, `2026-02-25`); we stay on the spec's until a live call says otherwise.

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

Secrets come from env (`.env` locally, Vercel project settings in production): `LIVEX_OPENROUTER_API_KEY`, `CAL_OAUTH_CLIENT_ID`, `CAL_OAUTH_CLIENT_SECRET`, plus `KV_REST_API_URL` and `KV_REST_API_TOKEN` from the Upstash store. There is no personal cal.com API key; every visitor signs in with their own cal.com account.

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

## Implementation (take-home, round 2)

This round wires the approved Version B design to the backend. The frontend is plain HTML, CSS and JS in `static/`, served by FastAPI, with no build step. The styles are ported from the design files.

### API contract

```
POST /api/chat     {session_id, message, tz}
                -> {reply, cards: [Card], options: [{label, value}], view: {start_date, end_date} | null,
                    confirmed?: ConfirmResult}
POST /api/confirm  {session_id, change_id, tz} -> ConfirmResult
POST /api/undo     {session_id, change_id, tz} -> {ok, message}
GET  /api/schedule ?tz=&day=YYYY-MM-DD&days=1  -> {bookings: [{uid, title, start, end, attendees, event_type_id}]}

Card          {change_id, kind: book|reschedule|cancel, title, attendee, minutes, old_start, new_start}
ConfirmResult {ok, message, card?, undo_seconds?, followup?: <chat response>}
```

All times the browser sees are local `YYYY-MM-DDTHH:MM`.

### Backend additions

- `show_options` tool. The model uses it to render slot chips and "which one?" buttons. A tap sends the option's `value` as the next user message.
- `view`. When the model lists a single day's bookings, the response names that day, and the today panel switches to it with a "Today" pill to return.
- Undo. Confirming a cancel starts an 8-second timer instead of calling cal.com. `/api/undo` stops the timer. When the timer fires, the cancel goes out and the attendee gets the email.
- A failed confirm runs one more model turn, so the bot explains and offers fresh times in the same response (`followup`).

Fixes from the backend review:
- A tool error the code didn't expect (timeout, malformed arguments) becomes an error result for the model, so every tool call gets a reply. Anything else rolls the turn's history back and drops the turn's cards before re-raising, so one bad turn can't wedge the session.
- Confirm reports a cal.com timeout as "cal.com didn't answer, check your calendar", because the write may have gone through.
- Only "y" or "yes" confirms by text, in both app and CLI. "ok" could mean "go on", and a cancel can't be taken back.
- A lock per session, so a confirm can't land in the middle of a chat turn's tool calls.
- Card times come from the converted UTC time, so a time that doesn't exist across a DST change shows what will actually be booked.
- The booking uid is URL-escaped before it goes into a path.

### Frontend, screen by screen

| Design frame | What drives it |
|---|---|
| Main: today panel + chat | `GET /api/schedule` on load, every minute, and after each confirm or undo |
| First run / empty day | Schedule returns no bookings |
| Book: confirm, booked, slot taken | `cards` (pending slot dashed on the panel), `/api/confirm`, `followup` |
| Reschedule: which one, confirm, moved | `options`, `cards`, `/api/confirm` |
| Cancel: confirm, cancelled + undo | `cards`, `/api/confirm` with `undo_seconds`, `/api/undo` |
| View a day | `view` |
| Thinking | While `/api/chat` is in flight |
| Phone | CSS breakpoint at 720px: the panel becomes a top strip with a "Today" toggle |

Motion follows the design's Motion frame: 150-300 ms, no bounce, and `prefers-reduced-motion` turns movement off.

### Checks

- pytest for every review fix and the new tools.
- Sign in on the Vercel deployment as a fresh user, then book, move and cancel with undo, each checked in cal.com.
- Playwright screenshots at 1280 and 390 px wide, compared against the design frames.

## Round 3: Vercel and cal.com sign-in

Hosting is Vercel (personal team `pranavp311s-projects`, project `livex-take-home`, auto-deploys from `main`). Testers just open the link and sign in with their own cal.com account. Nothing to install.

The server keeps no state, so any serverless instance can answer any request:

- The browser holds the chat history and open cards and sends them with each request. The server checks them (only user, assistant and tool messages; 60 messages or 100 KB at most) so the history can't swap the system prompt or bulk-spend the OpenRouter key.
- The cal.com tokens live in an httpOnly `cal_tokens` cookie. Tokens refresh a minute before their 30-minute expiry, and a middleware writes the new pair back on every response, failed ones included, because cal.com may retire the old refresh token.
- A card running once is now the browser's job: the button disables on the first tap. That's the take-home trade-off for having no server storage.
- Undo lives in the browser: "Cancel it" starts an 8-second toast, and the browser calls `/api/confirm` only when it runs out. Closing the tab inside those 8 seconds cancels nothing.
- The browser sends one request at a time, so two requests never refresh the token at once.

Sign-in (`auth.py`): `/api/auth/login` redirects to cal.com with a `state` cookie, `/api/auth/callback` checks it and swaps the code for tokens. The redirect URI comes from `PUBLIC_URL`, never from the request, because cal.com matches it exactly. Scopes: `EVENT_TYPE_READ BOOKING_READ BOOKING_WRITE SCHEDULE_READ PROFILE_READ`. Registered redirect URIs: `https://livex-take-home.vercel.app/api/auth/callback` and `http://localhost:8000/api/auth/callback`. The cal.com OAuth client stays "pending" until a cal.com admin approves it; until then only its owner can sign in.

Model: DeepSeek thinking is turned off. With it on, DeepSeek rejects any tool turn whose reasoning isn't passed back, and it's slower. Verified with a live two-turn tool call.

Vercel env (production): `LIVEX_OPENROUTER_API_KEY`, `CAL_OAUTH_CLIENT_ID`, `CAL_OAUTH_CLIENT_SECRET`, `PUBLIC_URL`.
