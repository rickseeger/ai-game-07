"""Accessible, replayable combat mission loop and balance tuning (node 8).

Node 8 of tree G14. MissionSystem is the integration layer that turns the
validated sibling systems (flight, damage, weapons, enemies, effects, cockpit)
into a real sortie with a clear objective, escalating encounters, a capital-ship
climax, comprehensible victory/defeat, and a clean restart. It owns ONLY the
orchestration and the mission-facing tuning; it reuses the shared systems rather
than reimplementing them:

- It spawns fighters/capital through enemies.EnemySystem (the SAME flight/weapon/
  impairment/repair rules as the player), never by direct state writes.
- It detects progression from the authoritative DamageSystem health snapshots
  (player destroyed -> defeat; all enemies destroyed -> next wave / victory).
- It publishes an immutable MissionState each tick for the HUD banner; it has no
  window/NodePath side effects.

Escalation design (finite waves, no infinitely replenishing enemies):
  Wave 1: one light fighter at close range -- the approachable first kill.
  Wave 2: two fighters -- target-lock and heat management under pressure.
  Wave 3: three fighters -- a full dogfight.
  Wave 4: the interdiction frigate (capital) -- a prolonged engagement where
          capital-ship repair windows and the player's own repair become the
          decisive decisions.
The frigate spawns only after the escort waves are cleared, so the turret does
not hound a brand-new pilot from second zero.

Balance contract: the damage/repair tension is tuned against explicit numeric
targets (per-ship-class sustained-DPS time-to-kill windows and repair
vulnerability windows). Those targets are validated by tests/test_mission.py
against the actual weapon specs and damage profiles, so they cannot drift from
the code they describe. See also data/mission_balance.json.
"""
from dataclasses import dataclass
import math

from panda3d.core import Vec3

from breach.damage import CAPITAL_PROFILE, FIGHTER_PROFILE, ShipState
from breach.enemies import EnemySystem, look_quat
from breach.weapons import WeaponKind


class MissionPhase(str):
    """Terminal-friendly phase labels (kept as plain strings for JSON traces)."""
    COMBAT = "combat"
    VICTORY = "victory"
    DEFEAT = "defeat"


@dataclass(frozen=True)
class MissionBalance:
    """Explicit numeric tuning targets the mission is validated against.

    These are *targets*, not independent knobs: the concrete values live in
    breach.weapons.WEAPONS / breach.damage.*_PROFILE, and tests/test_mission.py
    computes the realized sustained DPS / TTK / repair windows from those specs
    and asserts they land inside these windows. Editing one without the other
    fails the test, so the balance cannot silently drift.
    """
    # Sustained-DPS time-to-kill windows (seconds), per ship class. Sustained
    # DPS is heat-limited continuous fire (the player can never exceed the
    # cooldown AND heat budget at once).
    fighter_ttk_window: tuple = (1.2, 4.0)
    capital_ttk_window: tuple = (35.0, 75.0)
    # Repair vulnerability window (seconds) while a crippled ship repairs in
    # place: it is stationary, holds fire, and restores a subsystem to the cap
    # over this span unless hit (a hit interrupts without undoing progress).
    fighter_repair_window: tuple = (2.0, 6.0)
    capital_repair_window: tuple = (20.0, 40.0)
    # The frigate must survive a single torpedo (no one-shot), and a single
    # torpedo must NOT one-shot a fighter (the heaviest hit leaves it alive).
    torpedo_damage: float = 90.0

    def __post_init__(self):
        for name in ("fighter_ttk_window", "capital_ttk_window",
                     "fighter_repair_window", "capital_repair_window"):
            lo, hi = getattr(self, name)
            if not (0 < lo < hi):
                raise ValueError(f"{name} requires 0 < low < high")
        if not math.isfinite(self.torpedo_damage) or self.torpedo_damage <= 0:
            raise ValueError("torpedo_damage must be finite and positive")

    def to_dict(self):
        return {
            "fighter_ttk_window_s": list(self.fighter_ttk_window),
            "capital_ttk_window_s": list(self.capital_ttk_window),
            "fighter_repair_window_s": list(self.fighter_repair_window),
            "capital_repair_window_s": list(self.capital_repair_window),
            "torpedo_damage": self.torpedo_damage,
        }


