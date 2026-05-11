"""
Garmin Connect client wrapper using garminconnect library.
Supports both International (COM) and China (CN) regions.
"""

import logging
import time
from pathlib import Path

from garminconnect import Garmin

logger = logging.getLogger(__name__)


class GarminClient:
    """Wrapper around garminconnect.Garmin with token persistence."""

    def __init__(self, email: str, password: str, is_cn: bool = False):
        self.email = email
        self.password = password
        self.is_cn = is_cn
        self.client = Garmin(email, password, is_cn=is_cn)
        self._logged_in = False

    def login(self, tokenstore: str | None = None):
        """Login using tokenstore (token string) or credentials."""
        try:
            self.client.login(tokenstore=tokenstore)
            self._logged_in = True
            logger.info(f"Logged in to Garmin {'CN' if self.is_cn else 'COM'} successfully")
        except Exception as e:
            logger.error(f"Login failed for {'CN' if self.is_cn else 'COM'}: {e}")
            raise

    def get_session_token(self) -> str:
        """Get serializable session token for later reuse."""
        return self.client.garth.client.dumps()

    def get_activities(self, start: int = 0, limit: int = 100) -> list:
        """Get activity list."""
        return self.client.get_activities(start, limit)

    def get_all_activity_ids(self) -> list[str]:
        """Fetch all activity IDs with pagination."""
        all_ids = []
        start = 0
        while True:
            activities = self.client.get_activities(start, 100)
            if not activities:
                break
            ids = [str(a["activityId"]) for a in activities]
            all_ids.extend(ids)
            logger.info(f"Fetched {len(ids)} activities (total: {len(all_ids)})")
            if len(activities) < 100:
                break
            start += 100
            time.sleep(0.5)
        return all_ids

    def download_activity_fit(self, activity_id: int, output_dir: Path) -> Path | None:
        """Download activity as FIT file. Returns path to extracted .fit file."""
        try:
            data = self.client.download_activity(
                str(activity_id),
                dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            zip_path = output_dir / f"{activity_id}.zip"
            zip_path.write_bytes(data)

            # Extract .fit from zip
            import zipfile
            with zipfile.ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    if name.endswith(".fit"):
                        extracted = output_dir / f"{activity_id}.fit"
                        extracted.write_bytes(zf.read(name))
                        zip_path.unlink(missing_ok=True)
                        return extracted

            zip_path.unlink(missing_ok=True)
            return None
        except Exception as e:
            logger.warning(f"FIT download failed for {activity_id}: {e}")
            return None

    def download_activity_gpx(self, activity_id: int, output_dir: Path) -> Path | None:
        """Download activity as GPX file. Returns path to .gpx file."""
        try:
            data = self.client.download_activity(
                str(activity_id),
                dl_fmt=Garmin.ActivityDownloadFormat.GPX,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            gpx_path = output_dir / f"{activity_id}.gpx"
            gpx_path.write_bytes(data)
            return gpx_path
        except Exception as e:
            logger.warning(f"GPX download failed for {activity_id}: {e}")
            return None

    def upload_activity(self, file_path: Path) -> dict:
        """Upload activity file to this account."""
        result = self.client.upload_activity(str(file_path))
        logger.info(f"Uploaded {file_path.name}: {result}")
        return result
