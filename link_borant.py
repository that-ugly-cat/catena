#!/usr/bin/env python3
"""
Link an existing local account to a Borant ID subject, by hand.

Run this **before** switching an installation to AUTH_MODE=gateway, or right
after the first person bounces off it. Without the link, a subject arriving from
the gate does not match any row, so it gets a fresh empty profile — and the old
account, with its Zotero credential and its bindings, stays where it is.

The linking is deliberately manual and deliberately not automatic on email.
`user_from_gateway()` looks a person up by `borant_sub` and never by address,
because an email is something a gate operator types: matching on it would let a
typo hand one person another person's Zotero key. A human doing it once, and
seeing what happened, is the right amount of ceremony.

    python link_borant.py                     # show what is linked and what is not
    python link_borant.py <email> <sub>       # link one account
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from catena.server.models import SessionLocal, User, init_db  # noqa: E402


def show(db) -> int:
    rows = db.query(User).order_by(User.email).all()
    if not rows:
        print("No users yet.")
        return 0
    width = max(len(u.email) for u in rows)
    linked = 0
    for u in rows:
        if u.borant_sub:
            linked += 1
        print(f"  {u.email:<{width}}  {u.borant_sub or '— not linked —'}")
    print(f"\n{linked} of {len(rows)} accounts linked to a Borant ID subject.")
    return 0


def link(db, email: str, sub: str) -> int:
    email = email.strip().lower()
    sub = sub.strip()

    user = db.query(User).filter(User.email == email).first()
    if not user:
        print(f"No account with address {email}.", file=sys.stderr)
        return 1

    clash = db.query(User).filter(User.borant_sub == sub, User.id != user.id).first()
    if clash:
        print(
            f"Subject {sub} is already linked to {clash.email}. Unlink that one "
            "first — one subject cannot own two profiles.",
            file=sys.stderr,
        )
        return 1

    if user.borant_sub and user.borant_sub != sub:
        print(
            f"{email} is already linked to {user.borant_sub}. Refusing to move it "
            "silently: if that is what you want, clear it first.",
            file=sys.stderr,
        )
        return 1

    was = user.borant_sub
    user.borant_sub = sub
    db.commit()
    print(f"{email} -> {sub}" + (" (unchanged)" if was == sub else ""))
    return 0


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        if len(sys.argv) == 1:
            return show(db)
        if len(sys.argv) == 3:
            return link(db, sys.argv[1], sys.argv[2])
        print(__doc__.strip(), file=sys.stderr)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
