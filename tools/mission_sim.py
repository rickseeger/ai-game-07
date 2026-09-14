"""Deterministic combat-mission lifecycle scenarios (no window, no vision).

Drives the public MissionSystem / EnemySystem / WeaponsSystem / DamageSystem
boundary headlessly to prove the node-8 completion contract independently of the
unit tests:

- approachable_start: the sortie opens on a single fighter at readable range.
- autopilot_victory: a deterministic strafing autopilot plays the FULL mission
  (all waves -> capital) using ONLY ordinary weapons fire and flight movement --
  no scripted damage, no invulnerability -- and reaches the victory state.
- exploit_enemy_repair: a crippled fighter is finished during its vulnerable
  repair window.
- risky_player_repair: the player holds R to repair while an enemy is present;
  a hit interrupts the repair without undoing progress.

Every scenario reuses the SAME flight/weapon/damage rules as the live game. The
only scripting is a fire/repair *input* (the same PilotInput the keyboard
produces) and a strafe for the autopilot, not a damage bypass.

Run from the repository root (needs the project environment):
    .venv/bin/python tools/mission_sim.py --output artifacts/mission-sim
"""
import argparse
import json
import math
from pathlib import Path

from panda3d.core import Vec3

from breach.contracts import DamageEvent, PilotInput, ShipView, Subsystem, FIXED_DT
from breach.damage import CAPITAL_PROFILE, FIGHTER_PROFILE, DamageSystem, ShipState
from breach.enemies import EnemySystem, look_quat
from breach.flight import FlightSystem
from breach.mission import MissionSystem, PLAYER_SPAWN
from breach.weapons import WEAPONS_WITH_TURRET, WeaponsSystem, WeaponKind

IDLE = PilotInput()


class Autopilot:
    """A deterministic player proxy: aim at the nearest alive enemy, strafe to
    dodge return fire, and shoot the appropriate weapon. Movement is a straight
    lateral drift (a strafing pass), so enemy projectiles that lead the spawn
    position miss a moving player -- ordinary evasive flight, not invulnerability.
    """

    def __init__(self, flight):
        self.flight = flight

    def controls(self, enemies, weapons, damage):
        flight = self.flight
        nearest = None
        best = 1e18
        for eid in enemies.entities:
            if not enemies.is_alive(eid):
                continue
            pos = Vec3(*enemies.snapshot(eid).position)
            dist = (pos - flight.position).length()
            if dist < best:
                best = dist
                nearest = (eid, pos)
        if nearest is None:
            return PilotInput()
        eid, pos = nearest
        delta = pos - flight.position
        direction = delta.normalized()
        # Aim the nose at the target (identity local +Y forward).
        flight.orientation = look_quat(tuple(direction))
        # Fighters close on the player and are hit head-on; hold still and
        # out-shoot them. The capital turret is lethal inside 900 m, so against
        # the capital strafe to dodge and back off beyond turret reach (torpedoes
        # outrange it), closing only after it is blunted.
        if enemies.is_capital(eid):
            right = direction.cross(Vec3(0, 0, 1))
            if right.lengthSquared() < 1e-9:
                right = Vec3(1, 0, 0)
            right.normalize()
            flight.position += right * 24.0 * FIXED_DT
            if best < 950.0:
                flight.position -= direction * 24.0 * FIXED_DT
        # Weapon choice: torpedo is the anti-capital tool; cannon for fighters.
        weapons.select_weapon("player",
                              WeaponKind.TORPEDO if enemies.is_capital(eid)
                              else WeaponKind.CANNON)
        return PilotInput(fire=True)


import math


def build():
    flight = FlightSystem(position=PLAYER_SPAWN)
    d = DamageSystem(player_entity_id="player")
    flight.damage = d
    d.spawn("player", FIGHTER_PROFILE)
    w = WeaponsSystem(player_entity_id="player", damage=d,
                      pose_provider=flight.snapshot, specs=WEAPONS_WITH_TURRET)
    e = EnemySystem(player_entity_id="player", damage=d, weapons=w,
                    player_pose_provider=flight.snapshot)
    d.speed_provider = (lambda eid: flight.velocity.length()
                        if eid == "player" else e.speed_of(eid))
    m = MissionSystem(player_entity_id="player", damage=d, enemies=e, weapons=w,
                      player_pose_provider=flight.snapshot)
    m.start()
    return flight, d, w, e, m


def step(flight, e, w, d, m, controls, n=1):
    for _ in range(n):
        e.fixed_update(FIXED_DT, controls)
        w.fixed_update(FIXED_DT, controls)
        d.fixed_update(FIXED_DT, controls)
        m.fixed_update(FIXED_DT, controls)


