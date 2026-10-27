"""Sign in with cal.com (OAuth 2.0, confidential client).

Docs: https://cal.com/docs/api-reference/v2/oauth. Access tokens last 30 minutes;
we refresh a minute early with the client secret.
"""

import os
import secrets
import time
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://app.cal.com/auth/oauth2/authorize"
TOKEN_URL = "https://api.cal.com/v2/auth/oauth2/token"
SCOPES = "EVENT_TYPE_READ BOOKING_READ BOOKING_WRITE SCHEDULE_READ PROFILE_READ"


class AuthError(Exception):
    pass


def login_url(redirect_uri: str) -> tuple[str, str]:
    """Returns (url to send the browser to, state to check on the way back)."""
    state = secrets.token_urlsafe(24)
    query = urlencode({"client_id": os.environ["CAL_OAUTH_CLIENT_ID"], "redirect_uri": redirect_uri,
                       "state": state, "scope": SCOPES})
    return f"{AUTHORIZE_URL}?{query}", state


def _token_request(body: dict, transport: httpx.BaseTransport | None = None) -> dict:
    body = {"client_id": os.environ["CAL_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["CAL_OAUTH_CLIENT_SECRET"], **body}
    with httpx.Client(timeout=20, transport=transport) as http:
        r = http.post(TOKEN_URL, json=body)
    if r.status_code in (400, 401):
        # cal.com's docs: the grant or the client is bad, so the visitor has to sign in again.
        raise AuthError(r.json().get("error_description", "sign-in rejected"))
    r.raise_for_status()  # anything else (429, 5xx) is cal.com having a moment; don't sign the visitor out
    data = r.json()
    return {"access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", body.get("refresh_token")),
            "expires_at": time.time() + data["expires_in"]}


def exchange_code(code: str, redirect_uri: str, transport=None) -> dict:
    return _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                          transport)


def fresh_tokens(tokens: dict, transport=None) -> dict:
    """The same tokens if they have more than a minute left, otherwise refreshed ones."""
    if tokens["expires_at"] - time.time() > 60:
        return tokens
    return _token_request({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}, transport)
