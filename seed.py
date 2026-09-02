#!/usr/bin/env python3
"""Create the first user. Usage: python seed.py <email> "<name>" <password>"""
import sys
sys.path.insert(0, "src")

from catena.server.auth import hash_password          # noqa: E402
from catena.server.models import SessionLocal, User, init_db  # noqa: E402


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    email, name, password = sys.argv[1].strip().lower(), sys.argv[2], sys.argv[3]
    init_db()
    db = SessionLocal()
    if db.query(User).filter(User.email == email).first():
        print(f"{email} already exists.", file=sys.stderr)
        return 1
    db.add(User(email=email, name=name, password_hash=hash_password(password),
                is_admin=True, is_active=True))
    db.commit()
    print(f"created {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
