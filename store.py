"""Per-request session state. The server keeps nothing: the browser holds the chat and open cards
and sends them with each request, and the cal.com tokens ride in an httpOnly cookie.

That's safe because every cal.com call uses the visitor's own token, so editing any of it
can only affect their own calendar.
"""

import uuid
from dataclasses import dataclass, field


@dataclass
class Change:
    id: str
    kind: str  # book | reschedule | cancel
    args: dict  # what calcom.py needs to run it
    card: dict  # what the UI shows


@dataclass
class Session:
    tz: str
    messages: list[dict] = field(default_factory=list)
    pending: dict[str, Change] = field(default_factory=dict)


def add_change(s: Session, kind: str, args: dict, card: dict) -> Change:
    change = Change(id="c_" + uuid.uuid4().hex[:8], kind=kind, args=args, card=card)
    # The card carries its args so the browser can hand the whole thing back on confirm.
    change.card = {"change_id": change.id, "kind": kind, **card, "args": args}
    s.pending[change.id] = change
    return change


def change_from_card(card: dict) -> Change:
    return Change(id=card["change_id"], kind=card["kind"], args=card["args"], card=card)


def take_change(s: Session, change_id: str) -> Change | None:
    return s.pending.pop(change_id, None)
