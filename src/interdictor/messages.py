from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from google.protobuf.json_format import ParseDict
from google.protobuf.timestamp_pb2 import Timestamp

from .ids import new_ulid
from .mode_history import ModeTransition
from .proto import SapientMessage, Registration, StatusReport, TaskAck


def _now_timestamp() -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


def _wrap(node_id: str, field_name: str, body) -> SapientMessage:
    msg = SapientMessage()
    msg.timestamp.CopyFrom(_now_timestamp())
    msg.node_id = node_id
    getattr(msg, field_name).CopyFrom(body)
    return msg


def build_registration(node_id: str, registration_file: str | Path) -> SapientMessage:
    body = Registration()
    data = json.loads(Path(registration_file).read_text(encoding="utf-8"))
    ParseDict(data, body, ignore_unknown_fields=False)
    return _wrap(node_id, "registration", body)


def _set_wgs84_location(location_msg, latitude: float, longitude: float, altitude: float) -> None:
    """Populate the BSI Flex 335 v2 Location message using WGS84 lat/lon degrees/metres."""
    # In the v2 schema: x = longitude, y = latitude, z = altitude.
    location_msg.x = longitude
    location_msg.y = latitude
    location_msg.z = altitude
    location_msg.coordinate_system = 1  # LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M
    location_msg.datum = 1              # LOCATION_DATUM_WGS84_E


def build_status(
    node_id: str,
    cfg: dict,
    mode: str,
    active_task_id: str | None,
    last_transition: ModeTransition | None = None,
) -> SapientMessage:
    body = StatusReport()
    body.report_id = new_ulid()
    body.system = StatusReport.SYSTEM_OK
    body.info = StatusReport.INFO_NEW
    body.mode = mode
    if active_task_id:
        body.active_task_id = active_task_id

    loc = cfg["status"].get("node_location")
    if loc:
        _set_wgs84_location(
            body.node_location,
            float(loc["latitude"]),
            float(loc["longitude"]),
            float(loc.get("altitude", 0.0)),
        )

    if last_transition is not None:
        # Surfaces the latest Tasking-driven mode change to the Fusion Node /
        # C2 UI, not just local logs - see mode_history.ModeHistory.
        status_entry = body.status.add()
        status_entry.status_level = StatusReport.STATUS_LEVEL_INFORMATION_STATUS
        status_entry.status_type = StatusReport.STATUS_TYPE_OTHER
        reason = f" (task={last_transition.task_id})" if last_transition.task_id else ""
        status_entry.status_value = (
            f"Mode change: {last_transition.from_mode} -> {last_transition.to_mode}{reason}"
        )

    return _wrap(node_id, "status_report", body)


def build_task_ack(
    node_id: str, task_id: str, status: int, reasons: tuple[str, ...] = ()
) -> SapientMessage:
    body = TaskAck()
    body.task_id = task_id
    body.task_status = status
    body.reason.extend(reasons)
    return _wrap(node_id, "task_ack", body)