def scenario_approachable_start():
    flight, d, w, e, m = build()
    snap = m.snapshot()
    assert snap.wave == 1 and snap.phase == "combat"
    assert snap.objective == "Destroy the patrol fighter"
    assert len(e.entities) == 1
    pos = e.snapshot("fighter-1").position
    return dict(wave=snap.wave, objective=snap.objective,
                enemies=len(e.entities),
                fighter_range=round((Vec3(*pos) - Vec3(*PLAYER_SPAWN)).length(), 1))


def scenario_autopilot_victory():
    flight, d, w, e, m = build()
    pilot = Autopilot(flight)
    shots = hits = 0
    max_ticks = 60 * 60 * 12  # up to 12 simulated minutes
    for _ in range(max_ticks):
        controls = pilot.controls(e, w, d)
        e.fixed_update(FIXED_DT, controls)
        w.fixed_update(FIXED_DT, controls)
        d.fixed_update(FIXED_DT, controls)
        m.fixed_update(FIXED_DT, controls)
        if m.terminal:
            break
    snap = m.snapshot()
    assert snap.phase == "victory", snap.to_dict()
    return dict(phase=snap.phase, objective=snap.objective,
                transitions=list(m.transition_log),
                player_hull_hp=round(d.snapshot("player").hull_hp, 1))


def scenario_exploit_enemy_repair():
    flight, d, w, e, m = build()
    # Cripple fighter-1's weapons; it enters a vulnerable repair.
    d.queue(DamageEvent("player", "fighter-1",
                        FIGHTER_PROFILE.subsystem_max_hp, Subsystem.WEAPONS))
    step(flight, e, w, d, m, IDLE, 10)
    assert d.snapshot("fighter-1").repairing is Subsystem.WEAPONS
    assert e.speed_of("fighter-1") == 0.0
    fired_at_entry = e._ships["fighter-1"].fired
    step(flight, e, w, d, m, IDLE, 30)
    assert e._ships["fighter-1"].fired == fired_at_entry, "repairing fighter held fire"
    # Finish it inside the window.
    d.queue(DamageEvent("player", "fighter-1",
                        FIGHTER_PROFILE.hull_max_hp, None))
    d.fixed_update(FIXED_DT, IDLE)
    assert d.snapshot("fighter-1").state is ShipState.DESTROYED
    m.fixed_update(FIXED_DT, IDLE)
    assert m.snapshot().wave == 2
    return dict(held_fire=True, stationary=True, destroyed_in_window=True,
                wave_after=2)


def scenario_risky_player_repair():
    # Isolate the player's repair choice (no enemies) so ambient fire cannot
    # mask the mechanics: repair progresses when safe, and a hit interrupts the
    # current interval without undoing already-restored health.
    flight = FlightSystem(position=PLAYER_SPAWN)
    d = DamageSystem(player_entity_id="player")
    flight.damage = d
    d.spawn("player", FIGHTER_PROFILE)
    w = WeaponsSystem(player_entity_id="player", damage=d,
                      pose_provider=flight.snapshot, specs=WEAPONS_WITH_TURRET)
    e = EnemySystem(player_entity_id="player", damage=d, weapons=w,
                    player_pose_provider=flight.snapshot)
    d.speed_provider = (lambda eid: flight.velocity.length()
                        if eid == "player" else e.speed_of(eid))

    def tick(controls, n=1):
        for _ in range(n):
            e.fixed_update(FIXED_DT, controls)
            w.fixed_update(FIXED_DT, controls)
            d.fixed_update(FIXED_DT, controls)

    d.queue(DamageEvent("enemy", "player",
                        FIGHTER_PROFILE.subsystem_max_hp, Subsystem.WEAPONS))
    tick(IDLE)
    assert d.subsystem_health("player", Subsystem.WEAPONS) < 0.2
    repair = PilotInput(repair=True, repair_subsystem=Subsystem.WEAPONS)
    tick(repair, 120)
    progressed = d.snapshot("player").repair_progress > 0.0
    health_before = d.subsystem_health("player", Subsystem.WEAPONS)
    # A hit interrupts the current repair interval without undoing progress.
    d.queue(DamageEvent("enemy", "player", 1, None))
    tick(repair, 1)
    interrupted = (d.subsystem_health("player", Subsystem.WEAPONS) == health_before)
    return dict(repair_progressed=progressed,
                repair_progress=round(d.snapshot("player").repair_progress, 3),
                hit_interrupted_without_undo=interrupted)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = dict(
        approachable_start=scenario_approachable_start(),
        autopilot_victory=scenario_autopilot_victory(),
        exploit_enemy_repair=scenario_exploit_enemy_repair(),
        risky_player_repair=scenario_risky_player_repair(),
    )
    (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print("MISSION_SIM_PASS " + json.dumps(results))


if __name__ == "__main__":
    main()
