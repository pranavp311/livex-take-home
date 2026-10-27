"""Thin client for the cal.com v2 API.

Versions come from cal.com's OpenAPI spec (calcom/cal.com, docs/api-reference/v2/openapi.json).
Each endpoint family needs its own cal-api-version header.
"""

import time
from urllib.parse import quote

import httpx

BASE = "https://api.cal.com/v2"
V_BOOKINGS = "2024-08-13"
V_SLOTS = "2024-09-04"
V_EVENT_TYPES = "2024-06-14"


class CalComError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"cal.com {status}: {message}")
        self.status = status
        self.message = message


class CalCom:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(
            base_url=BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
            transport=transport,
        )

    def _call(self, method: str, path: str, version: str | None, **kw) -> dict:
        headers = {"cal-api-version": version} if version else {}
        # Only reads are retried. Retrying a write after a timeout could book twice.
        attempts = 3 if method == "GET" else 1
        for i in range(attempts):
            r = self.http.request(method, path, headers=headers, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(2**i)
                continue
            break
        if r.is_error:
            try:
                err = r.json().get("error", {})
                msg = err.get("message") if isinstance(err, dict) else str(err)
            except ValueError:
                msg = r.text[:300]
            raise CalComError(r.status_code, msg)
        return r.json()["data"]

    def me(self) -> dict:
        return self._call("GET", "/me", None)

    def event_types(self) -> list[dict]:
        # Without a username the endpoint isn't scoped to the key's owner.
        if not hasattr(self, "_username"):
            self._username = self.me()["username"]
        return self._call("GET", "/event-types", V_EVENT_TYPES, params={"username": self._username})

    def bookings(self, after_start: str, before_end: str, status: str = "upcoming",
                 sort: str = "asc") -> list[dict]:
        params = {"afterStart": after_start, "beforeEnd": before_end, "status": status,
                  "sortStart": sort, "take": 100}
        return self._call("GET", "/bookings", V_BOOKINGS, params=params)

    def booking(self, uid: str) -> dict:
        return self._call("GET", f"/bookings/{quote(uid, safe='')}", V_BOOKINGS)

    def slots(self, event_type_id: int, start: str, end: str, tz: str,
              reschedule_uid: str | None = None) -> dict[str, list[dict]]:
        params = {"eventTypeId": event_type_id, "start": start, "end": end, "timeZone": tz,
                  "format": "range"}
        if reschedule_uid:
            params["bookingUidToReschedule"] = reschedule_uid
        return self._call("GET", "/slots", V_SLOTS, params=params)

    def create_booking(self, event_type_id: int, start: str, name: str, email: str, tz: str) -> dict:
        body = {"eventTypeId": event_type_id, "start": start,
                "attendee": {"name": name, "email": email, "timeZone": tz}}
        return self._call("POST", "/bookings", V_BOOKINGS, json=body)

    def reschedule(self, uid: str, start: str) -> dict:
        return self._call("POST", f"/bookings/{quote(uid, safe='')}/reschedule", V_BOOKINGS, json={"start": start})

    def cancel(self, uid: str, reason: str) -> dict:
        return self._call("POST", f"/bookings/{quote(uid, safe='')}/cancel", V_BOOKINGS,
                          json={"cancellationReason": reason})
