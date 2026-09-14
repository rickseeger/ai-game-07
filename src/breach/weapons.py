"""Deterministic weapons, projectile kinematics and hit resolution (node 4).

Node 4 of tree G14. WeaponsSystem owns projectile state, fire cadence,
cooldown/heat resource accounting, target lock/aim feedback, deterministic
segment-vs-oriented-box hit detection, and DamageEvent emission. It NEVER
reduces health directly: every resolved ship hit is routed through the injected
damage service's queue() as a DamageEvent, so sustained combat accumulates in
the shared DamageSystem exactly like scripted damage (no bypass).

Design (fully deterministic, no randomness):
- Three weapon kinds (CANNON / SCATTER / TORPEDO) with distinct cadence,
  damage, projectile speed, range and heat so they feel different. Damage per
  shot is scaled by the firing ship's WEAPONS capability (linear in subsystem
  health), and firing is blocked while the WEAPONS subsystem is destroyed or
  repair is engaged (the repair entry request disables weapons at this boundary).
- Cooldown (seconds) and heat (0..capacity, cooling at cool_rate) are the two
  declared resources. A shot is refused when it would push heat over capacity,
  so sustained full-auto fire is throttled by cooling rather than a hard lockout.
- Projectiles are points swept as line segments each fixed tick; the segment is
  tested against each target's oriented box (OBB) in the box's local frame
  (slab method). Ownership filtering: a projectile never hits its owner's
  faction, and environment targets block/remove the projectile without emitting
  damage.
- Target lock cycles enemy targets on the target_next edge; the aim snapshot
  exposes weapon, ready/cooldown, heat, impairment, locked target and whether
  the reticle currently has a fire solution (the forward ray actually intersects
  the locked target's box within range).

Public boundary (matches docs/INTEGRATION.md):
    WeaponsSystem.fixed_update(dt, controls) -> None
    WeaponsSystem.fire(entity_id, origin: Vec3, direction: Vec3, weapon=None) -> bool
    WeaponsSystem.add_target / remove_target / set_target_pose / set_faction
    WeaponsSystem.aim_snapshot() -> AimState
"""

from dataclasses import dataclass
import math
from enum import Enum

from panda3d.core import Quat, Vec3

from breach.contracts import DamageEvent, PilotInput, Subsystem

# Muzzle offset (metres) along the ship's +Y forward axis from its centre,
# just past the declared hull nose (HULL_MAX.y == 2.0).
MUZZLE_OFFSET = 2.0
# Lock cone half-angle (degrees) used for the "in front" aim-feedback flag.
LOCK_CONE_DEG = 45.0
# Cooldown readiness tolerance: absorbs float residue from repeated 1/60 decay.
EPS = 1e-9


class WeaponKind(str, Enum):
    CANNON = "cannon"
    SCATTER = "scatter"
    TORPEDO = "torpedo"


