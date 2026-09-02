"""Admin CLI for plans, clients, and API keys — subscription lifecycle is
manual for now (no billing integration), so this is how you provision and
revoke access.

Usage examples (run from the shopee_scraper_api/ directory so .env is found):

    python scripts/manage_keys.py create-plan --name starter \\
        --rpm 30 --daily-quota 1000 --monthly-quota 20000 --duration-days 30

    python scripts/manage_keys.py create-client --name "Acme Corp" --email acme@example.com

    python scripts/manage_keys.py create-key --client-email acme@example.com --plan-name starter
    # -> prints the raw API key ONCE; only its hash is stored.

    python scripts/manage_keys.py revoke-key --key-prefix sk_AbCdEfGh
    python scripts/manage_keys.py extend-key --key-prefix sk_AbCdEfGh --days 30
    python scripts/manage_keys.py list-keys
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import generate_api_key, hash_api_key, key_prefix  # noqa: E402
from app.db.client import get_supabase  # noqa: E402


def create_plan(args: argparse.Namespace) -> None:
    supabase = get_supabase()
    result = (
        supabase.table("plans")
        .insert(
            {
                "name": args.name,
                "requests_per_minute": args.rpm,
                "daily_quota": args.daily_quota,
                "monthly_quota": args.monthly_quota,
                "duration_days": args.duration_days,
                "price_cents": args.price_cents,
            }
        )
        .execute()
    )
    print(f"Created plan: {result.data[0]}")


def create_client(args: argparse.Namespace) -> None:
    supabase = get_supabase()
    result = supabase.table("clients").insert({"name": args.name, "email": args.email}).execute()
    print(f"Created client: {result.data[0]}")


def create_key(args: argparse.Namespace) -> None:
    supabase = get_supabase()

    client = supabase.table("clients").select("id").eq("email", args.client_email).limit(1).execute()
    if not client.data:
        print(f"No client found with email {args.client_email}", file=sys.stderr)
        sys.exit(1)

    plan = supabase.table("plans").select("id, duration_days").eq("name", args.plan_name).limit(1).execute()
    if not plan.data:
        print(f"No plan found with name {args.plan_name}", file=sys.stderr)
        sys.exit(1)

    raw_key = generate_api_key()
    expires_at = datetime.now(timezone.utc) + timedelta(days=plan.data[0]["duration_days"])

    supabase.table("api_keys").insert(
        {
            "client_id": client.data[0]["id"],
            "plan_id": plan.data[0]["id"],
            "key_hash": hash_api_key(raw_key),
            "key_prefix": key_prefix(raw_key),
            "expires_at": expires_at.isoformat(),
        }
    ).execute()

    print("API key created. Store this now — it will not be shown again:")
    print(raw_key)
    print(f"Expires at: {expires_at.isoformat()}")


def revoke_key(args: argparse.Namespace) -> None:
    supabase = get_supabase()
    result = (
        supabase.table("api_keys").update({"status": "suspended"}).eq("key_prefix", args.key_prefix).execute()
    )
    print(f"Revoked {len(result.data)} key(s) matching prefix {args.key_prefix}")


def extend_key(args: argparse.Namespace) -> None:
    supabase = get_supabase()
    row = supabase.table("api_keys").select("id, expires_at").eq("key_prefix", args.key_prefix).limit(1).execute()
    if not row.data:
        print(f"No key found with prefix {args.key_prefix}", file=sys.stderr)
        sys.exit(1)

    current_expiry = datetime.fromisoformat(row.data[0]["expires_at"])
    new_expiry = current_expiry + timedelta(days=args.days)
    supabase.table("api_keys").update({"expires_at": new_expiry.isoformat(), "status": "active"}).eq(
        "id", row.data[0]["id"]
    ).execute()
    print(f"New expiry: {new_expiry.isoformat()}")


def list_keys(_: argparse.Namespace) -> None:
    supabase = get_supabase()
    rows = (
        supabase.table("api_keys")
        .select("key_prefix, status, expires_at, clients(name, email), plans(name)")
        .execute()
        .data
    )
    for row in rows:
        client = row.get("clients") or {}
        plan = row.get("plans") or {}
        print(
            f"{row['key_prefix']}  status={row['status']}  expires={row['expires_at']}  "
            f"client={client.get('email')}  plan={plan.get('name')}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("create-plan")
    p.add_argument("--name", required=True)
    p.add_argument("--rpm", type=int, default=30)
    p.add_argument("--daily-quota", type=int, default=1000)
    p.add_argument("--monthly-quota", type=int, default=20000)
    p.add_argument("--duration-days", type=int, default=30)
    p.add_argument("--price-cents", type=int, default=0)
    p.set_defaults(func=create_plan)

    p = subparsers.add_parser("create-client")
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    p.set_defaults(func=create_client)

    p = subparsers.add_parser("create-key")
    p.add_argument("--client-email", required=True)
    p.add_argument("--plan-name", required=True)
    p.set_defaults(func=create_key)

    p = subparsers.add_parser("revoke-key")
    p.add_argument("--key-prefix", required=True)
    p.set_defaults(func=revoke_key)

    p = subparsers.add_parser("extend-key")
    p.add_argument("--key-prefix", required=True)
    p.add_argument("--days", type=int, required=True)
    p.set_defaults(func=extend_key)

    p = subparsers.add_parser("list-keys")
    p.set_defaults(func=list_keys)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
