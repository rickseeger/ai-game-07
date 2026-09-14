"""Accumulated-damage and repair simulation shared by player, fighter, capital.

Node 3 of tree G14. This module is the sole owner of hull/sub-system health,
repair progress, and ship condition state. It is pure simulation: no NodePaths,
no window, no engine side effects. It is injected into FlightSystem as the
FlightDamageService so that real damage-derived performance (thrust, turning,
repair lock) drives the live flight rather than a disconnected demonstration.

Model (fully deterministic, no randomness):
- Every ship has a hull HP pool and one HP pool per Subsystem (ENGINE, WEAPONS,
  SENSORS). Snapshots normalize everything to [0, 1].
- Hull damage drives condition via explicit thresholds:
    healthy -> smoking -> burning -> destroyed (hull == 0).
- Subsystem capability is linear in subsystem health: a subsystem at fraction h
  delivers h of its capability; at 0 it has failed (capability fully lost).
  ENGINE drives BOTH thrust and turning (main + attitude thrusters), WEAPONS
  drives weapons output, SENSORS drives sensor reach (the future weapons node
  reads the same multiplier).
- Repair restores one selected subsystem at repair_rate HP/s up to repair_cap
  (70% by default), requires the ship to be at/below repair_max_speed (a
  tactically vulnerable low-mobility/stationary state) AND free of incoming
  damage on the current tick. A hit interrupts the current uninterrupted repair
  interval without undoing already-restored HP.
- Capital ships use a much larger profile, so they withstand substantially
  longer sustained attack than fighters.

Public boundary (matches docs/INTEGRATION.md):
    DamageSystem.queue(DamageEvent | RepairIntent) -> None
    DamageSystem.fixed_update(dt, controls) -> None
    DamageSystem.snapshot(entity_id) -> HealthView
    DamageSystem.flight_performance(entity_id) -> FlightPerformance
    DamageSystem.capability(entity_id, subsystem) -> float
    DamageSystem.subsystem_health(entity_id, subsystem) -> float
"""
from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping

from breach.contracts import DamageEvent, PilotInput, RepairIntent, Subsystem
from breach.flight import FlightPerformance


def _capability(health):
    """Subsystem health [0, 1] -> capability multiplier [0, 1] (linear)."""
    return max(0.0, min(1.0, health))


class ShipState(str, Enum):
    """Hull condition, worst-first ordering by numeric hull fraction."""
    HEALTHY = "healthy"
    SMOKING = "smoking"
    BURNING = "burning"
    DESTROYED = "destroyed"


