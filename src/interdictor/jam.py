from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JamProfile:
    mode_name: str
    description: str
    centre_frequency: float
    bandwidth: float
    tx_power_dbm: float


def _parse_profile(p: dict) -> JamProfile:
    return JamProfile(
        mode_name=p["mode_name"],
        description=p.get("description", ""),
        centre_frequency=float(p["centre_frequency"]),
        bandwidth=float(p["bandwidth"]),
        tx_power_dbm=float(p["tx_power_dbm"]),
    )


class JammerController:
    """Tracks the effector's current mode and (simulated) jam profile.

    No RF is ever transmitted here - this mirrors spectre's EWDetectionSource
    on the sensor side: a protocol-level stand-in so the Tasking/StatusReport
    state machine can be built and interop-tested against a real Fusion Node
    before any hardware TX chain exists.
    """

    def __init__(self, cfg: dict, default_mode: str):
        self._profiles = {
            p.mode_name: p for p in (_parse_profile(e) for e in cfg.get("profiles", []))
        }
        self._default_mode = default_mode
        self._mode = default_mode

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def active_profile(self) -> JamProfile | None:
        return self._profiles.get(self._mode)

    def known_modes(self) -> set[str]:
        return {self._default_mode, *self._profiles}

    def is_jamming(self) -> bool:
        return self._mode != self._default_mode

    def switch_to(self, mode_name: str) -> bool:
        """Switch to `mode_name` if it is a known mode; returns whether it was."""
        if mode_name not in self.known_modes():
            return False
        self._mode = mode_name
        return True

    def revert_to_default(self) -> None:
        self._mode = self._default_mode
