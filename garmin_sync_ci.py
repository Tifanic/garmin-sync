"""
Garmin COM -> CN sync script for GitHub Actions.
- COM: uses garminconnect library (login works on overseas servers)
- CN: uses garth library with garmin.cn domain (avoids mobile.integration.garmin.com)
- Upload to CN: uses httpx directly (like running_page pattern)
"""

import asyncio
import json
import logging
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

SYNC_STATE_FILE = Path("sync_state.json")

# CN API endpoints (from running_page)
CN_MODERN_URL = "https://connectapi.garmin.cn"
CN_UPLOAD_URL = "https://connectapi.garmin.cn/upload-service/upload/"


def load_sync_state() -> dict:
    if SYNC_STATE_FILE.exists():
        return json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
    return {"synced_ids": []}


def save_sync_state(state: dict):
    state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SYNC_STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def login_cn_with_garth(email: str, password: str) -> tuple:
    """Login to Garmin CN using garth (proper garmin.cn domain support)."""
    import garth

    garth.configure(domain="garmin.cn", ssl_verify=False)
    garth.login(email, password)

    token = garth.client.oauth2_token
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "origin": "https://sso.garmin.com",
        "nk": "NT",
        "Authorization": str(token),
    }
    return headers


def upload_to_cn(headers: dict, file_data: bytes, filename: str) -> dict:
    """Upload activity file to Garmin CN using httpx."""
    files = {"file": (filename, file_data)}
    resp = httpx.post(CN_UPLOAD_URL, files=files, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def try_process_fit(file_data: bytes) -> bytes:
    """Inject Garmin device info into FIT file."""
    try:
        from fit_tool.fit_file import FitFile
        from fit_tool.fit_file_builder import FitFileBuilder
        from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
        from fit_tool.profile.messages.record_message import RecordMessage

        fit_file = FitFile.from_bytes(file_data)
        builder = FitFileBuilder(auto_define=True)
        records = []

        for record in fit_file.records:
            msg = record.message
            if isinstance(msg, DeviceInfoMessage):
                continue
            if isinstance(msg, RecordMessage):
                records.append(msg)
            else:
                builder.add(msg)

        dev = DeviceInfoMessage()
        dev.serial_number = 1234567890
        dev.manufacturer = 1
        dev.garmin_product = 3415
        dev.software_version = 3.58
        dev.device_index = 0
        dev.source_type = 5
        dev.product = 3415
        builder.add(dev)

        for i, msg in enumerate(records):
            if msg.heart_rate is None or msg.heart_rate == 255:
                hr = _find_valid_hr(records, i)
                if hr is not None:
                    new_msg = RecordMessage()
                    for f in msg.fields:
                        name = f.name
                        if hasattr(msg, name):
                            val = hr if name == "heart_rate" else getattr(msg, name)
                            if val is not None:
                                setattr(new_msg, name, val)
                    builder.add(new_msg)
                else:
                    builder.add(msg)
            else:
                builder.add(msg)

        result = builder.build().to_bytes()
        logger.info("FIT processing succeeded")
        return result
    except Exception as e:
        logger.warning(f"FIT processing failed, using original: {e}")
        return file_data


def _find_valid_hr(messages, index):
    for m in messages[index + 1:]:
        if m.heart_rate is not None and m.heart_rate != 255:
            return m.heart_rate
    for m in reversed(messages[:index]):
        if m.heart_rate is not None and m.heart_rate != 255:
            return m.heart_rate
    return None


def extract_fit_from_zip(zip_data: bytes) -> bytes | None:
    try:
        with zipfile.ZipFile(BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if name.endswith(".fit"):
                    return zf.read(name)
    except zipfile.BadZipFile:
        pass
    return None


def main():
    com_email = sys.argv[1] if len(sys.argv) > 1 else ""
    com_password = sys.argv[2] if len(sys.argv) > 2 else ""
    cn_email = sys.argv[3] if len(sys.argv) > 3 else ""
    cn_password = sys.argv[4] if len(sys.argv) > 4 else ""

    if not all([com_email, com_password, cn_email, cn_password]):
        print("Usage: python garmin_sync_ci.py <com_email> <com_password> <cn_email> <cn_password>")
        sys.exit(1)

    # Login to COM (garminconnect)
    logger.info("Logging in to Garmin COM...")
    from garminconnect import Garmin

    source = Garmin(com_email, com_password, is_cn=False)
    source.login()
    logger.info("COM login OK")

    # Login to CN (garth - proper garmin.cn support)
    logger.info("Logging in to Garmin CN...")
    cn_headers = login_cn_with_garth(cn_email, cn_password)
    logger.info("CN login OK")

    # Only sync activities from 2026 onwards
    from datetime import datetime, timezone
    SYNC_START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Get all activity IDs from COM (stop when activities are older than 2026)
    all_ids = []
    start = 0
    while True:
        activities = source.get_activities(start, 100)
        if not activities:
            break
        for a in activities:
            start_time = a.get("startTimeGMT", "")
            if start_time:
                try:
                    t = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    if t < SYNC_START_DATE:
                        logger.info(f"Reached activity before 2026, stopping pagination")
                        activities = None
                        break
                except ValueError:
                    pass
            if activities is not None:
                all_ids.append(str(a["activityId"]))
        if activities is None:
            break
        if len(activities) < 100:
            break
        start += 100
        time.sleep(0.5)
    logger.info(f"Found {len(all_ids)} activities from 2026 on COM")

    # Load synced state
    state = load_sync_state()
    synced_ids = set(state.get("synced_ids", []))
    new_ids = [aid for aid in all_ids if aid not in synced_ids]
    logger.info(f"Already synced: {len(synced_ids)}, New: {len(new_ids)}")

    if not new_ids:
        logger.info("Nothing to sync")
        save_sync_state(state)
        return

    # Download from COM, upload to CN
    tmp_dir = Path("data/tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    failed = 0

    for i, aid in enumerate(new_ids):
        logger.info(f"[{i+1}/{len(new_ids)}] Syncing activity {aid}...")

        file_data = None
        filename = None

        # Try FIT first
        try:
            raw = source.download_activity(aid, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
            fit_data = extract_fit_from_zip(raw)
            if fit_data:
                file_data = try_process_fit(fit_data)
                filename = f"{aid}.fit"
        except Exception as e:
            logger.warning(f"FIT download failed: {e}")

        # Fallback to GPX
        if file_data is None:
            try:
                file_data = source.download_activity(aid, dl_fmt=Garmin.ActivityDownloadFormat.GPX)
                filename = f"{aid}.gpx"
            except Exception as e:
                logger.error(f"GPX download also failed: {e}")
                failed += 1
                continue

        # Upload to CN via httpx
        try:
            result = upload_to_cn(cn_headers, file_data, filename)
            logger.info(f"Uploaded: {result.get('detailedImportResult', result)}")
            synced_ids.add(aid)
            success += 1
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            failed += 1

        time.sleep(1)

    state["synced_ids"] = sorted(synced_ids)
    save_sync_state(state)
    logger.info(f"Done! Synced: {success}, Failed: {failed}")


if __name__ == "__main__":
    main()
