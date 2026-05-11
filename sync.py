"""
Garmin COM <-> CN Data Sync Tool

Usage:
  python sync.py

First run will prompt for credentials if config.yaml has no tokens.
Subsequent runs reuse saved tokens automatically.
"""

import json
import logging
import sys

import yaml

from garmin_sync.sync import load_sync_state, run_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def load_config() -> dict:
    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print("Error: config.yaml not found.")
        print("Create one based on config.yaml.example:")
        print("  cp config.yaml.example config.yaml")
        sys.exit(1)


def main():
    config = load_config()
    direction = config.get("sync_direction", "COM_TO_CN")
    com = config.get("garmin_com", {})
    cn = config.get("garmin_cn", {})

    com_email = com.get("email", "")
    com_password = com.get("password", "")
    cn_email = cn.get("email", "")
    cn_password = cn.get("password", "")

    if not com_email or not cn_email:
        print("Error: Please fill in email and password for both accounts in config.yaml")
        sys.exit(1)

    # Try to reuse saved tokens
    state = load_sync_state()
    com_token = state.get("com_token")
    cn_token = state.get("cn_token")

    print(f"Sync direction: {direction}")
    if com_token:
        print("COM: reusing saved token")
    else:
        print("COM: will login with credentials")
    if cn_token:
        print("CN: reusing saved token")
    else:
        print("CN: will login with credentials")

    result = run_sync(
        com_email=com_email,
        com_password=com_password,
        com_token=com_token,
        cn_email=cn_email,
        cn_password=cn_password,
        cn_token=cn_token,
        direction=direction,
    )
    print(f"\nDone! Synced: {result['synced']}, Failed: {result['failed']}")


if __name__ == "__main__":
    main()