@dataclass(frozen=True)
class WaveSpec:
    """One mission wave: an objective line plus the enemies it spawns."""
    objective: str
    fighters: tuple = ()
    capital: bool = False

    def enemy_ids(self):
        return tuple(self.fighters) + (("capital",) if self.capital else ())


# Player spawn (fixed for every attempt). Enemy spawns are placed relative to
# this and face the player at spawn via look_quat.
PLAYER_SPAWN = (0.0, -15.0, 4.0)

# Default sortie: approachable start -> escalating dogfight -> capital climax.
# Finite by construction; no wave ever respawns.
DEFAULT_WAVES = (
    WaveSpec("Destroy the patrol fighter", fighters=("fighter-1",)),
    WaveSpec("Hostiles inbound - clear the escort", fighters=("fighter-2", "fighter-3")),
    WaveSpec("Fighters closing - hold them off", fighters=("fighter-4", "fighter-5", "fighter-6")),
    WaveSpec("Destroy the interdiction frigate", capital=True),
)

# Enemy spawn positions (world metres). Placed ahead of the player (+Y) so the
# first contact is visible and readable without a radar hunt.
_SPAWN_POSITIONS = {
    "fighter-1": (0.0, 40.0, 4.0),
    "fighter-2": (-26.0, 60.0, 6.0),
    "fighter-3": (26.0, 64.0, 2.0),
    "fighter-4": (-40.0, 84.0, 9.0),
    "fighter-5": (0.0, 92.0, 5.0),
    "fighter-6": (38.0, 82.0, 0.0),
    "capital": (0.0, 130.0, 4.0),
}

VICTORY_TEXT = "Mission complete - frigate destroyed"
DEFEAT_TEXT = "Ship destroyed"


@dataclass(frozen=True)
class MissionState:
    """Immutable, trace-ready view of the mission for the HUD/tools."""
    phase: str
    wave: int
    total_waves: int
    objective: str
    remaining_enemies: tuple
    terminal: bool
    restart_hint: str

    def to_dict(self):
        return {
            "phase": self.phase,
            "wave": self.wave,
            "total_waves": self.total_waves,
            "objective": self.objective,
            "remaining_enemies": list(self.remaining_enemies),
            "terminal": self.terminal,
            "restart_hint": self.restart_hint,
        }


