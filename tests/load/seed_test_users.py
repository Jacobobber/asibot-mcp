"""Seed test users for load testing.

Creates 200 test users with valid API keys in the Asibot data store and
outputs a CSV file (user_id,api_key) that Locust workers consume to
authenticate as real users.

Usage:
    python tests/load/seed_test_users.py [--users 200] [--output tests/load/test_users.csv]

Requires ASIBOT_DATA_DIR to point to the same directory the server uses.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys

# Ensure the src directory is importable when running standalone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from asibot import auth, token_store
from asibot.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Mock connector credentials so tool calls don't fail on missing creds.
# These are fake values, but they satisfy the schema validation in token_store
# so the server won't reject requests with "not connected to X" errors.
MOCK_CONNECTOR_CREDS: dict[str, dict[str, str]] = {
    "github": {"token": "ghp_loadtest_mock_token_0000000000000000"},
    "atlassian": {
        "email": "{email}",
        "api_token": "loadtest-atlassian-mock-token",
        "domain": "loadtest.atlassian.net",
    },
    "confluence": {
        "email": "{email}",
        "api_token": "loadtest-confluence-mock-token",
        "domain": "loadtest.atlassian.net",
    },
    "notion": {"token": "ntn_loadtest_mock_token_00000000000000000"},
    "zendesk": {
        "email": "{email}",
        "api_token": "loadtest-zendesk-mock-token",
        "subdomain": "loadtest",
    },
    "hubspot": {"token": "loadtest-hubspot-mock-token"},
    "figma": {"token": "loadtest-figma-mock-token"},
    "salesforce": {
        "token": "loadtest-sf-mock-token",
        "instance_url": "https://loadtest.my.salesforce.com",
    },
}


def seed_users(num_users: int, output_path: str) -> None:
    """Create test users and write their credentials to a CSV file."""
    settings.ensure_dirs()

    users_created = 0
    users_existing = 0
    rows: list[tuple[str, str]] = []

    for i in range(num_users):
        email = f"loadtest-user-{i:04d}@test.example.com"
        name = f"Load Test User {i}"

        user = auth.create_user(email, name)
        api_key = user["api_key"]

        if user.get("created_at"):
            # Determine if this is a new or existing user based on whether
            # create_user logged "already exists" -- we just count the result
            users_created += 1

        rows.append((user["user_id"], api_key))

        # Set up mock connector credentials so tool calls are exercised
        for service, creds_template in MOCK_CONNECTOR_CREDS.items():
            creds = {}
            for k, v in creds_template.items():
                creds[k] = v.format(email=email) if "{email}" in v else v
            token_store.set_credentials(email, service, creds)

        if (i + 1) % 50 == 0:
            logger.info("Seeded %d / %d users...", i + 1, num_users)

    # Write CSV
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "api_key"])
        for row in rows:
            writer.writerow(row)

    logger.info(
        "Seeding complete: %d users total, CSV written to %s",
        len(rows),
        output_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed test users for load testing")
    parser.add_argument("--users", type=int, default=200, help="Number of test users to create")
    parser.add_argument(
        "--output",
        default=os.path.join(os.path.dirname(__file__), "test_users.csv"),
        help="Output CSV path",
    )
    args = parser.parse_args()

    seed_users(args.users, args.output)


if __name__ == "__main__":
    main()
