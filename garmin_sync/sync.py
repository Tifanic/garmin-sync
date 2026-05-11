"""
Core sync logic: download activities from source, upload to target.
Tracks synced state in sync_state.json.
"""

import json
import logging
import time
from pathlib import Path

from .client import GarminClient
from .fit_processor import process_fit_file

logger = logging.getLogger(__name__)

SYNC_STATE_FILE = Path("sync_state.json")
DATA_DIR = Path("data")


def load_sync_state() -> dict:
    if SYNC_STATE_FILE.exists():
        return json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
    return {"synced_ids": [], "last_sync": None}


def save_sync_state(state: dict):
    state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SYNC_STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def run_sync(
    com_email: str,
    com_password: str,
    com_token: str | None,
    cn_email: str,
    cn_password: str,
    cn_token: str | None,
    direction: str = "COM_TO_CN",
):
    """
    Sync activities between Garmin COM and CN.

    Args:
        com_email, com_password: International account credentials
        com_token: Previously saved COM session token (optional)
        cn_email, cn_password: China account credentials
        cn_token: Previously saved CN session token (optional)
        direction: "COM_TO_CN" or "CN_TO_COM"
    """
    if direction == "CN_TO_COM":
        source_kwargs = dict(email=cn_email, password=cn_password, is_cn=True)
        target_kwargs = dict(email=com_email, password=com_password, is_cn=False)
        source_token, target_token = cn_token, com_token
        source_label, target_label = "CN", "COM"
    else:
        source_kwargs = dict(email=com_email, password=com_password, is_cn=False)
        target_kwargs = dict(email=cn_email, password=cn_password, is_cn=True)
        source_token, target_token = com_token, cn_token
        source_label, target_label = "COM", "CN"

    logger.info(f"Sync: {source_label} -> {target_label}")

    # Login
    source = GarminClient(**source_kwargs)
    source.login(tokenstore=source_token)
    target = GarminClient(**target_kwargs)
    target.login(tokenstore=target_token)

    # Save fresh tokens for next run
    state = load_sync_state()
    state["com_token"] = source.get_session_token() if not source.is_cn else state.get("com_token")
    state["cn_token"] = target.get_session_token() if target.is_cn else state.get("cn_token")
    # Make sure both tokens are saved
    if source_label == "COM":
        state["com_token"] = source.get_session_token()
        state["cn_token"] = target.get_session_token()
    else:
        state["com_token"] = target.get_session_token()
        state["cn_token"] = source.get_session_token()

    # Get activity IDs
    all_ids = source.get_all_activity_ids()
    synced_ids = set(state.get("synced_ids", []))
    new_ids = [aid for aid in all_ids if aid not in synced_ids]
    logger.info(f"Total: {len(all_ids)}, Already synced: {len(synced_ids)}, New: {len(new_ids)}")

    if not new_ids:
        logger.info("Nothing to sync")
        save_sync_state(state)
        return {"synced": 0, "failed": 0}

    # Download, process, upload
    success_count = 0
    fail_count = 0
    tmp_dir = DATA_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for i, aid in enumerate(new_ids):
        logger.info(f"[{i+1}/{len(new_ids)}] Processing activity {aid}...")

        # Download FIT (preferred) or GPX
        file_path = source.download_activity_fit(int(aid), tmp_dir)
        if file_path is None:
            file_path = source.download_activity_gpx(int(aid), tmp_dir)
        if file_path is None:
            logger.error(f"Failed to download activity {aid}")
            fail_count += 1
            continue

        # Process FIT files (inject device info, fix HR)
        if file_path.suffix == ".fit":
            try:
                processed = process_fit_file(file_path.read_bytes())
                file_path.write_bytes(processed)
            except Exception as e:
                logger.warning(f"FIT processing failed for {aid}, uploading original: {e}")

        # Upload to target
        try:
            target.upload_activity(file_path)
            synced_ids.add(aid)
            success_count += 1
        except Exception as e:
            logger.error(f"Upload failed for {aid}: {e}")
            fail_count += 1
        finally:
            file_path.unlink(missing_ok=True)

        # Rate limit: wait between uploads
        if i < len(new_ids) - 1:
            time.sleep(1)

    # Save state
    state["synced_ids"] = sorted(synced_ids)
    save_sync_state(state)

    logger.info(f"Sync complete: {success_count} succeeded, {fail_count} failed")
    return {"synced": success_count, "failed": fail_count}
