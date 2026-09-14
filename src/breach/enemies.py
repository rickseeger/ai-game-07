"""Enemy fighters and the dangerous capital ship (node 5).

EnemySystem owns every non-player ship's pose, steering, attack decisions and
repair decisions. It reuses the SAME flight, weapon, subsystem-impairment and
repair rules that govern the player rather than reimplementing them:

- Each enemy fighter is a real FlightSystem sharing the player's FlightTuning and
  the shared DamageSystem as its FlightDamageService, so ENGINE damage impairs an
  enemy's thrust and turning exactly as it impairs the player's (one-tick latency
  included), and destruction zeroes its performance.
- Enemies attack by requesting shots through weapons.fire(entity_id, origin,
  direction, weapon) -> bool, the same cadence/heat/impairment boundary the
  player uses. WEAPONS damage scales an enemy's per-shot damage, a destroyed
  WEAPONS subsystem blocks its fire, and repair engagement blocks its fire
  (repair_locked). There is no bypass.
- A crippled enemy enters a vulnerable repair: it brakes to a stop, stops
  shooting, and restores one subsystem at the same timed, hit-interruptible rate
  the player uses (RepairIntent through the shared DamageSystem). Repair entry
  and exit are explicit state transitions recorded in the trace -- never silent
  or instantaneous hidden recovery.
- The capital is a large anchored hull (CAPITAL_PROFILE) with a turret battery
  that fires back through the same weapon boundary. Its huge hull pool means
  destruction requires prolonged sustained fire, and it degrades/repairs under
  the identical subsystem rules.

The module is pure deterministic simulation: no NodePaths, no window, no
randomness. Presentation of enemy poses is the app's job.
"""
from dataclasses import dataclass
import math

from panda3d.core import Quat, Vec3

from breach.contracts import PilotInput, RepairIntent, Subsystem
from breach.damage import CAPITAL_PROFILE, FIGHTER_PROFILE, ShipState
from breach.flight import FlightSystem, FlightTuning
from breach.weapons import WeaponKind

# Hitbox half-extents matching scene.combat_targets() (metres, +Y forward).
FIGHTER_HALF = (2.5, 2.0, 0.4)
CAPITAL_HALF = (8.0, 23.0, 4.0)
# Muzzle standoff past the capital's +Y half-length (its longest axis).
CAPITAL_MUZZLE = 25.0


def look_quat(forward):
    """Orientation quaternion (wxyz) mapping the ship's +Y nose to `forward`."""
    f = Vec3(*forward)
    f.normalize()
    heading = math.degrees(math.atan2(-f.x, f.y))
    pitch = math.degrees(math.atan2(f.z, math.hypot(f.x, f.y)))
    q = Quat()
    q.setHpr((heading, pitch, 0))
    return tuple(q)


def _clamp01(value):
    return max(-1.0, min(1.0, value))