class MissionSystem:
    """Owns the mission loop: wave spawns, objectives, win/lose, restart."""

    def __init__(self, player_entity_id, damage, enemies, weapons,
                 player_pose_provider, waves=None, balance=None,
                 spawn_positions=None):
        self.player_entity_id = player_entity_id
        self.damage = damage
        self.enemies = enemies
        self.weapons = weapons
        self.player_pose_provider = player_pose_provider
        self.waves = tuple(DEFAULT_WAVES if waves is None else waves)
        self.balance = balance or MissionBalance()
        self.spawn_positions = dict(_SPAWN_POSITIONS if spawn_positions is None
                                    else spawn_positions)
        self.phase = MissionPhase.COMBAT
        self.wave_index = 0
        self._started = False
        self._spawned = set()
        self._defeated = False
        self._victory = False
        self._transition_log = []

    # -- lifecycle ----------------------------------------------------------
    @property
    def terminal(self):
        return self.phase in (MissionPhase.VICTORY, MissionPhase.DEFEAT)

    def start(self):
        """Begin (or restart) the sortie from wave 1. Pure spawn orchestration."""
        self.phase = MissionPhase.COMBAT
        self.wave_index = 0
        self._started = True
        self._spawned = set()
        self._defeated = False
        self._victory = False
        self._transition_log = []
        self._spawn_wave(0)

    def _spawn_wave(self, index):
        wave = self.waves[index]
        for fid in wave.fighters:
            pos = self.spawn_positions[fid]
            aim = self._aim_at_player(pos)
            self.enemies.spawn_fighter(fid, pos, orientation=aim)
            self._spawned.add(fid)
        if wave.capital:
            pos = self.spawn_positions["capital"]
            self.enemies.spawn_capital("capital", pos,
                                       orientation=self._aim_at_player(pos))
            self._spawned.add("capital")

    def _aim_at_player(self, pos):
        player_pos = Vec3(*self.player_pose_provider().position)
        return look_quat(tuple((player_pos - Vec3(*pos)).normalized()))

    # -- progression --------------------------------------------------------
    def fixed_update(self, dt, controls):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and positive")
        if not self._started or self.terminal:
            return
        player = self.damage.snapshot(self.player_entity_id)
        if player.state is ShipState.DESTROYED and not self._defeated:
            self._defeated = True
            self.phase = MissionPhase.DEFEAT
            self._transition_log.append(("defeat", self.wave_index + 1))
            return
        wave = self.waves[self.wave_index]
        if self._wave_cleared(wave):
            self._despawn_wave(wave)
            if self.wave_index + 1 >= len(self.waves):
                self._victory = True
                self.phase = MissionPhase.VICTORY
                self._transition_log.append(("victory", self.wave_index + 1))
            else:
                self.wave_index += 1
                self._spawn_wave(self.wave_index)
                self._transition_log.append(("wave", self.wave_index + 1))

    def _wave_cleared(self, wave):
        for eid in wave.enemy_ids():
            if self.damage.is_spawned(eid):
                if self.damage.snapshot(eid).state is not ShipState.DESTROYED:
                    return False
        return True

    def _despawn_wave(self, wave):
        for eid in wave.enemy_ids():
            self.enemies.despawn(eid)
            self._spawned.discard(eid)

    # -- queries ------------------------------------------------------------
    def snapshot(self):
        wave = self.waves[min(self.wave_index, len(self.waves) - 1)]
        remaining = tuple(eid for eid in self._spawned
                          if self.damage.is_spawned(eid)
                          and self.damage.snapshot(eid).state is not ShipState.DESTROYED)
        if self.phase == MissionPhase.VICTORY:
            objective = VICTORY_TEXT
        elif self.phase == MissionPhase.DEFEAT:
            objective = DEFEAT_TEXT
        else:
            objective = wave.objective
        hint = "Press N to restart" if self.terminal else ""
        return MissionState(
            phase=self.phase, wave=min(self.wave_index + 1, len(self.waves)),
            total_waves=len(self.waves), objective=objective,
            remaining_enemies=remaining, terminal=self.terminal,
            restart_hint=hint,
        )

    @property
    def transition_log(self):
        return tuple(self._transition_log)


def sustained_dps(specs):
    """Heat-limited sustained DPS for a single weapon (the realistic ceiling).

    A weapon can fire no faster than its cooldown AND no faster than its heat
    capacity allows under cooling: fire_rate = min(1/cooldown,
    cool_rate/heat_per_shot). Damage per shot times that rate is the sustained
    hull DPS. Returns (dps, fire_rate).
    """
    from breach.weapons import WEAPON_ORDER
    best = 0.0
    for kind in WEAPON_ORDER:
        spec = specs[kind]
        rate = min(1.0 / spec.cooldown, spec.cool_rate / spec.heat_per_shot)
        dps = spec.damage * rate
        best = max(best, dps)
    return best


__all__ = [
    "MissionPhase", "MissionBalance", "WaveSpec", "MissionState",
    "MissionSystem", "PLAYER_SPAWN", "DEFAULT_WAVES", "_SPAWN_POSITIONS",
    "VICTORY_TEXT", "DEFEAT_TEXT", "sustained_dps",
]
