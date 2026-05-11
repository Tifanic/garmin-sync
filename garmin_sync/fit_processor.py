"""
FIT file processor - injects Garmin device info and fixes heart rate data.
Adapted from running_page's garmin_device_adaptor.py.
"""

import io
import logging
import zipfile

logger = logging.getLogger(__name__)

try:
    from fit_tool.fit_file import FitFile
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
    from fit_tool.profile.messages.record_message import RecordMessage

    FIT_TOOL_AVAILABLE = True
except ImportError:
    FIT_TOOL_AVAILABLE = False

# Garmin device identifiers
MANUFACTURER = 1  # Garmin
PRODUCT_ID = 3415  # Forerunner 245
SOFTWARE_VERSION = 3.58
SERIAL_NUMBER = 1234567890


def extract_fit_from_zip(zip_data: bytes) -> bytes | None:
    """Extract .fit file from Garmin's zip download."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if name.endswith(".fit"):
                    return zf.read(name)
    except zipfile.BadZipFile:
        pass
    return None


def process_fit_file(file_data: bytes) -> bytes:
    """
    Process a FIT file: inject Garmin device info and fix HR data.
    If fit-tool is not available, returns original data.
    """
    if not FIT_TOOL_AVAILABLE:
        logger.warning("fit-tool not available, skipping FIT processing")
        return file_data

    try:
        return _do_process(file_data)
    except Exception as e:
        logger.error(f"FIT processing failed, using original: {e}")
        return file_data


def _do_process(file_data: bytes) -> bytes:
    fit_file = FitFile.from_bytes(file_data)
    builder = FitFileBuilder(auto_define=True)

    record_messages = []

    for record in fit_file.records:
        message = record.message
        # Remove existing device info (e.g. from third-party apps)
        if isinstance(message, DeviceInfoMessage):
            continue
        if not isinstance(message, RecordMessage):
            builder.add(message)
        else:
            record_messages.append(message)

    # Inject Garmin device info
    device_info = DeviceInfoMessage()
    device_info.serial_number = SERIAL_NUMBER
    device_info.manufacturer = MANUFACTURER
    device_info.garmin_product = PRODUCT_ID
    device_info.software_version = SOFTWARE_VERSION
    device_info.device_index = 0
    device_info.source_type = 5
    device_info.product = PRODUCT_ID
    builder.add(device_info)

    # Fix heart rate data
    for msg in _fix_heart_rate(record_messages):
        builder.add(msg)

    return builder.build().to_bytes()


def _fix_heart_rate(messages: list[RecordMessage]) -> list[RecordMessage]:
    """Replace None/255 HR values with nearest valid value."""
    result = []
    for i, msg in enumerate(messages):
        if msg.heart_rate is None or msg.heart_rate == 255:
            valid_hr = _find_valid_hr(messages, i)
            if valid_hr is not None:
                new_msg = RecordMessage()
                for field in msg.fields:
                    name = field.name
                    if hasattr(msg, name):
                        val = valid_hr if name == "heart_rate" else getattr(msg, name)
                        if val is not None:
                            setattr(new_msg, name, val)
                result.append(new_msg)
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


def _find_valid_hr(messages: list, index: int) -> int | None:
    """Find nearest valid heart rate value."""
    for msg in messages[index + 1:]:
        if msg.heart_rate is not None and msg.heart_rate != 255:
            return msg.heart_rate
    for msg in reversed(messages[:index]):
        if msg.heart_rate is not None and msg.heart_rate != 255:
            return msg.heart_rate
    return None
