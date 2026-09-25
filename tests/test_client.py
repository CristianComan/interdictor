import asyncio

from interdictor.client import SapientEffectorClient
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
