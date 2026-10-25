"""A timed-out operation cannot disconnect a subsequent user of the native client."""

import threading
from types import SimpleNamespace

from s7comm_mcp import s7_client
from s7comm_mcp.s7_client import S7Client, _Call


def test_interrupt_and_native_session_handoff_are_fenced(monkeypatch):
    client = S7Client()
    old, new = _Call(), _Call()
    entered, finish_old, interrupting, finish_interrupt, next_entered = (threading.Event() for _ in range(5))
    disconnected_owners = []
    client._client = SimpleNamespace(disconnect=lambda: disconnected_owners.append(client._active_call))
    client._connected = True

    def log(*args):
        interrupting.set()
        finish_interrupt.wait(2)

    monkeypatch.setattr(s7_client.logger, "warning", log)

    def first_operation(native):
        entered.set()
        finish_old.wait(2)

    a = threading.Thread(target=lambda: client._execute("first", first_operation, call=old))
    a.start()
    assert entered.wait(2)
    b = threading.Thread(target=lambda: client._interrupt(old))
    b.start()
    assert interrupting.wait(2)
    finish_old.set()
    c = threading.Thread(target=lambda: client._execute("next", lambda native: next_entered.set(), call=new))
    c.start()
    try:
        assert not next_entered.wait(0.05)
    finally:
        finish_interrupt.set()
        for thread in (a, b, c):
            thread.join(2)
    assert disconnected_owners == [old]
    assert next_entered.is_set()
    assert not any(thread.is_alive() for thread in (a, b, c))
