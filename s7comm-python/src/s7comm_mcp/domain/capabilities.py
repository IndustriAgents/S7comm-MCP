"""Backend support is distinct from device capability and authorization."""

from typing import Any


def capability_report(*, writes_enabled: bool, system_commands_enabled: bool = False) -> dict[str, Any]:
    operations = {
        "read_values": {"supported": True, "enabled": True},
        "write_values": {"supported": True, "enabled": writes_enabled},
        "read_plc_info": {"supported": True, "enabled": True},
        "read_cpu_state": {"supported": True, "enabled": True},
        "set_cpu_state": {"supported": True, "enabled": writes_enabled and system_commands_enabled},
        "read_system_time": {"supported": True, "enabled": True},
        "read_szl": {"supported": True, "enabled": True},
        "list_tags": {"supported": True, "enabled": True},
        "get_connection_status": {"supported": True, "enabled": True},
    }
    for operation in operations.values():
        if not operation["enabled"]:
            operation["reason"] = "Disabled by native configuration gates"
    return {
        "protocol": "s7comm",
        "backend": "python-snap7",
        "simulated": False,
        "scope": "backend_support",
        "device_verified": False,
        "operations": operations,
        "prerequisites": [
            "A configured PLC with supported S7 communication access and correct rack/slot is required.",
            "Absolute DB access requires compatible block layouts and PLC access settings.",
        ],
        "limitations": [
            "Symbolic optimized-block access, S7comm Plus, native subscriptions "
            "and encrypted sessions are not implemented.",
            "Supported services depend on PLC family, firmware and access settings.",
            "Batch writes are not atomic; protocol acknowledgement does not verify a physical process.",
        ],
        "authorization": "enabled reflects native configuration only; MCP policy and device permissions still apply",
    }
