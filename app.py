"""Web server: cal.com sign-in, chat, confirm, and the schedule for the today panel.

Stateless, so it runs on Vercel's serverless functions: the browser sends the chat history and
open cards with each request, and the cal.com tokens live in an httpOnly cookie.
"""

import base64
import json
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

load_dotenv()

import auth  # noqa: E402
import store  # noqa: E402
import timeutil  # noqa: E402
from agent import YES, Agent, summarize_booking  # noqa: E402
from calcom import CalCom, CalComError  # noqa: E402

app = FastAPI()
TOKENS = "cal_tokens"
# The site's own address. cal.com matches the redirect URI exactly, and behind Vercel's proxy the
# request itself may look like plain http, so it's set, not guessed.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
REDIRECT_URI = PUBLIC_URL + "/api/auth/callback"
SECURE = PUBLIC_URL.startswith("https://")


def _check_tz(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone {tz!r}")
    return tz


def _check_history(history: list[dict]) -> list[dict]:
    # The browser holds the chat, so it can send anything. Keep it to real chat messages. Normal
    # long chats get trimmed by the agent; only sizes no real chat reaches are refused outright.
    if len(history) > 400 or len(json.dumps(history)) > 1_000_000:
        raise ValueError("history too long")
    for m in history:
        if m.get("role") not in ("user", "assistant", "tool"):
            raise ValueError("history may only hold user, assistant and tool messages")
    return history


class Card(BaseModel):
    model_config = ConfigDict(extra="allow")  # display fields the UI shows
    change_id: str
    kind: Literal["book", "reschedule", "cancel"]
    title: str
    args: dict


class ChatIn(BaseModel):
    message: str = Field(max_length=2000)
    tz: str
    history: list[dict] = []
    open_cards: list[Card] = []
    _tz = field_validator("tz")(_check_tz)
    _history = field_validator("history")(_check_history)


class ConfirmIn(BaseModel):
    card: Card
    tz: str
    history: list[dict] = []
    _tz = field_validator("tz")(_check_tz)
    _history = field_validator("history")(_check_history)


@app.middleware("http")
async def keep_refreshed_tokens(request: Request, call_next):
    """cal.com rotates the refresh token, so a refreshed pair must reach the browser even when the
    request then fails. Otherwise the browser keeps a dead refresh token and is signed out next time."""
    try:
        resp = await call_next(request)
    except Exception:
        logging.exception("unhandled error on %s", request.url.path)
        resp = JSONResponse({"detail": "Something went wrong. Try again."}, status_code=500)
    if new := getattr(request.state, "new_tokens", None):
        _write_tokens(resp, new)
    return resp


@app.exception_handler(CalComError)
def calcom_error(request: Request, e: CalComError):
    if e.status == 401:  # the visitor revoked access in cal.com
        return JSONResponse({"detail": "Connect cal.com again."}, status_code=401)
    return JSONResponse({"detail": e.message}, status_code=502)


@app.exception_handler(httpx.HTTPError)
def upstream_down(request: Request, e: httpx.HTTPError):
    return JSONResponse({"detail": "cal.com didn't answer. Try again in a moment."}, status_code=503)


def _set_cookie(resp: Response, name: str, value: str, max_age: int):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", secure=SECURE)


def _write_tokens(resp: Response, tokens: dict):
    raw = base64.urlsafe_b64encode(json.dumps(tokens).encode()).decode()
    if len(raw) > 3800:
        # Browsers silently drop cookies over ~4 KB, which would loop the visitor on Connect.
        raise RuntimeError(f"cal.com tokens too big for a cookie ({len(raw)} bytes)")
    _set_cookie(resp, TOKENS, raw, max_age=30 * 24 * 3600)


def _cal(request: Request) -> CalCom:
    """A cal.com client for the signed-in visitor. Refreshes the tokens if they're about to expire;
    the middleware writes refreshed ones back."""
    raw = request.cookies.get(TOKENS)
    if not raw:
        raise HTTPException(401, "Connect cal.com first.")
    try:
        tokens = json.loads(base64.urlsafe_b64decode(raw))
        if not isinstance(tokens, dict):
            raise ValueError("not an object")
        fresh = auth.fresh_tokens(tokens)
    except (ValueError, KeyError, TypeError, auth.AuthError):
        # The cookie is left alone: if two requests refreshed at once, the other one may have
        # just written a good pair, and deleting here could overwrite it.
        raise HTTPException(401, "Your cal.com sign-in expired.")
    if fresh is not tokens:
        request.state.new_tokens = fresh
    return CalCom(fresh["access_token"])


# ---- sign-in ----

@app.get("/api/auth/login")
def login():
    url, state = auth.login_url(REDIRECT_URI)
    resp = RedirectResponse(url)
    _set_cookie(resp, "oauth_state", state, max_age=600)
    return resp


@app.get("/api/auth/callback", name="callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code:
        return RedirectResponse("/?" + urlencode({"auth_error": error or "missing_code"}))
    if not state or not secrets.compare_digest(state.encode(), request.cookies.get("oauth_state", "").encode()):
        raise HTTPException(400, "Sign-in state didn't match. Start again.")
    try:
        tokens = auth.exchange_code(code, REDIRECT_URI)
    except auth.AuthError as e:
        return RedirectResponse("/?" + urlencode({"auth_error": str(e)}))
    resp = RedirectResponse("/")
    _write_tokens(resp, tokens)
    resp.delete_cookie("oauth_state")
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = Response(status_code=204)
    resp.delete_cookie(TOKENS)
    return resp


@app.get("/api/me")
def me(request: Request):
    try:
        cal = _cal(request)
    except HTTPException:
        return {"connected": False}
    return {"connected": True, "name": cal.me().get("name")}


# ---- chat ----

@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    agent = Agent(_cal(request))
    s = store.Session(tz=body.tz, messages=body.history)
    open_cards = [store.change_from_card(c.model_dump()) for c in body.open_cards]
    if YES.match(body.message) and len(open_cards) == 1:
        change = open_cards[0]
        if change.kind == "cancel":
            # A typed yes would skip the browser's undo window, so point at the card instead.
            return {"reply": "Tap Cancel it on the card to confirm.", "cards": [], "options": [], "view": None,
                    "form": None, "history": s.messages}
        s.pending[change.id] = change
        result = agent.confirm(s, change.id)
        return {"reply": result["message"], "cards": [], "options": [], "view": None, "form": None,
                "confirmed": result, "history": s.messages}
    return {**agent.chat(s, body.message), "history": s.messages}


@app.post("/api/confirm")
def confirm(body: ConfirmIn, request: Request):
    agent = Agent(_cal(request))
    change = store.change_from_card(body.card.model_dump())
    s = store.Session(tz=body.tz, messages=body.history, pending={change.id: change})
    return {**agent.confirm(s, change.id), "history": s.messages}


def _check_start(start: str) -> str:
    datetime.fromisoformat(start)  # the pattern checks the shape; this rejects 2026-13-45T99:99
    return start


class ProposeIn(BaseModel):
    event_type_id: int
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    tz: str
    history: list[dict] = []
    _tz = field_validator("tz")(_check_tz)
    _history = field_validator("history")(_check_history)
    _start = field_validator("start")(_check_start)


@app.get("/api/booking-form")
def booking_form(request: Request, tz: str, day: str | None = None, event_type_id: int | None = None):
    try:
        _check_tz(tz)
        day = (date.fromisoformat(day) if day else timeutil.now_local(tz).date()).isoformat()
        timeutil.day_range_utc(day, day, tz)  # 9999-12-31 overflows when the day's end is computed
    except (ValueError, OverflowError) as e:
        raise HTTPException(422, str(e))
    return Agent(_cal(request)).booking_form(tz, day, event_type_id)


@app.post("/api/propose")
def propose(body: ProposeIn, request: Request):
    agent = Agent(_cal(request))
    s = store.Session(tz=body.tz, messages=body.history)
    return {**agent.propose(s, body.event_type_id, body.start, body.name, body.email), "history": s.messages}


@app.get("/api/schedule")
def schedule(request: Request, tz: str, day: str | None = None, days: int = 1):
    try:
        _check_tz(tz)
        start_day = date.fromisoformat(day) if day else None
        if start_day:
            timeutil.day_range_utc(start_day.isoformat(), (start_day + timedelta(days=13)).isoformat(), tz)
    except (ValueError, OverflowError) as e:
        raise HTTPException(422, str(e))
    if not 1 <= days <= 14:
        raise HTTPException(422, "days must be 1-14")
    cal = _cal(request)
    start = start_day or timeutil.now_local(tz).date()
    end = start + timedelta(days=days - 1)
    lo, hi = timeutil.day_range_utc(start.isoformat(), end.isoformat(), tz)
    return {"bookings": [summarize_booking(b, tz) for b in cal.bookings(lo, hi)]}


if Path("static").is_dir():
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
