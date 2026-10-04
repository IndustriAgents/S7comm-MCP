"""Simple Siemens S7 mock PLC built on python-snap7."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ipaddress
import os
import random
import signal
import sys
from dataclasses import dataclass

try:
    import snap7  # type: ignore
    from snap7 import util as snap7_util  # type: ignore
    from snap7.type import SrvArea  # type: ignore
except ImportError:  # pragma: no cover - runtime guard
    snap7 = None
    snap7_util = None
    SrvArea = None  # type: ignore[assignment]


def log(message: str) -> None:
    """Print a status line immediately, even when stdout is a pipe or a file."""
    print(f"[mock-s7] {message}", flush=True)


@dataclass
class MockConfig:
    # Plain defaults: the environment is read in parse_args(), at run time.
    host: str = "0.0.0.0"
    port: int = 1102
    update_interval: float = 1.0
    motor_speed: float = 1450.0
    temp_start: float = 68.0


class MockS7Server:
    """Registers deterministic memory areas and mutates them over time."""

    def __init__(self, config: MockConfig) -> None:
        if snap7 is None:
            raise RuntimeError("python-snap7 is required for the mock server. Install snap7 first.")
        self.config = config
        self.server = snap7.server.Server()  # type: ignore[attr-defined]
        self.db1 = bytearray(256)  # telemetry
        self.db2 = bytearray(128)  # alarms/config
        self.db3 = bytearray(1024)  # bulk/recipe area, larger than one S7 PDU
        self.inputs = bytearray(8)
        self.outputs = bytearray(8)
        self.markers = bytearray(8)

        # ctypes views used by snap7 server; backed by the bytearrays above.
        self._db1_buf = (ctypes.c_uint8 * len(self.db1)).from_buffer(self.db1)
        self._db2_buf = (ctypes.c_uint8 * len(self.db2)).from_buffer(self.db2)
        self._db3_buf = (ctypes.c_uint8 * len(self.db3)).from_buffer(self.db3)
        self._inputs_buf = (ctypes.c_uint8 * len(self.inputs)).from_buffer(self.inputs)
        self._outputs_buf = (ctypes.c_uint8 * len(self.outputs)).from_buffer(self.outputs)
        self._markers_buf = (ctypes.c_uint8 * len(self.markers)).from_buffer(self.markers)
        self._running = False
        self._update_task: asyncio.Task[None] | None = None

    def _register_areas(self) -> None:
        self.server.register_area(SrvArea.DB, 1, self._db1_buf)
        self.server.register_area(SrvArea.DB, 2, self._db2_buf)
        self.server.register_area(SrvArea.DB, 3, self._db3_buf)
        self.server.register_area(SrvArea.PE, 0, self._inputs_buf)
        self.server.register_area(SrvArea.PA, 0, self._outputs_buf)
        self.server.register_area(SrvArea.MK, 0, self._markers_buf)

    def seed(self) -> None:
        snap7_util.set_real(self.db1, 0, self.config.motor_speed)  # MotorSpeed
        snap7_util.set_real(self.db1, 4, 42.0)  # MotorTorque
        snap7_util.set_real(self.db1, 8, 22.5)  # Flow
        snap7_util.set_dint(self.db1, 12, 12345678)  # Batch counter
        snap7_util.set_bool(self.db2, 0, 0, True)  # Alarm bit
        snap7_util.set_string(self.db2, 2, "OK", 30)  # STRING[30]: max 30, length 2
        snap7_util.set_bool(self.outputs, 0, 0, False)  # Pump command

    async def start(self) -> None:
        if self._running:
            return
        self._register_areas()
        self.seed()
        # start_to binds the requested interface; start() would bind 0.0.0.0.
        self.server.start_to(self.config.host, tcp_port=self.config.port)
        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())
        log(f"Listening on {self.config.host}:{self.config.port}")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._update_task is not None:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        self.server.stop()
        self.server.destroy()

    async def _update_loop(self) -> None:
        """Continuously mutate the in-memory areas."""
        phase = 0.0
        while self._running:
            phase += 0.1
            speed = self.config.motor_speed + 20.0 * (random.random() - 0.5)
            temp = self.config.temp_start + 5.0 * (random.random() - 0.5)
            snap7_util.set_real(self.db1, 0, speed)
            snap7_util.set_real(self.db1, 16, temp)
            snap7_util.set_bool(self.inputs, 0, 0, bool(int(phase) % 2))
            snap7_util.set_bool(self.inputs, 0, 1, bool(int(phase * 2) % 2))
            snap7_util.set_bool(self.markers, 0, 0, True)
            await asyncio.sleep(self.config.update_interval)


def _ipv4(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an IPv4 address (Snap7 binds IPv4 only)") from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mock Siemens S7 server using python-snap7.")
    parser.add_argument(
        "--host",
        type=_ipv4,
        default=os.getenv("MOCK_S7_HOST", "0.0.0.0"),
        help="IPv4 address to bind (default 0.0.0.0, all interfaces; use 127.0.0.1 for local only)",
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("MOCK_S7_PORT", "1102")))
    parser.add_argument("--update-interval", type=float, default=float(os.getenv("MOCK_S7_UPDATE_INTERVAL", "1.0")))
    parser.add_argument("--motor-speed", type=float, default=float(os.getenv("MOCK_S7_MOTOR_SPEED", "1450.0")))
    parser.add_argument("--temp-start", type=float, default=float(os.getenv("MOCK_S7_TEMP_START", "68.0")))
    return parser.parse_args(argv)


async def run_server(config: MockConfig) -> None:
    server = MockS7Server(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - e.g. Windows
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    await server.start()
    try:
        await stop.wait()
    finally:
        log("Shutting down...")
        await server.stop()


def main(argv: list[str] | None = None) -> None:
    if snap7 is None:
        raise SystemExit("python-snap7 is not installed. Install snap7 to run the mock PLC.")
    args = parse_args(argv)
    config = MockConfig(
        host=args.host,
        port=args.port,
        update_interval=args.update_interval,
        motor_speed=args.motor_speed,
        temp_start=args.temp_start,
    )
    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:  # pragma: no cover - signal handler normally wins
        pass
    sys.stdout.flush()


if __name__ == "__main__":
    main()