@dataclass(frozen=True)
class EnemyTuning:
    """Deterministic AI tuning. All values are configuration, not buried magic."""
    fighter_repair_below: float = 0.25     # enter repair below this subsystem health
    capital_repair_below: float = 0.40
    fighter_engagement_range: float = 400.0  # metres at which a fighter opens fire
    capital_engagement_range: float = 900.0  # metres at which the capital opens fire
    fire_cone_deg: float = 18.0            # half-angle the nose must be within to fire
    steer_gain: float = 3.0                # proportional yaw/pitch gain (rad -> [-1,1])
    steer_deadzone: float = 0.02
    fighter_throttle_far: float = 0.70
    fighter_standoff: float = 90.0         # begin easing throttle inside this range
    fighter_standoff_close: float = 40.0   # near-stall standoff
    hit_cooldown: float = 3.0              # seconds after a hit before repair entry
    max_repair_interruptions: int = 4      # aborts a repair after this many hits

    def __post_init__(self):
        for name in ("fighter_repair_below", "capital_repair_below"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if not math.isfinite(self.fighter_engagement_range) or self.fighter_engagement_range <= 0:
            raise ValueError("fighter_engagement_range must be finite and positive")
        if not math.isfinite(self.capital_engagement_range) or self.capital_engagement_range <= 0:
            raise ValueError("capital_engagement_range must be finite and positive")
        if not 0 < self.fire_cone_deg < 90:
            raise ValueError("fire_cone_deg must be in (0, 90)")
        if self.max_repair_interruptions < 1:
            raise ValueError("max_repair_interruptions must be >= 1")


@dataclass
class _EnemyShip:
    """Mutable per-entity AI/ownership state. Never exposed directly."""
    entity_id: str
    flight: FlightSystem
    faction: str
    weapon: WeaponKind
    is_capital: bool
    # AI state machine
    repair_target: Subsystem | None = None
    repair_cap: float = 0.70
    repair_interruption_base: int = 0
    last_hull_hp: float = 0.0
    hit_cooldown: float = 0.0
    destroyed: bool = False
    fired: int = 0
    last_fire: bool = False


class EnemySystem:
    """Owns non-player ships: flight, steering, attack and repair decisions."""

    def __init__(self, player_entity_id="player", damage=None, weapons=None,
                 player_pose_provider=None, tuning=None, flight_tuning=None,
                 player_half_extents=FIGHTER_HALF):
        self.player_entity_id = player_entity_id
        self.damage = damage
        self.weapons = weapons
        self.player_pose_provider = player_pose_provider
        self.tuning = tuning or EnemyTuning()
        self.flight_tuning = flight_tuning or FlightTuning()
        self.player_half_extents = tuple(player_half_extents)
        self._ships = {}
        self.capital_id = None
        self.last_enemy_events = []   # (entity_id, kind, detail) decisions this tick
        if self.weapons is not None:
            self.weapons.set_faction(self.player_entity_id, "player")
            if self.player_pose_provider is not None:
                view = self.player_pose_provider()
                if view is not None:
                    self.weapons.add_target(self.player_entity_id, "player",
                                            view.position, self.player_half_extents,
                                            view.orientation)

    # -- spawn ---------------------------------------------------------------
    def spawn_fighter(self, entity_id, position, orientation=(1, 0, 0, 0),
                      faction="enemy", weapon=WeaponKind.CANNON,
                      profile=FIGHTER_PROFILE):
        if entity_id in self._ships:
            raise ValueError(f"entity already spawned: {entity_id}")
        flight = FlightSystem(damage=self.damage, tuning=self.flight_tuning,
                              entity_id=entity_id, position=position,
                              orientation=orientation)
        self.damage.spawn(entity_id, profile)
        self.weapons.set_faction(entity_id, faction)
        self.weapons.add_target(entity_id, faction, position, FIGHTER_HALF, orientation)
        self.weapons.select_weapon(entity_id, weapon)
        ship = _EnemyShip(entity_id, flight, faction, weapon, is_capital=False)
        ship.last_hull_hp = profile.hull_max_hp
        ship.repair_cap = profile.repair_cap
        self._ships[entity_id] = ship
        return entity_id

    def spawn_capital(self, entity_id, position, orientation=(1, 0, 0, 0),
                      faction="enemy", weapon=WeaponKind.TURRET,
                      profile=CAPITAL_PROFILE):
        if entity_id in self._ships:
            raise ValueError(f"entity already spawned: {entity_id}")
        if self.capital_id is not None:
            raise ValueError("only one capital ship is supported")
        flight = FlightSystem(damage=self.damage, tuning=self.flight_tuning,
                              entity_id=entity_id, position=position,
                              orientation=orientation)
        self.damage.spawn(entity_id, profile)
        self.weapons.set_faction(entity_id, faction)
        self.weapons.add_target(entity_id, faction, position, CAPITAL_HALF, orientation)
        self.weapons.select_weapon(entity_id, weapon)
        ship = _EnemyShip(entity_id, flight, faction, weapon, is_capital=True)
        ship.last_hull_hp = profile.hull_max_hp
        ship.repair_cap = profile.repair_cap
        self._ships[entity_id] = ship
        self.capital_id = entity_id
        return entity_id

    def reset(self):
        """Clear all owned enemy ships for a clean mission restart.

        Leaves the shared damage/weapons references intact (the app resets
        those separately); it only drops EnemySystem's per-ship flight/AI
        state and its capital marker. The player hitbox is re-registered by
        the app on the next spawn pass.
        """
        self._ships.clear()
        self.capital_id = None
        self.last_enemy_events = []
        # Re-register the player hitbox (cleared by weapons.reset()) so enemy
        # projectiles can resolve against the player again after a restart.
        if self.weapons is not None and self.player_pose_provider is not None:
            view = self.player_pose_provider()
            if view is not None:
                self.weapons.set_faction(self.player_entity_id, "player")
                self.weapons.add_target(self.player_entity_id, "player",
                                        view.position, self.player_half_extents,
                                        view.orientation)

    def despawn(self, entity_id):
        """Remove a destroyed/defeated enemy from all three registries.

        Owned here because EnemySystem is the single place that knows the full
        set of per-ship resources (flight system, damage slot, weapon hitbox).
        Despawning a ship that is already gone is a no-op, so mission cleanup
        can call it once per defeated ship without tracking prior removals.
        """
        ship = self._ships.pop(entity_id, None)
        if ship is None:
            return
        if self.capital_id == entity_id:
            self.capital_id = None
        self.damage.despawn(entity_id)
        self.weapons.remove_target(entity_id)

    # -- queries -------------------------------------------------------------
    @property
    def entities(self):
        return tuple(self._ships)

    def is_capital(self, entity_id):
        ship = self._ships.get(entity_id)
        return bool(ship and ship.is_capital)

    def snapshot(self, entity_id):
        return self._ships[entity_id].flight.snapshot()

    def speed_of(self, entity_id):
        ship = self._ships.get(entity_id)
        if ship is None or ship.destroyed:
            return 0.0
        return ship.flight.velocity.length()

    def is_alive(self, entity_id):
        ship = self._ships.get(entity_id)
        return ship is not None and not ship.destroyed

    def ai_snapshot(self):
        """Trace-ready read-only view of every enemy (no mutable state leaks)."""
        rows = []
        for ship in self._ships.values():
            view = self.damage.snapshot(ship.entity_id)
            rows.append({
                "entity_id": ship.entity_id,
                "kind": "capital" if ship.is_capital else "fighter",
                "faction": ship.faction,
                "position": tuple(ship.flight.position),
                "velocity": tuple(ship.flight.velocity),
                "speed": ship.flight.velocity.length(),
                "throttle": ship.flight.throttle,
                "hull": view.hull,
                "hull_hp": view.hull_hp,
                "state": view.state.value,
                "subsystems": {s.value: v for s, v in view.subsystems.items()},
                "repairing": view.repairing.value if view.repairing else None,
                "repair_progress": view.repair_progress,
                "repair_interruptions": self.damage.repair_interruptions[ship.entity_id],
                "firing": ship.last_fire,
                "destroyed": ship.destroyed,
            })
        return rows

    # -- fixed-step simulation ------------------------------------------------
    def fixed_update(self, dt, controls):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and positive")
        self.last_enemy_events = []
        # Refresh the player's hitbox pose so enemy projectiles (resolved later
        # this tick in WeaponsSystem.fixed_update) hit the player where it now is.
        player_pos = None
        if self.player_pose_provider is not None:
            view = self.player_pose_provider()
            if view is not None:
                player_pos = Vec3(*view.position)
                self.weapons.set_target_pose(self.player_entity_id, view.position,
                                             view.orientation)
        destroyed = []
        for ship in list(self._ships.values()):
            if ship.destroyed:
                continue
            health = self.damage.snapshot(ship.entity_id)
            if health.state is ShipState.DESTROYED:
                ship.destroyed = True
                ship.last_fire = False
                self.weapons.remove_target(ship.entity_id)
                self.last_enemy_events.append((ship.entity_id, "destroyed", None))
                destroyed.append(ship.entity_id)
                continue
            if ship.is_capital:
                self._update_capital(ship, health, player_pos, dt)
            else:
                self._update_fighter(ship, health, player_pos, dt)
        return destroyed

    # -- fighter AI -----------------------------------------------------------
    def _update_fighter(self, ship, health, player_pos, dt):
        t = self.tuning
        self._note_hit(ship, health, dt)
        if ship.repair_target is not None:
            self._drive_repair(ship, health, dt)
            return
        target = self._repair_target(health, t.fighter_repair_below)
        if target is not None and ship.hit_cooldown <= 0.0:
            self._begin_repair(ship, target)
            self._drive_repair(ship, health, dt)
            return
        self._drive_combat(ship, health, player_pos, t.fighter_engagement_range, dt)

    # -- capital AI -----------------------------------------------------------
    def _update_capital(self, ship, health, player_pos, dt):
        t = self.tuning
        self._note_hit(ship, health, dt)
        if ship.repair_target is not None:
            self._drive_repair(ship, health, dt)
            return
        target = self._repair_target(health, t.capital_repair_below)
        if target is not None and ship.hit_cooldown <= 0.0:
            self._begin_repair(ship, target)
            self._drive_repair(ship, health, dt)
            return
        # Combat: the capital is anchored; only its turrets act.
        ship.flight.fixed_update(dt, PilotInput())
        ship.last_fire = False
        if player_pos is None:
            return
        delta = player_pos - ship.flight.position
        dist = delta.length()
        if dist <= t.capital_engagement_range and dist > 1e-6:
            direction = delta / dist
            origin = ship.flight.position + direction * CAPITAL_MUZZLE
            if self.weapons.fire(ship.entity_id, tuple(origin), tuple(direction),
                                 weapon=ship.weapon):
                ship.fired += 1
                ship.last_fire = True
                self.last_enemy_events.append((ship.entity_id, "fire", ship.weapon.value))

    # -- shared helpers -------------------------------------------------------
    def _note_hit(self, ship, health, dt):
        if health.hull_hp < ship.last_hull_hp - 1e-9:
            ship.hit_cooldown = self.tuning.hit_cooldown
        ship.last_hull_hp = health.hull_hp
        if ship.hit_cooldown > 0.0:
            ship.hit_cooldown = max(0.0, ship.hit_cooldown - dt)

    def _repair_target(self, health, below):
        """Lowest-health subsystem below `below`, WEAPONS-first on ties."""
        best, best_health = None, below
        for sub in (Subsystem.WEAPONS, Subsystem.ENGINE, Subsystem.SENSORS):
            h = health.subsystems[sub]
            if h < best_health:
                best_health, best = h, sub
        return best

    def _begin_repair(self, ship, subsystem):
        ship.repair_target = subsystem
        ship.repair_interruption_base = self.damage.repair_interruptions[ship.entity_id]
        self.damage.queue(RepairIntent(ship.entity_id, subsystem, True))
        self.last_enemy_events.append((ship.entity_id, "repair_enter", subsystem.value))

    def _end_repair(self, ship, cooldown):
        if ship.repair_target is not None:
            self.damage.queue(RepairIntent(ship.entity_id, ship.repair_target, False))
            self.last_enemy_events.append((ship.entity_id, "repair_exit",
                                           ship.repair_target.value))
        ship.repair_target = None
        if cooldown:
            # A brief combat-only pause prevents instant repair re-entry thrash.
            ship.hit_cooldown = self.tuning.hit_cooldown

    def _drive_repair(self, ship, health, dt):
        t = self.tuning
        ship.last_fire = False
        # Completion: the subsystem reached the repair cap. The damage system
        # clears its own target on the same tick this becomes visible to us.
        if health.subsystems[ship.repair_target] >= ship.repair_cap - 1e-9:
            self._end_repair(ship, cooldown=False)
            return
        interruptions = (self.damage.repair_interruptions[ship.entity_id]
                         - ship.repair_interruption_base)
        if interruptions > t.max_repair_interruptions:
            self._end_repair(ship, cooldown=True)  # abort under sustained fire
            return
        controls = PilotInput(repair=True, repair_subsystem=ship.repair_target)
        ship.flight.fixed_update(dt, controls)

    def _drive_combat(self, ship, health, player_pos, engagement_range, dt):
        t = self.tuning
        ship.last_fire = False
        if player_pos is None:
            ship.flight.fixed_update(dt, PilotInput())
            return
        yaw, pitch = self._steer(ship.flight, player_pos)
        dist = (player_pos - ship.flight.position).length()
        # Slow down to turn: scale throttle by nose alignment so a fighter
        # tightens its turn instead of orbiting at high speed.
        align = 1.0
        if dist > 1e-6:
            forward = ship.flight.orientation.xform(Vec3(0, 1, 0))
            align = max(0.0, forward.dot((player_pos - ship.flight.position) / dist))
        throttle = self._throttle_rate_to(ship, self._throttle_for(dist) * align)
        fire = self._want_fire(ship.flight, player_pos, engagement_range, t.fire_cone_deg)
        controls = PilotInput(yaw=yaw, pitch=pitch, throttle=throttle, fire=fire,
                              repair_subsystem=Subsystem.ENGINE)
        ship.flight.fixed_update(dt, controls)
        if fire:
            origin, direction = self._muzzle(ship.flight)
            if self.weapons.fire(ship.entity_id, origin, direction, weapon=ship.weapon):
                ship.fired += 1
                ship.last_fire = True
                self.last_enemy_events.append((ship.entity_id, "fire", ship.weapon.value))

    def _steer(self, flight, target):
        """Proportional yaw/pitch to rotate the nose toward the target.

        The world rotation axis forward x desired, mapped into the body frame,
        decomposes into the flight adapter's yaw (about body -Z, nose right) and
        pitch (about body +X, nose up). The directly-behind case (cross ~ 0 and
        nose opposed) is broken with a hard yaw so a fighter can turn around.
        """
        t = self.tuning
        forward = flight.orientation.xform(Vec3(0, 1, 0))
        delta = Vec3(*target) - flight.position
        dist = delta.length()
        if dist < 1e-6:
            return 0.0, 0.0
        desired = delta / dist
        cross = forward.cross(desired)
        if cross.lengthSquared() < 1e-12:
            if forward.dot(desired) < 0.0:
                return 1.0, 0.0  # exactly opposed: hard yaw right to come around
            return 0.0, 0.0
        body = flight.orientation.conjugate().xform(cross)
        pitch = _clamp01(body.x * t.steer_gain)
        yaw = _clamp01(-body.z * t.steer_gain)
        if abs(yaw) < t.steer_deadzone:
            yaw = 0.0
        if abs(pitch) < t.steer_deadzone:
            pitch = 0.0
        return yaw, pitch

    def _throttle_for(self, dist):
        t = self.tuning
        if dist < t.fighter_standoff_close:
            return 0.15
        if dist < t.fighter_standoff:
            return 0.35
        return t.fighter_throttle_far

    def _throttle_rate_to(self, ship, target_fraction):
        """Return a signed throttle rate that drives the ship's throttle setting
        toward target_fraction. The flight model treats controls.throttle as a
        rate of change, so a proportional error converges on the target instead
        of pinning at full speed (the old behavior made fighters overshoot and
        flee without slowing to turn)."""
        error = target_fraction - ship.flight.throttle
        return max(-1.0, min(1.0, error * 4.0))

    def _want_fire(self, flight, target, max_range, cone_deg):
        forward = flight.orientation.xform(Vec3(0, 1, 0))
        delta = Vec3(*target) - flight.position
        dist = delta.length()
        if dist > max_range:
            return False
        if dist < 1e-6:
            return True
        return forward.dot(delta / dist) >= math.cos(math.radians(cone_deg))

    def _muzzle(self, flight):
        forward = flight.orientation.xform(Vec3(0, 1, 0)).normalized()
        return tuple(flight.position + forward * 2.0), tuple(forward)


__all__ = [
    "EnemyTuning", "EnemySystem", "FIGHTER_HALF", "CAPITAL_HALF",
    "CAPITAL_MUZZLE", "look_quat",
]
