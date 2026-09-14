"""Integrated combat effects and sound driven by real damage events (node 7).

EffectsSystem consumes copies of the *real* combat events published each fixed
tick by the shared simulation and turns them into bounded, deterministic
billboard particles plus audio cues. Nothing here is a cosmetic timer decoupled
from damage state: every emitter is keyed off an authoritative event or state.

Event bindings (see docs/INTEGRATION.md "Node 7 boundary"):
- weapons.fired_this_tick  (entity, kind, muzzle, direction) -> muzzle flash +
  per-weapon firing sound (cannon/scatter/torpedo/turret).
- weapons.impacts_this_tick (target, weapon, amount, hit position) -> impact
  sparks + impact sound at the resolved hit point.
- damage.destroyed_this_tick -> a scale-appropriate explosion (fighter vs
  capital) + explosion sound, so destruction feels consequential.
- damage.snapshot(e).state (healthy/smoking/burning) -> persistent smoke
  (smoking) and smoke + fire (burning) emitters at the ship's live position,
  so degradation stays visible before destruction. A looping fire crackle
  plays while any ship burns.
- damage.snapshot(e).repairing (None <-> Subsystem) -> repair start/complete
  sound cues plus a soft green glow while a ship is actively repairing.

Design is two layers:
1. Pure, window-free planning (Particle dataclass + spawn_* functions +
   EffectsSystem.fixed_update) testable with no window and no vision.
2. A thin billboard renderer (EffectsSystem.present) that writes particle state
   into a pool of flat-color, depth-tested, camera-facing quads. Particles are
   flat exact colours so automated pixel sampling can verify them (smoke gray,
   fire orange, flash near-white, repair glow green) with no image perception.

Boundedness: a global particle cap (MAX_PARTICLES), per-emitter rate limits via
accumulators, finite lifetimes, and a single shared fire loop. Audio is routed
through breach.audio.AudioEngine which honours --mute and master volume and
degrades gracefully when no device is present.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import random

from panda3d.core import CardMaker, Vec3

from breach.damage import ShipState
from breach.enemies import CAPITAL_HALF, FIGHTER_HALF
from breach.weapons import WeaponKind

# -- flat, pixel-sampleable colours ------------------------------------------
SMOKE_COLOR = (0.55, 0.50, 0.45)     # warm smoke (r>g>b; distinct from stars)
FIRE_ORANGE = (1.0, 0.50, 0.10)      # fire body
FIRE_YELLOW = (1.0, 0.85, 0.30)      # hot fire
MUZZLE_COLOR = (1.0, 0.96, 0.62)     # muzzle flash
IMPACT_COLOR = (1.0, 0.86, 0.42)     # impact spark
EXPLOSION_CORE = (1.0, 0.96, 0.72)   # explosion fireball core
EXPLOSION_FIRE = (1.0, 0.45, 0.12)   # explosion fire
EXPLOSION_SMOKE = (0.34, 0.32, 0.30)  # explosion smoke
REPAIR_COLOR = (0.30, 0.95, 0.55)    # repair glow

MAX_PARTICLES = 600
# Distance (metres) at which a sound attenuates to zero for the listener.
AUDIO_FALLOFF = 600.0

# Weapon kind -> firing sound asset name.
_FIRE_SOUND = {
    WeaponKind.CANNON: "cannon",
    WeaponKind.SCATTER: "scatter",
    WeaponKind.TORPEDO: "torpedo",
    WeaponKind.TURRET: "turret",
}

# Emission cadence (seconds between emissions) and particle counts per emission.
_SMOKE_INTERVAL = 0.055
_FIRE_INTERVAL = 0.05
_GLOW_INTERVAL = 0.07


def _clamp01(v):
    return max(0.0, min(1.0, v))


@dataclass
class Particle:
    """One flat billboard particle in world space (pure data, no NodePath)."""
    kind: str                       # smoke|fire|spark|flash|glow|explosion|muzzle
    pos: list                       # [x, y, z]
    vel: list                       # [vx, vy, vz]
    color: tuple                    # (r, g, b)
    size: float                     # current half-extent (metres)
    growth: float                   # metres/second of size growth
    age: float = 0.0
    max_life: float = 1.0
    drag: float = 0.0               # per-second velocity damping
    gravity: float = 0.0            # + = downward acceleration on z
    phase: float = 0.0
    flicker: float = 0.0            # 0 = steady; else alpha flicker amplitude

    def alpha(self):
        t = max(0.0, min(1.0, 1.0 - self.age / self.max_life))
        if self.flicker > 0.0:
            t *= 1.0 - self.flicker + self.flicker * (
                0.5 + 0.5 * math.sin(self.phase + self.age * 40.0))
        return t

    def to_dict(self):
        return dict(kind=self.kind, pos=[round(v, 3) for v in self.pos],
                    color=[round(c, 3) for c in self.color], size=round(self.size, 3),
                    age=round(self.age, 3), max_life=round(self.max_life, 3))


# -- pure spawn planners (deterministic given the seeded RNG) -----------------
def spawn_smoke(rng, center, half, heavy=False):
    hx, hy, hz = half
    p = [center[0] + rng.uniform(-hx, hx), center[1] + rng.uniform(-hy, hy),
         center[2] + hz + rng.uniform(0.0, 0.7)]
    v = [rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(0.9, 1.8)]
    if heavy:
        v[2] += 0.5
    g = rng.uniform(0.0, 0.15)
    return Particle("smoke", p, v, SMOKE_COLOR, 0.7 + g, 2.4,
                    max_life=rng.uniform(1.2, 1.6), drag=0.4, gravity=-0.2,
                    phase=rng.uniform(0, 6.28))


def spawn_fire(rng, center, half):
    hx, hy, hz = half
    p = [center[0] + rng.uniform(-hx, hx), center[1] + rng.uniform(-hy, hy),
         center[2] + hz * 0.5 + rng.uniform(0.0, 0.8)]
    v = [rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(1.1, 2.3)]
    color = FIRE_YELLOW if rng.random() < 0.35 else FIRE_ORANGE
    return Particle("fire", p, v, color, rng.uniform(0.5, 1.0), 1.3,
                    max_life=rng.uniform(0.3, 0.5), drag=0.8, gravity=-0.4,
                    phase=rng.uniform(0, 6.28), flicker=0.45)


def spawn_spark(rng, pos, direction):
    v = [direction[0] * rng.uniform(3, 9) + rng.uniform(-2, 2),
         direction[1] * rng.uniform(3, 9) + rng.uniform(-2, 2),
         direction[2] * rng.uniform(3, 9) + rng.uniform(-1, 3)]
    return Particle("spark", list(pos), v, IMPACT_COLOR, rng.uniform(0.10, 0.2),
                    0.0, max_life=rng.uniform(0.15, 0.3), drag=4.0, gravity=4.0,
                    flicker=0.5)


def spawn_muzzle_flash(rng, pos, direction, kind):
    size = {WeaponKind.CANNON: 0.5, WeaponKind.SCATTER: 0.9,
            WeaponKind.TORPEDO: 1.2, WeaponKind.TURRET: 0.8}.get(kind, 0.5)
    return Particle("muzzle", list(pos), [0.0, 0.0, 0.0], MUZZLE_COLOR, size, 0.0,
                    max_life=0.08, phase=rng.uniform(0, 6.28), flicker=0.4)


def spawn_repair_glow(rng, center, half=(2.5, 2.0, 0.4)):
    hx, hy, hz = half
    p = [center[0] + rng.uniform(-hx, hx), center[1] + rng.uniform(-hy, hy),
         center[2] + hz + rng.uniform(0.0, 1.0)]
    return Particle("glow", p, [0.0, 0.0, 0.2], REPAIR_COLOR, 1.3, -0.6,
                    max_life=0.45, phase=rng.uniform(0, 6.28), flicker=0.5)


def spawn_explosion(rng, pos, capital):
    """A scale-appropriate fireball: more, faster, longer-lived for a capital."""
    particles = []
    fire_count = 80 if capital else 26
    smoke_count = 30 if capital else 12
    radius = 24.0 if capital else 8.0
    life = 2.6 if capital else 1.2
    p0 = [pos[0], pos[1], pos[2]]
    particles.append(Particle("flash", list(p0), [0, 0, 0], EXPLOSION_CORE,
                              radius * 0.85, radius * 0.1, max_life=0.18))
    for _ in range(fire_count):
        az = rng.uniform(0.0, 2.0 * math.pi)
        el = math.asin(rng.uniform(-0.4, 1.0))
        speed = rng.uniform(0.4, 1.0) * (radius / 2.6)
        v = [math.cos(az) * math.cos(el) * speed,
             math.sin(az) * math.cos(el) * speed,
             math.sin(el) * speed * 0.5 + radius * 0.06]
        color = (1.0, rng.uniform(0.28, 0.55), rng.uniform(0.04, 0.2))
        particles.append(Particle("explosion", list(p0), v, color,
                                  rng.uniform(0.8, 2.0), 1.5,
                                  max_life=life * rng.uniform(0.55, 1.0),
                                  drag=1.4, gravity=1.0,
                                  phase=rng.uniform(0, 6.28), flicker=0.35))
    for _ in range(smoke_count):
        az = rng.uniform(0.0, 2.0 * math.pi)
        el = math.asin(rng.uniform(-0.3, 0.85))
        speed = rng.uniform(0.15, 0.55) * radius * 0.5
        v = [math.cos(az) * math.cos(el) * speed * 0.5,
             math.sin(az) * math.cos(el) * speed * 0.5,
             math.sin(el) * speed + radius * 0.05]
        particles.append(Particle("explosion", list(p0), v, EXPLOSION_SMOKE,
                                  rng.uniform(1.5, 3.2), 2.6,
                                  max_life=life * rng.uniform(1.0, 1.6),
                                  drag=0.5, gravity=-0.3,
                                  phase=rng.uniform(0, 6.28)))
    return particles


class EffectsSystem:
    """Owns disposable combat particles + audio; reads events, never writes
    simulation state (it only clears the weapons effect event buffers it read)."""

    def __init__(self, render, camera, damage, weapons, enemies, flight,
                 audio=None, tracked_entities=None, max_particles=MAX_PARTICLES,
                 seed=14):
        self.render = render
        self.camera = camera
        self.damage = damage
        self.weapons = weapons
        self.enemies = enemies
        self.flight = flight
        self.audio = audio
        self.tracked_entities = list(tracked_entities or [])
        self.max_particles = max_particles
        self.rng = random.Random(seed)
        self.particles = []
        self.quads = []
        self._emit_acc = {}       # entity_id -> seconds since last smoke/fire emit
        self._glow_acc = {}       # entity_id -> seconds since last glow emit
        self._repair_prev = {}    # entity_id -> previous repair subsystem
        self._fire_loop_active = False
        self.last_audio = []      # audio events this tick (for trace)
        self.last_spawns = []     # spawn records this tick (for trace)
        self.event_tally = {}     # cumulative effect-event counts (never reset)
        self.destroyed_kinds = set()  # cumulative destroyed ship kinds

    # -- event binding (fixed-step; deterministic) ---------------------------
    def fixed_update(self, dt, controls=None):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and positive")
        self.last_audio = []
        self.last_spawns = []
        self._process_firing()
        self._process_impacts()
        self._process_destruction()
        self._process_damage_states(dt)
        self._process_repair(dt)
        self._advance(dt)
        if hasattr(self.weapons, "clear_tick_effects"):
            self.weapons.clear_tick_effects()

    # -- entity helpers ------------------------------------------------------
    def _entity_pos(self, eid):
        if self.flight is not None and eid == self.flight.entity_id:
            return tuple(self.flight.position)
        if self.enemies is not None:
            try:
                return self.enemies.snapshot(eid).position
            except (KeyError, ValueError):
                pass
        if self.weapons is not None:
            try:
                return self.weapons.target_position(eid)
            except (KeyError, ValueError):
                pass
        return (0.0, 0.0, 0.0)

    def _is_capital(self, eid):
        if self.enemies is not None:
            try:
                if eid in self.enemies.entities:
                    return self.enemies.is_capital(eid)
            except (KeyError, ValueError):
                pass
        try:
            return self.damage.ship_profile(eid).name == "capital"
        except (KeyError, AttributeError):
            return False

    def _entity_half(self, eid):
        if self.enemies is not None:
            try:
                if eid in self.enemies.entities:
                    return CAPITAL_HALF if self.enemies.is_capital(eid) else FIGHTER_HALF
            except (KeyError, ValueError):
                pass
        return CAPITAL_HALF if self._is_capital(eid) else FIGHTER_HALF

    def _volume_at(self, pos):
        if self.flight is None:
            return 1.0
        dist = (Vec3(*pos) - Vec3(*self.flight.position)).length()
        return _clamp01(1.0 - dist / AUDIO_FALLOFF)

    def _add(self, particles, record=None):
        if record:
            self.last_spawns.append(record)
            self.event_tally[record["event"]] = self.event_tally.get(record["event"], 0) + 1
        for p in particles:
            if len(self.particles) >= self.max_particles:
                return  # bounded: refuse new particles at the cap
            self.particles.append(p)

    # -- firing --------------------------------------------------------------
    def _process_firing(self):
        for eid, kind, origin, direction in self.weapons.fired_this_tick:
            self._add([spawn_muzzle_flash(self.rng, origin, direction, kind)],
                      record=dict(event="fire", entity=eid, weapon=kind.value))
            sound = _FIRE_SOUND.get(kind)
            if sound:
                self._play(sound, self._volume_at(origin) * 0.9)

    def _process_impacts(self):
        for target_id, weapon, amount, pos in self.weapons.impacts_this_tick:
            direction = (0.0, 0.0, 1.0)
            sparks = [spawn_spark(self.rng, pos, direction) for _ in range(7)]
            self._add(sparks, record=dict(event="impact", target=target_id,
                                          weapon=weapon.value))
            self._play("impact", self._volume_at(pos) * 0.8)

    def _process_destruction(self):
        for eid in self.damage.destroyed_this_tick:
            pos = self._entity_pos(eid)
            capital = self._is_capital(eid)
            self.destroyed_kinds.add("capital" if capital else "fighter")
            self._add(spawn_explosion(self.rng, pos, capital),
                      record=dict(event="destroy", entity=eid,
                                  kind="capital" if capital else "fighter"))
            self._play("explosion_capital" if capital else "explosion_fighter",
                       self._volume_at(pos))

    # -- progressive damage states (smoke/fire) ------------------------------
    def _process_damage_states(self, dt):
        any_burning = False
        for eid in self.tracked_entities:
            view = self.damage.snapshot(eid)
            state = view.state
            pos = self._entity_pos(eid)
            half = self._entity_half(eid)
            if state is ShipState.SMOKING or state is ShipState.BURNING:
                acc = self._emit_acc.get(eid, 0.0) + dt
                while acc >= _SMOKE_INTERVAL:
                    acc -= _SMOKE_INTERVAL
                    self._add([spawn_smoke(self.rng, pos, half,
                                           heavy=(state is ShipState.BURNING))])
                self._emit_acc[eid] = acc
            if state is ShipState.BURNING:
                acc = self._emit_acc.get(eid + ":fire", 0.0) + dt
                while acc >= _FIRE_INTERVAL:
                    acc -= _FIRE_INTERVAL
                    self._add([spawn_fire(self.rng, pos, half)])
                self._emit_acc[eid + ":fire"] = acc
                any_burning = True
        self._set_fire_loop(any_burning)

    def _set_fire_loop(self, any_burning):
        if any_burning and not self._fire_loop_active:
            self._fire_loop_active = True
            self._play("fire_loop", 0.4, loop=True)
        elif not any_burning and self._fire_loop_active:
            self._fire_loop_active = False
            if self.audio is not None:
                self.audio.stop_loop("fire_loop")

    # -- repair cues ---------------------------------------------------------
    def _process_repair(self, dt):
        for eid in self.tracked_entities:
            view = self.damage.snapshot(eid)
            now = view.repairing
            prev = self._repair_prev.get(eid)
            pos = self._entity_pos(eid)
            if now != prev:
                if now is not None:
                    self._play("repair_start", self._volume_at(pos) * 0.7)
                    self.last_spawns.append(dict(event="repair_start",
                                                 entity=eid, subsystem=now.value))
                    self.event_tally["repair_start"] = self.event_tally.get("repair_start", 0) + 1
                elif prev is not None:
                    self._play("repair_complete", self._volume_at(pos) * 0.7)
                    self.last_spawns.append(dict(event="repair_complete",
                                                 entity=eid, subsystem=prev.value))
                    self.event_tally["repair_complete"] = self.event_tally.get("repair_complete", 0) + 1
                self._repair_prev[eid] = now
            if now is not None:
                acc = self._glow_acc.get(eid, 0.0) + dt
                while acc >= _GLOW_INTERVAL:
                    acc -= _GLOW_INTERVAL
                    self._add([spawn_repair_glow(self.rng, pos,
                                                 self._entity_half(eid))])
                self._glow_acc[eid] = acc

    # -- audio ---------------------------------------------------------------
    def _play(self, name, volume, loop=False):
        volume = _clamp01(volume)
        self.last_audio.append(dict(name=name, volume=round(volume, 4),
                                    loop=loop))
        if self.audio is not None:
            self.audio.play(name, volume=volume, loop=loop)

    # -- particle integration -------------------------------------------------
    def _advance(self, dt):
        surviving = []
        for p in self.particles:
            p.age += dt
            if p.age >= p.max_life:
                continue
            vx, vy, vz = p.vel
            vz -= p.gravity * dt
            if p.drag > 0.0:
                k = max(0.0, 1.0 - p.drag * dt)
                vx *= k
                vy *= k
                vz *= k
            p.vel = [vx, vy, vz]
            p.pos[0] += vx * dt
            p.pos[1] += vy * dt
            p.pos[2] += vz * dt
            p.size += p.growth * dt
            if p.size < 0.05:
                p.size = 0.05
            surviving.append(p)
        self.particles = surviving

    # -- rendering ------------------------------------------------------------
    def _new_quad(self, i):
        cm = CardMaker(f"fx-{i}")
        cm.setFrame(-0.5, 0.5, -0.5, 0.5)
        np = self.render.attachNewNode(cm.generate())
        np.setBillboardPointEye()
        np.setTwoSided(True)
        np.setTransparency(True)
        np.setDepthWrite(False)
        np.setDepthTest(True)
        np.setBin("transparent", 10)
        np.setLightOff()
        return np

    def present(self, alpha=1.0):
        """Write the current particle state into billboard quads (render frame)."""
        while len(self.quads) < min(len(self.particles), self.max_particles):
            self.quads.append(self._new_quad(len(self.quads)))
        for i, p in enumerate(self.particles):
            if i >= len(self.quads):
                break
            quad = self.quads[i]
            quad.show()
            quad.setPos(*p.pos)
            quad.setScale(p.size)
            r, g, b = p.color
            quad.setColor(r, g, b, p.alpha())
        for i in range(len(self.particles), len(self.quads)):
            self.quads[i].hide()
        return len(self.particles)

    # -- introspection --------------------------------------------------------
    def counts(self):
        counts = {}
        for p in self.particles:
            counts[p.kind] = counts.get(p.kind, 0) + 1
        return dict(active=len(self.particles), by_kind=counts,
                    fire_loop=self._fire_loop_active)


__all__ = [
    "Particle", "EffectsSystem",
    "spawn_smoke", "spawn_fire", "spawn_spark", "spawn_muzzle_flash",
    "spawn_repair_glow", "spawn_explosion",
    "SMOKE_COLOR", "FIRE_ORANGE", "FIRE_YELLOW", "MUZZLE_COLOR",
    "IMPACT_COLOR", "EXPLOSION_CORE", "EXPLOSION_SMOKE", "REPAIR_COLOR",
    "MAX_PARTICLES", "AUDIO_FALLOFF",
]