@dataclass(frozen=True)
class WeaponSpec:
    """Static tuning for one weapon. All amounts/timers are configuration."""
    kind: WeaponKind
    name: str
    damage: float          # hull HP per projectile at full capability
    cooldown: float        # seconds between shots
    projectile_speed: float  # metres/second
    max_range: float       # metres before the projectile expires (miss)
    heat_per_shot: float
    heat_capacity: float
    cool_rate: float       # heat/second
    pellets: int = 1
    spread: float = 0.0    # radians half-angle (scatter)

    def __post_init__(self):
        for name in ("damage", "cooldown", "projectile_speed", "max_range",
                     "heat_per_shot", "heat_capacity"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not math.isfinite(self.cool_rate) or self.cool_rate < 0:
            raise ValueError("cool_rate must be finite and nonnegative")
        if not math.isfinite(self.spread) or self.spread < 0:
            raise ValueError("spread must be finite and nonnegative")
        if self.pellets < 1:
            raise ValueError("pellets must be >= 1")


WEAPONS = {
    WeaponKind.CANNON: WeaponSpec(
        WeaponKind.CANNON, "Rapid Cannon", damage=12.0, cooldown=0.15,
        projectile_speed=160.0, max_range=800.0, heat_per_shot=6.0,
        heat_capacity=100.0, cool_rate=30.0),
    WeaponKind.SCATTER: WeaponSpec(
        WeaponKind.SCATTER, "Scatter Cannon", damage=8.0, cooldown=0.5,
        projectile_speed=120.0, max_range=500.0, heat_per_shot=12.0,
        heat_capacity=100.0, cool_rate=20.0, pellets=5, spread=0.12),
    WeaponKind.TORPEDO: WeaponSpec(
        WeaponKind.TORPEDO, "Torpedo", damage=90.0, cooldown=1.4,
        projectile_speed=50.0, max_range=1200.0, heat_per_shot=40.0,
        heat_capacity=100.0, cool_rate=25.0),
}

# Stable order used for cycling and deterministic selection.
WEAPON_ORDER = (WeaponKind.CANNON, WeaponKind.SCATTER, WeaponKind.TORPEDO)


@dataclass(frozen=True)
class AimState:
    """Immutable aim/weapon feedback snapshot for the cockpit/HUD."""
    weapon: str
    weapon_name: str
    ready: bool
    cooldown_remaining: float
    heat: float
    heat_capacity: float
    overheated: bool
    weapons_multiplier: float
    firing_blocked: bool
    locked_target: str | None
    lock_in_range: bool
    lock_in_front: bool
    on_target: bool
    projectiles: int
    fired: int
    hits: int
    misses: int

    def to_dict(self):
        return {name: getattr(self, name) for name in (
            "weapon", "weapon_name", "ready", "cooldown_remaining", "heat",
            "heat_capacity", "overheated", "weapons_multiplier",
            "firing_blocked", "locked_target", "lock_in_range", "lock_in_front",
            "on_target", "projectiles", "fired", "hits", "misses")}


@dataclass
class Hitbox:
    """A target's oriented box in world space. Mutable pose (moving targets)."""
    entity_id: str
    faction: str
    position: Vec3
    orientation: Quat
    half_extents: Vec3


@dataclass
class _Projectile:
    owner_id: str
    faction: str
    weapon: WeaponKind
    damage: float
    position: Vec3
    direction: Vec3
    speed: float
    travelled: float
    max_range: float


class NeutralWeaponDamage:
    """Default when no damage service is injected: full capability, no lock,
    and a no-op queue. The real app always injects the shared DamageSystem."""
    def queue(self, event):
        pass

    def capability(self, entity_id, subsystem):
        return 1.0

    def flight_performance(self, entity_id):
        from breach.flight import FlightPerformance
        return FlightPerformance()


def _pellet_directions(forward, spread, pellets):
    """Deterministic spread pattern (no randomness): centre plus a ring."""
    fwd = Vec3(*forward)
    fwd.normalize()
    if pellets <= 1 or spread <= 0:
        return [fwd]
    world_up = Vec3(0, 0, 1)
    right = fwd.cross(world_up)
    if right.lengthSquared() < 1e-12:
        right = Vec3(1, 0, 0)
    right.normalize()
    up = right.cross(fwd)
    up.normalize()
    s = math.sin(spread)
    dirs = [fwd]
    for k in range(1, pellets):
        azimuth = 2 * math.pi * (k - 1) / (pellets - 1)
        dirs.append((fwd + right * (s * math.cos(azimuth))
                     + up * (s * math.sin(azimuth))).normalized())
    return dirs


def _segment_obb(p0, p1, target):
    """Entry parameter t in [0,1] if the segment intersects the oriented box,
    else None. Transforms the segment into the box's local frame (slab method).
    """
    center = target.position
    inv = target.orientation.conjugate()
    lp0 = inv.xform(p0 - center)
    lp1 = inv.xform(p1 - center)
    half = target.half_extents
    tmin, tmax = 0.0, 1.0
    for i in range(3):
        d = lp1[i] - lp0[i]
        if abs(d) < 1e-12:
            if lp0[i] < -half[i] or lp0[i] > half[i]:
                return None
        else:
            t1 = (-half[i] - lp0[i]) / d
            t2 = (half[i] - lp0[i]) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return None
    return tmin


class WeaponsSystem:
    """Owns projectiles, cadence/resources, hit resolution and damage emission."""

    def __init__(self, player_entity_id="player", damage=None, pose_provider=None,
                 specs=None):
        self.player_entity_id = player_entity_id
        self.specs = dict(WEAPONS if specs is None else specs)
        self._order = tuple(self.specs.keys())
        self.damage = damage if damage is not None else NeutralWeaponDamage()
        # pose_provider() -> ShipView; supplies the player's muzzle/aim each tick.
        self.pose_provider = pose_provider
        self._factions = {player_entity_id: "player"}
        self._selected = {player_entity_id: WeaponKind.CANNON}
        self._targets = {}
        self._projectiles = []
        self._cooldown = {w: 0.0 for w in self.specs}
        self._heat = {w: 0.0 for w in self.specs}
        self._lock_index = 0
        self._locked = None
        self._fired = 0
        self._hits = 0
        self._misses = 0
        self.last_events = []       # DamageEvents emitted this tick (for effects)
        self.hits_this_tick = []    # (target_id, weapon, amount) this tick

    # -- registry ----------------------------------------------------------
    def set_faction(self, entity_id, faction):
        self._factions[entity_id] = faction

    def add_target(self, entity_id, faction, position, half_extents, orientation=None):
        if entity_id in self._targets:
            raise ValueError(f"target already registered: {entity_id}")
        orient = Quat(*orientation) if orientation is not None else Quat(1, 0, 0, 0)
        orient.normalize()
        self._targets[entity_id] = Hitbox(entity_id, faction, Vec3(*position),
                                          orient, Vec3(*half_extents))

    def remove_target(self, entity_id):
        self._targets.pop(entity_id, None)
        if self._locked == entity_id:
            self._locked = None

    def set_target_pose(self, entity_id, position, orientation=None):
        target = self._targets.get(entity_id)
        if target is None:
            raise KeyError(f"unknown target: {entity_id}")
        target.position = Vec3(*position)
        if orientation is not None:
            target.orientation = Quat(*orientation)
            target.orientation.normalize()

    @property
    def targets(self):
        return tuple(self._targets)

    @property
    def projectiles(self):
        return tuple(self._projectiles)

    @property
    def weapon(self):
        return self._selected.get(self.player_entity_id, WeaponKind.CANNON)

    def select_weapon(self, entity_id, kind):
        if kind not in self.specs:
            raise ValueError(f"unknown weapon kind: {kind}")
        self._selected[entity_id] = kind

    # -- capability --------------------------------------------------------
    def weapons_multiplier(self, entity_id):
        return max(0.0, min(1.0, self.damage.capability(entity_id, Subsystem.WEAPONS)))

    def _firing_blocked(self, entity_id):
        if self.weapons_multiplier(entity_id) <= 0:
            return True
        return self.damage.flight_performance(entity_id).repair_locked

    # -- firing ------------------------------------------------------------
    def fire(self, entity_id, origin, direction, weapon=None):
        """Request a shot from entity_id (player or enemy). Enforces cadence,
        heat and impairment/repair restrictions. Returns True if any projectile
        spawned. origin is the muzzle point; direction is the aim vector."""
        kind = weapon if weapon is not None else self._selected.get(entity_id, WeaponKind.CANNON)
        if kind not in self.specs:
            raise ValueError(f"unknown weapon kind: {kind}")
        spec = self.specs[kind]
        if self._cooldown[kind] > EPS:
            return False
        if self._firing_blocked(entity_id):
            return False
        if self._heat[kind] + spec.heat_per_shot > spec.heat_capacity + 1e-9:
            return False  # would overheat
        mult = self.weapons_multiplier(entity_id)
        faction = self._factions.get(entity_id, "enemy")
        origin = Vec3(*origin)
        direction = Vec3(*direction)
        if direction.lengthSquared() <= 0:
            raise ValueError("fire direction must be non-zero")
        forward = direction.normalized()
        for pellet_dir in _pellet_directions(forward, spec.spread, spec.pellets):
            self._projectiles.append(_Projectile(
                owner_id=entity_id, faction=faction, weapon=kind,
                damage=spec.damage * mult, position=origin, direction=pellet_dir,
                speed=spec.projectile_speed, travelled=0.0,
                max_range=spec.max_range))
        self._cooldown[kind] = spec.cooldown
        self._heat[kind] += spec.heat_per_shot
        self._fired += spec.pellets
        return True

    # -- fixed-step simulation ---------------------------------------------
    def fixed_update(self, dt, controls):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and positive")
        controls = controls if controls is not None else PilotInput()
        # 1. resources decay every tick for every weapon.
        for kind in self.specs:
            self._cooldown[kind] = max(0.0, self._cooldown[kind] - dt)
            self._heat[kind] = max(0.0, self._heat[kind] - self.specs[kind].cool_rate * dt)
        # 2. discrete edges.
        if controls.target_next:
            self._cycle_lock()
        if controls.cycle_weapon:
            self._cycle_weapon()
        # 3. player firing (held fire level).
        if controls.fire:
            origin, direction = self._muzzle()
            if origin is not None:
                self.fire(self.player_entity_id, origin, direction)
        # 4. advance projectiles and resolve hits.
        self._advance_projectiles(dt)

    def _muzzle(self):
        if self.pose_provider is None:
            return None, None
        view = self.pose_provider()
        if view is None:
            return None, None
        center = Vec3(*view.position)
        forward = Quat(*view.orientation).xform(Vec3(0, 1, 0)).normalized()
        return center + forward * MUZZLE_OFFSET, forward

    def _cycle_weapon(self):
        current = self.weapon
        idx = self._order.index(current)
        self._selected[self.player_entity_id] = self._order[(idx + 1) % len(self._order)]

    def _enemy_ids(self):
        return [eid for eid, t in self._targets.items()
                if t.faction not in ("player", "environment")]

    def _cycle_lock(self):
        enemies = self._enemy_ids()
        if not enemies:
            self._locked = None
            return
        self._lock_index = (self._lock_index + 1) % len(enemies)
        self._locked = enemies[self._lock_index]

    def _advance_projectiles(self, dt):
        self.last_events = []
        self.hits_this_tick = []
        surviving = []
        for proj in self._projectiles:
            p0 = proj.position
            step = proj.direction * (proj.speed * dt)
            p1 = p0 + step
            proj.travelled += step.length()
            target, _hit_pos = self._first_hit(proj, p0, p1)
            if target is not None:
                if target.faction == "environment":
                    self._misses += 1  # blocked, spent without a ship hit
                else:
                    self._hits += 1
                    self.hits_this_tick.append((target.entity_id, proj.weapon, proj.damage))
                    event = DamageEvent(source_id=proj.owner_id,
                                        target_id=target.entity_id,
                                        amount=proj.damage, subsystem=None)
                    self.last_events.append(event)
                    self.damage.queue(event)
                continue
            if proj.travelled >= proj.max_range:
                self._misses += 1
                continue
            proj.position = p1
            surviving.append(proj)
        self._projectiles = surviving

    def _first_hit(self, proj, p0, p1):
        """Nearest target the segment intersects, skipping the owner's faction."""
        best = None
        best_target = None
        for target in self._targets.values():
            if target.faction == proj.faction:
                continue  # ownership filtering: never hit self/allies
            t = _segment_obb(p0, p1, target)
            if t is not None and (best is None or t < best):
                best = t
                best_target = target
        if best_target is None:
            return None, None
        return best_target, (p0 + (p1 - p0) * best)

    # -- feedback ----------------------------------------------------------
    def _max_lock_range(self):
        return max(spec.max_range for spec in self.specs.values())

    def aim_snapshot(self):
        kind = self.weapon
        spec = self.specs[kind]
        mult = self.weapons_multiplier(self.player_entity_id)
        overheated = self._heat[kind] + spec.heat_per_shot > spec.heat_capacity + 1e-9
        blocked = self._firing_blocked(self.player_entity_id)
        ready = self._cooldown[kind] <= EPS and not blocked and not overheated
        locked = self._locked
        lock_in_range = lock_in_front = on_target = False
        if locked is not None and locked in self._targets:
            target = self._targets[locked]
            origin, forward = self._muzzle()
            if origin is not None:
                to = target.position - origin
                dist = to.length()
                lock_in_range = dist <= self._max_lock_range()
                if dist > 1e-9:
                    lock_in_front = (forward.dot(to / dist)
                                     >= math.cos(math.radians(LOCK_CONE_DEG)))
                on_target = (_segment_obb(origin, origin + forward * spec.max_range,
                                          target) is not None)
        return AimState(
            weapon=kind.value, weapon_name=spec.name, ready=ready,
            cooldown_remaining=max(0.0, self._cooldown[kind]), heat=self._heat[kind],
            heat_capacity=spec.heat_capacity, overheated=overheated,
            weapons_multiplier=mult, firing_blocked=blocked, locked_target=locked,
            lock_in_range=lock_in_range, lock_in_front=lock_in_front,
            on_target=on_target, projectiles=len(self._projectiles),
            fired=self._fired, hits=self._hits, misses=self._misses)


__all__ = [
    "WeaponKind", "WeaponSpec", "WEAPONS", "WEAPON_ORDER", "AimState", "Hitbox",
    "WeaponsSystem", "NeutralWeaponDamage", "MUZZLE_OFFSET", "LOCK_CONE_DEG",
]
