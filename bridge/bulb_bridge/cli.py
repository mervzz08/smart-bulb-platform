"""Helpers for configuring the bridge.

  python -m bulb_bridge.cli new-secret        print a random secret
  python -m bulb_bridge.cli hash <secret>     print its sha256 (for API keys / OAuth clients)
"""

from __future__ import annotations

import secrets
import sys

from .auth import sha256_hex


def main(argv: list[str]) -> int:
    if len(argv) == 1 and argv[0] == "new-secret":
        print(secrets.token_urlsafe(32))
        return 0
    if len(argv) == 2 and argv[0] == "hash":
        print(sha256_hex(argv[1]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
