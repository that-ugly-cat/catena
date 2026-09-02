#!/usr/bin/env python3
"""
Print the Caddy site block for catena, from the paths declared in the code.

Generated rather than written, for the reason the house convention gives: the
list of public paths lives in exactly one place, so the day somebody adds a
public route they notice while writing it instead of six months later.

    python caddy.py           # local mode: the app has its own sign-in
    python caddy.py --gated   # behind Borant ID

The matcher lists the *public* paths and not the private ones, deliberately: the
default branch is the gated one, so a route added later is born closed rather
than open.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("JWT_SECRET", "unused-by-this-script")

from catena.server.main import PUBLIC_PATHS  # noqa: E402

HOST = os.environ.get("PUBLIC_HOST", "catena.borant.eu")
PORT = os.environ.get("PORT", "8022")

PLAIN = """{host} {{
    reverse_proxy localhost:{port}
}}
"""

GATED = """{host} {{
    @public path {paths}
    handle @public {{
        import noforge
        import nocookie
        reverse_proxy localhost:{port}
    }}
    handle {{
        import borantid
        reverse_proxy localhost:{port}
    }}
}}
"""


def main() -> int:
    template = GATED if "--gated" in sys.argv else PLAIN
    print(template.format(host=HOST, port=PORT, paths=" ".join(PUBLIC_PATHS)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
