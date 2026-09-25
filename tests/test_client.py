import asyncio

from interdictor.client import SapientEffectorClient
from interdictor.framing import encode_frame
from interdictor.ids import new_ulid
from interdictor.proto import SapientMessage, Task, TaskAck


class FakeWriter:
    def __init__(self):
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        pass


def _make_client() -> SapientEffectorClient:
    cfg = {
        "fusion_node": {"host": "127.0.0.1", "port": 0},
        "node": {"node_id": "test-node", "registration_file": "config/registration.json"},
        "status": {
            "interval_s": 5.0,
            "default_mode": "STANDBY",
            "node_location": {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0},
        },
        "jamming": {
            "profiles": [
                {
                    "mode_name": "JAM_GNSS_L1",
                    "description": "test",
                    "centre_frequency": 1575420000.0,
                    "bandwidth": 4000000.0,
                    "tx_power_dbm": 30.0,
                }
            ]
        },
    }
    client = SapientEffectorClient(cfg)
    client.writer = FakeWriter()  # type: ignore[assignment]
    return client


def _sent_messages(client: SapientEffectorClient) -> list[SapientMessage]:
    messages = []
    for frame in client.writer.frames:  # type: ignore[union-attr]
        length = int.from_bytes(frame[:4], "little")
        msg = SapientMessage()
        msg.ParseFromString(frame[4 : 4 + length])
        messages.append(msg)
    return messages


def _start_task(mode_change: str) -> Task:
    task = Task()
    task.task_id = new_ulid()
    task.control = Task.CONTROL_START
    task.command.mode_change = mode_change
    return task


def test_mode_change_to_known_mode_is_accepted_and_switches_jammer():
    client = _make_client()
    task = _start_task("JAM_GNSS_L1")

    asyncio.run(client.handle_task(task))

    assert client.jammer.mode == "JAM_GNSS_L1"
    assert client.active_task_id == task.task_id
    sent = _sent_messages(client)
    assert [m.WhichOneof("content") for m in sent] == ["task_ack", "status_report"]
    assert sent[0].task_ack.task_status == TaskAck.TASK_STATUS_ACCEPTED
    assert sent[1].status_report.mode == "JAM_GNSS_L1"
    assert sent[1].status_report.active_task_id == task.task_id


def test_mode_change_to_unknown_mode_is_rejected():
    client = _make_client()
    task = _start_task("NOT_A_MODE")

    asyncio.run(client.handle_task(task))

    assert client.jammer.mode == "STANDBY"
    assert client.active_task_id is None
    sent = _sent_messages(client)
    assert len(sent) == 1
    assert sent[0].task_ack.task_status == TaskAck.TASK_STATUS_REJECTED
    assert "unsupported mode" in list(sent[0].task_ack.reason)


def test_second_concurrent_task_is_rejected():
    client = _make_client()
    asyncio.run(client.handle_task(_start_task("JAM_GNSS_L1")))

    second = _start_task("JAM_GNSS_L1")
    asyncio.run(client.handle_task(second))

    sent = _sent_messages(client)
    assert sent[-1].task_ack.task_status == TaskAck.TASK_STATUS_REJECTED
    assert "concurrent task limit" in list(sent[-1].task_ack.reason)


def test_stop_on_active_task_reverts_to_default_mode():
    client = _make_client()
    start = _start_task("JAM_GNSS_L1")
    asyncio.run(client.handle_task(start))

    stop = Task()
    stop.task_id = start.task_id
    stop.control = Task.CONTROL_STOP
    asyncio.run(client.handle_task(stop))

    assert client.jammer.mode == "STANDBY"
    assert client.active_task_id is None
    sent = _sent_messages(client)
    assert sent[-2].task_ack.task_status == TaskAck.TASK_STATUS_ACCEPTED


def test_stop_on_unknown_task_is_rejected():
    client = _make_client()
    stop = Task()
    stop.task_id = new_ulid()
    stop.control = Task.CONTROL_STOP

    asyncio.run(client.handle_task(stop))

    sent = _sent_messages(client)
    assert sent[0].task_ack.task_status == TaskAck.TASK_STATUS_REJECTED
    assert "resource unavailable" in list(sent[0].task_ack.reason)


