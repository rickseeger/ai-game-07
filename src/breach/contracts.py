"""Shared integration vocabulary; no engine/window side effects."""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
FIXED_DT = 1.0 / 60.0
MAX_FRAME_DT = 0.1
MAX_STEPS = 6
class Subsystem(str, Enum):
    ENGINE = "engine"
    WEAPONS = "weapons"
    SENSORS = "sensors"
@dataclass(frozen=True)
class PilotInput:
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
    throttle: float = 0.0
    fire: bool = False
    repair: bool = False
    target_next: bool = False
    cycle_weapon: bool = False
    brake: bool = False
    level: bool = False
    repair_subsystem: Subsystem = Subsystem.ENGINE
    def __post_init__(self):
        for name in ("pitch", "yaw", "roll", "throttle"):
            if not -1.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [-1, 1]")
@dataclass(frozen=True)
class DamageEvent:
    source_id: str
    target_id: str
    amount: float
    subsystem: Subsystem | None = None
    def __post_init__(self):
        import math
        if not math.isfinite(self.amount) or self.amount < 0:
            raise ValueError("damage must be finite and nonnegative")
@dataclass(frozen=True)
class RepairIntent:
    entity_id: str
    subsystem: Subsystem
    active: bool
class SimulationSystem(Protocol):
    def fixed_update(self, dt: float, controls: PilotInput) -> None: ...
class PresentationSystem(Protocol):
    def present(self, alpha: float) -> None: ...


@dataclass(frozen=True)
class ShipView:
    entity_id: str
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    throttle: float