@dataclass(frozen=True)
class DamageProfile:
    """Per-ship-class tuning. Health is HP; multipliers/timers as documented."""
    name: str
    hull_max_hp: float
    subsystem_max_hp: float
    repair_duration: float = 4.0       # seconds to restore 0 -> repair_cap
    repair_cap: float = 0.70           # max fraction a repair can reach
    repair_max_speed: float = 2.0      # m/s at/below which repair can proceed
    smoke_at: float = 0.66             # hull fraction at/below which = smoking
    burn_at: float = 0.33              # hull fraction at/below which = burning

    def __post_init__(self):
        for name in ("hull_max_hp", "subsystem_max_hp"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not math.isfinite(self.repair_duration) or self.repair_duration <= 0:
            raise ValueError("repair_duration must be finite and positive")
        if not 0 < self.repair_cap <= 1:
            raise ValueError("repair_cap must be in (0, 1]")
        if not math.isfinite(self.repair_max_speed) or self.repair_max_speed < 0:
            raise ValueError("repair_max_speed must be finite and nonnegative")
        if not (0 < self.burn_at < self.smoke_at < 1):
            raise ValueError("require 0 < burn_at < smoke_at < 1")

    @property
    def repair_rate(self):
        """Subsystem HP restored per second of uninterrupted repair."""
        return self.subsystem_max_hp * self.repair_cap / self.repair_duration


FIGHTER_PROFILE = DamageProfile(
    "fighter", hull_max_hp=100.0, subsystem_max_hp=40.0,
    repair_duration=4.0, repair_cap=0.70, repair_max_speed=2.0)

CAPITAL_PROFILE = DamageProfile(
    "capital", hull_max_hp=3000.0, subsystem_max_hp=600.0,
    repair_duration=30.0, repair_cap=0.70, repair_max_speed=0.5)


@dataclass(frozen=True)
class HealthView:
    """Immutable read-only snapshot of one ship's damage/repair state."""
    entity_id: str
    hull: float                          # normalized [0, 1]
    hull_hp: float                       # absolute remaining HP
    state: ShipState
    subsystems: Mapping[Subsystem, float]  # normalized health per subsystem
    repairing: Subsystem | None
    repair_progress: float               # [0, 1] of current repair interval
    engine_multiplier: float             # thrust (engine)
    turning_multiplier: float            # turning (engine)
    weapons_multiplier: float            # weapons output
    sensors_multiplier: float            # sensor reach

    def to_dict(self):
        return {
            "entity_id": self.entity_id,
            "hull": self.hull,
            "hull_hp": self.hull_hp,
            "state": self.state.value,
            "subsystems": {s.value: v for s, v in self.subsystems.items()},
            "repairing": self.repairing.value if self.repairing else None,
            "repair_progress": self.repair_progress,
            "engine_multiplier": self.engine_multiplier,
            "turning_multiplier": self.turning_multiplier,
            "weapons_multiplier": self.weapons_multiplier,
            "sensors_multiplier": self.sensors_multiplier,
        }


class _ShipState:
    """Authoritative mutable state for one entity. Never exposed directly."""
    __slots__ = ("entity_id", "profile", "hull_hp", "subsystem_hp",
                 "repair_target", "repair_start", "repair_progress",
                 "damage_this_tick")

    def __init__(self, entity_id, profile):
        self.entity_id = entity_id
        self.profile = profile
        self.hull_hp = profile.hull_max_hp
        self.subsystem_hp = {s: profile.subsystem_max_hp for s in Subsystem}
        self.repair_target = None
        self.repair_start = 1.0
        self.repair_progress = 0.0
        self.damage_this_tick = False


class DamageSystem:
    """Sole owner of health/repair state; a FlightDamageService as well."""

    def __init__(self, player_entity_id="player", speed_provider=None):
        self.player_entity_id = player_entity_id
        # speed_provider(entity_id) -> metres/second; default treats every ship
        # as stationary so repair is gated only by the caller's real wiring.
        self.speed_provider = speed_provider if speed_provider is not None else (lambda _eid: 0.0)
        self._ships = {}
        self._queue = []
        self._player_intent = None      # last player repair target (edge-triggered)
        self.events = []               # copy of this tick's events for effects
        self.destroyed_this_tick = []  # entities that hit hull 0 this tick
        self.repair_interruptions = {}  # entity_id -> cumulative count

    # -- lifecycle ---------------------------------------------------------
    def spawn(self, entity_id, profile):
        if entity_id in self._ships:
            raise ValueError(f"entity already spawned: {entity_id}")
        self._ships[entity_id] = _ShipState(entity_id, profile)
        self.repair_interruptions[entity_id] = 0

    def despawn(self, entity_id):
        self._ships.pop(entity_id, None)
        self.repair_interruptions.pop(entity_id, None)

    @property
    def entities(self):
        return tuple(self._ships)

    def ship_profile(self, entity_id):
        return self._ships[entity_id].profile

    def is_spawned(self, entity_id):
        return entity_id in self._ships

    # -- event intake ------------------------------------------------------
    def queue(self, event):
        if not isinstance(event, (DamageEvent, RepairIntent)):
            raise TypeError("queue() accepts DamageEvent or RepairIntent")
        self._queue.append(event)

    # -- queries -----------------------------------------------------------
    def subsystem_health(self, entity_id, subsystem):
        ship = self._ships.get(entity_id)
        if ship is None:
            return 1.0
        return max(0.0, min(1.0, ship.subsystem_hp[subsystem] / ship.profile.subsystem_max_hp))

    def capability(self, entity_id, subsystem):
        """Raw subsystem capability (linear health); ignores hull destruction."""
        return _capability(self.subsystem_health(entity_id, subsystem))

    def flight_performance(self, entity_id):
        ship = self._ships.get(entity_id)
        if ship is None:
            return FlightPerformance()
        if ship.hull_hp <= 0:
            return FlightPerformance(0.0, 0.0, True)
        mult = _capability(ship.subsystem_hp[Subsystem.ENGINE] / ship.profile.subsystem_max_hp)
        locked = ship.repair_target is not None
        return FlightPerformance(thrust_multiplier=mult, turning_multiplier=mult,
                                 repair_locked=locked)

    # -- fixed-step simulation ---------------------------------------------
    def fixed_update(self, dt, controls):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and positive")
        controls = controls if controls is not None else PilotInput()
        self.events = list(self._queue)
        self.destroyed_this_tick = []
        for ship in self._ships.values():
            ship.damage_this_tick = False
        # 1. Player repair intent from held controls (R), before combat events.
        self._resolve_player_intent(controls)
        # 2. Consume queued events once, in enqueue order.
        for event in self._queue:
            if isinstance(event, DamageEvent):
                self._apply_damage(event)
            else:
                self._set_repair_target(self._ships.get(event.entity_id), event.subsystem if event.active else None)
        self._queue.clear()
        # 3. Advance repair (gated on speed and absence of incoming damage).
        for ship in self._ships.values():
            if ship.repair_target is not None:
                self._advance_repair(ship, dt)

    def _resolve_player_intent(self, controls):
        """Edge-triggered R intent: only changes on press/release/switch.

        This keeps the held-R level semantics (repair persists while R is held)
        while allowing queued RepairIntent events for the player entity to
        coexist without being cleared every tick by an unpressed R.
        """
        ship = self._ships.get(self.player_entity_id)
        if ship is None:
            return
        want = controls.repair_subsystem if controls.repair else None
        if want != self._player_intent:
            self._player_intent = want
            self._set_repair_target(ship, want)

    def _set_repair_target(self, ship, subsystem):
        if ship is None or ship.hull_hp <= 0:
            return
        if subsystem is None:
            ship.repair_target = None
            ship.repair_progress = 0.0
            return
        if ship.repair_target != subsystem:
            ship.repair_target = subsystem
            ship.repair_start = ship.subsystem_hp[subsystem] / ship.profile.subsystem_max_hp
            ship.repair_progress = 0.0

    def _apply_damage(self, event):
        ship = self._ships.get(event.target_id)
        if ship is None or ship.hull_hp <= 0:
            return
        ship.damage_this_tick = True
        if event.subsystem is None:
            ship.hull_hp = max(0.0, ship.hull_hp - event.amount)
            if ship.hull_hp <= 0:
                ship.repair_target = None
                ship.repair_progress = 0.0
                self.destroyed_this_tick.append(event.target_id)
        else:
            value = ship.subsystem_hp[event.subsystem]
            ship.subsystem_hp[event.subsystem] = max(0.0, value - event.amount)

    def _advance_repair(self, ship, dt):
        if ship.damage_this_tick:
            # A hit interrupts the current uninterrupted repair interval; it
            # does not undo already-restored HP (health is untouched here).
            self.repair_interruptions[ship.entity_id] += 1
            ship.repair_progress = 0.0
            ship.repair_start = ship.subsystem_hp[ship.repair_target] / ship.profile.subsystem_max_hp
            return
        if self.speed_provider(ship.entity_id) > ship.profile.repair_max_speed:
            return  # too fast: repair requires a low-mobility/stationary state
        subsystem = ship.repair_target
        max_hp = ship.profile.subsystem_max_hp
        health = ship.subsystem_hp[subsystem] / max_hp
        if health >= ship.profile.repair_cap:
            ship.repair_target = None
            ship.repair_progress = 0.0
            return
        ship.subsystem_hp[subsystem] = min(
            max_hp * ship.profile.repair_cap,
            ship.subsystem_hp[subsystem] + ship.profile.repair_rate * dt)
        new_health = ship.subsystem_hp[subsystem] / max_hp
        span = ship.profile.repair_cap - ship.repair_start
        if span > 0:
            ship.repair_progress = min(1.0, (new_health - ship.repair_start) / span)
        if new_health >= ship.profile.repair_cap - 1e-9:
            ship.repair_target = None
            ship.repair_progress = 0.0

    # -- snapshot ----------------------------------------------------------
    def snapshot(self, entity_id):
        return self._view(self._ships[entity_id])

    def _view(self, ship):
        destroyed = ship.hull_hp <= 0
        hull = max(0.0, ship.hull_hp / ship.profile.hull_max_hp)
        if destroyed:
            state = ShipState.DESTROYED
        elif hull <= ship.profile.burn_at:
            state = ShipState.BURNING
        elif hull <= ship.profile.smoke_at:
            state = ShipState.SMOKING
        else:
            state = ShipState.HEALTHY
        subsystems = {s: max(0.0, min(1.0, ship.subsystem_hp[s] / ship.profile.subsystem_max_hp))
                      for s in Subsystem}
        if destroyed:
            engine_mult = turning_mult = weapons_mult = sensors_mult = 0.0
        else:
            engine_mult = turning_mult = _capability(subsystems[Subsystem.ENGINE])
            weapons_mult = _capability(subsystems[Subsystem.WEAPONS])
            sensors_mult = _capability(subsystems[Subsystem.SENSORS])
        return HealthView(
            entity_id=ship.entity_id,
            hull=hull,
            hull_hp=ship.hull_hp,
            state=state,
            subsystems=MappingProxyType(subsystems),
            repairing=ship.repair_target,
            repair_progress=max(0.0, min(1.0, ship.repair_progress)),
            engine_multiplier=engine_mult,
            turning_multiplier=turning_mult,
            weapons_multiplier=weapons_mult,
            sensors_multiplier=sensors_mult,
        )


__all__ = [
    "ShipState", "DamageProfile", "HealthView", "DamageSystem",
    "FIGHTER_PROFILE", "CAPITAL_PROFILE",
]
