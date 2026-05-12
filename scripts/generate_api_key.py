"""Generate a fresh API key + its Argon2id hash.

Usage:
    python scripts/generate_api_key.py <name>

Prints the plain key (copy to the client, never store) and the
``name:hash`` snippet to append to the API_KEYS env var.
"""

import argparse
import secrets

from argon2 import PasswordHasher


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an API key and its Argon2id hash."
    )
    parser.add_argument(
        "name",
        help="Human-readable identifier for the key (e.g. 'laptop', 'pi-monitor').",
    )
    parser.add_argument(
        "--bytes",
        type=int,
        default=32,
        help="Random key size in bytes (default: 32 = 256 bits).",
    )
    args = parser.parse_args()

    if ";" in args.name or ":" in args.name:
        parser.error("name must not contain ';' or ':'.")

    plain_key = secrets.token_urlsafe(args.bytes)
    hashed = PasswordHasher().hash(plain_key)

    print()
    print(f"Name:      {args.name}")
    print(f"Plain key: {plain_key}")
    print()
    print("Send the plain key to the client. NEVER store it server-side.")
    print()
    print("Append (or replace) in your .env (single quotes are MANDATORY:")
    print("Argon2 hashes contain '$' characters that python-dotenv would")
    print("otherwise interpret as variable references):")
    print()
    print(f"  API_KEYS='{args.name}:{hashed}'")
    print()
    print("Multiple entries are separated by ';' inside the same quoted value:")
    print("  API_KEYS='name1:$argon2id$...;name2:$argon2id$...'")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