def test_out_of_scope_command_is_rejected():
    client = _make_client()
    task = Task()
    task.task_id = new_ulid()
    task.control = Task.CONTROL_START
    task.command.look_at.SetInParent()

    asyncio.run(client.handle_task(task))

    sent = _sent_messages(client)
    assert sent[0].task_ack.task_status == TaskAck.TASK_STATUS_REJECTED
    assert "unsupported command" in list(sent[0].task_ack.reason)


def test_request_command_triggers_immediate_status():
    client = _make_client()
    task = Task()
    task.task_id = new_ulid()
    task.control = Task.CONTROL_START
    task.command.request = "status"

    asyncio.run(client.handle_task(task))

    sent = _sent_messages(client)
    assert [m.WhichOneof("content") for m in sent] == ["task_ack", "status_report"]
    assert sent[0].task_ack.task_status == TaskAck.TASK_STATUS_ACCEPTED


def test_mode_change_is_recorded_in_mode_history_and_surfaced_on_status():
    client = _make_client()
    task = _start_task("JAM_GNSS_L1")

    asyncio.run(client.handle_task(task))

    transition = client.mode_history.last
    assert transition is not None
    assert transition.from_mode == "STANDBY"
    assert transition.to_mode == "JAM_GNSS_L1"
    assert transition.task_id == task.task_id

    sent = _sent_messages(client)
    status_entries = list(sent[1].status_report.status)
    assert len(status_entries) == 1
    assert "STANDBY -> JAM_GNSS_L1" in status_entries[0].status_value


def test_mode_change_to_unknown_mode_does_not_record_a_transition():
    client = _make_client()
    task = _start_task("NOT_A_MODE")

    asyncio.run(client.handle_task(task))

    assert client.mode_history.last is None


def test_stop_reverting_to_default_records_a_transition():
    client = _make_client()
    start = _start_task("JAM_GNSS_L1")
    asyncio.run(client.handle_task(start))

    stop = Task()
    stop.task_id = start.task_id
    stop.control = Task.CONTROL_STOP
    asyncio.run(client.handle_task(stop))

    transition = client.mode_history.last
    assert transition.from_mode == "JAM_GNSS_L1"
    assert transition.to_mode == "STANDBY"


def test_net_stats_track_sent_messages_and_types():
    client = _make_client()
    task = _start_task("JAM_GNSS_L1")

    asyncio.run(client.handle_task(task))

    assert client.net_stats.messages_sent == 2
    assert client.net_stats.sent_by_type == {"task_ack": 1, "status_report": 1}
    assert client.net_stats.bytes_sent > 0
    assert client.net_stats.send_errors == 0


class FakeReader:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def readexactly(self, n: int) -> bytes:
        if self._pos + n > len(self._data):
            raise asyncio.IncompleteReadError(self._data[self._pos :], n)
        chunk = self._data[self._pos : self._pos + n]
        self._pos += n
        return chunk


class FailingWriter:
    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        raise ConnectionResetError("connection reset")


def _encode_task_frame(task: Task) -> bytes:
    msg = SapientMessage()
    msg.timestamp.GetCurrentTime()
    msg.node_id = "fusion-node"
    msg.task.CopyFrom(task)
    return encode_frame(msg.SerializeToString())


def test_receive_loop_records_send_error_and_sets_conn_lost_when_task_ack_fails():
    client = _make_client()
    client.writer = FailingWriter()  # type: ignore[assignment]
    task = _start_task("JAM_GNSS_L1")
    client.reader = FakeReader(_encode_task_frame(task))  # type: ignore[assignment]

    conn_lost = asyncio.Event()
    asyncio.run(client.receive_loop(conn_lost))

    assert conn_lost.is_set()
    assert client.net_stats.send_errors == 1
    assert client.net_stats.last_error == "connection reset"


def test_receive_loop_records_received_message_and_type():
    client = _make_client()
    client.writer = FakeWriter()
    task = _start_task("JAM_GNSS_L1")
    client.reader = FakeReader(_encode_task_frame(task))  # type: ignore[assignment]

    conn_lost = asyncio.Event()
    asyncio.run(client.receive_loop(conn_lost))

    assert client.net_stats.messages_received == 1
    assert client.net_stats.received_by_type == {"task": 1}
