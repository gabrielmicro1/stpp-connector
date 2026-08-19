"""Mint dev JWTs (HS256) for the two standing demo users.

Usage: python scripts/mint_jwt.py analyst-full        # token on stdout
       python scripts/mint_jwt.py --all               # "<user>\t<token>" lines
Env:   JWT_SECRET (default matches compose: dev-demo-secret)
       JWT_TTL_HOURS (default 720 — baked-in frontend tokens must outlive
       the demo week; see docs/specs/integration-api.md).
"""
import argparse
import os
import time

import jwt

TEST_USERS = {
    "analyst-full": {
        "name": "Avery Fullaccess",
        "component": "DARPA",
        "roles": ["rfff_reader", "wdp_reader"],
    },
    "analyst-local": {
        "name": "Logan Localonly",
        "component": "DARPA",
        "roles": ["rfff_reader"],
    },
}


def mint(user: str) -> str:
    profile = TEST_USERS[user]
    now = int(time.time())
    ttl_hours = int(os.getenv("JWT_TTL_HOURS", "720"))
    claims = {
        "sub": user,
        "name": profile["name"],
        "component": profile["component"],
        "roles": profile["roles"],
        "iat": now,
        "exp": now + ttl_hours * 3600,
    }
    return jwt.encode(claims, os.environ.get("JWT_SECRET", "dev-demo-secret"), algorithm="HS256")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user", nargs="?", choices=sorted(TEST_USERS))
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.all:
        for user in sorted(TEST_USERS):
            print(f"{user}\t{mint(user)}")
    elif args.user:
        print(mint(args.user))
    else:
        parser.error("provide a user or --all")


if __name__ == "__main__":
    main()
