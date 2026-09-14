"""Independent deterministic combat scenario (no window, no vision).

Drives the public weapons/damage/flight boundary exactly as the app does, but
headlessly, to prove the completion-contract behaviors independently of the unit
tests: fire, miss, track (aim follows a moving target), sustain hits against a
moving target through the shared damage model, no-one-hit-kill durability, and
frame-interval determinism (30/60/144 Hz + jittered intervals).

Run from the repository root (needs the project environment):
    .venv/bin/python tools/weapons_sim.py --output artifacts/weapons-sim
"""
import argparse
import json
import math
from pathlib import Path

from panda3d.core import Quat, Vec3

from breach.contracts import PilotInput, ShipView, FIXED_DT
from breach.damage import (CAPITAL_PROFILE, FIGHTER_PROFILE, DamageSystem,
                           ShipState)
from breach.flight import FlightSystem
from breach.timing import FixedStepper
from breach.weapons import WeaponKind, WeaponsSystem

FIRE = PilotInput(fire=True)
IDLE = PilotInput()


def look_quat(forward):
    """Orientation quaternion (wxyz) mapping the ship's +Y nose to `forward`."""
    f = Vec3(*forward)
    f.normalize()
    heading = math.degrees(math.atan2(-f.x, f.y))
    pitch = math.degrees(math.atan2(f.z, math.hypot(f.x, f.y)))
    q = Quat()
    q.setHpr((heading, pitch, 0))
    return tuple(q)


class Player:
    """Mutable aim pose; the pose_provider the WeaponsSystem reads each tick."""
    def __init__(self, position=(0.0, 0.0, 0.0)):
        self.position = Vec3(*position)
        self.forward = Vec3(0, 1, 0)

    def view(self):
        return ShipView("player", tuple(self.position), (0, 0, 0),
                        look_quat(self.forward), 0.0)


def step(w, d, controls, ticks):
    for _ in range(ticks):
        w.fixed_update(FIXED_DT, controls)
        d.fixed_update(FIXED_DT, controls)


def scenario_miss():
    """Fire into empty space: every shot is a miss and nothing is damaged."""
    d = DamageSystem()
    d.spawn("player", FIGHTER_PROFILE)
    d.spawn("fighter", FIGHTER_PROFILE)
    player = Player()
    w = WeaponsSystem(player_entity_id="player", damage=d,
                      pose_provider=player.view)
    w.set_faction("fighter", "enemy")
    # target sits at +X; the player aims +Y -> every shot misses
    w.add_target("fighter", "enemy", (60, 0, 0), (2.5, 2.0, 0.4))
    player.forward = Vec3(0, 1, 0)
    step(w, d, FIRE, 360)
    snap = w.aim_snapshot()
    assert snap.misses > 0, snap
    assert snap.hits == 0, snap
    assert d.snapshot("fighter").hull_hp == FIGHTER_PROFILE.hull_max_hp
    return dict(misses=snap.misses, fired=snap.fired,
                fighter_hull_hp=d.snapshot("fighter").hull_hp)


def scenario_track_and_sustain():
    """Aim follows a slowly moving fighter; sustained fire destroys it through
    the shared damage model (never a one-hit kill)."""
    d = DamageSystem()
    d.spawn("player", FIGHTER_PROFILE)
    d.spawn("fighter", FIGHTER_PROFILE)
    player = Player()
    w = WeaponsSystem(player_entity_id="player", damage=d,
                      pose_provider=player.view)
    w.set_faction("fighter", "enemy")
    w.add_target("fighter", "enemy", (0, 60, 0), (2.5, 2.0, 0.4))
    fighter_pos = Vec3(0, 60, 0)
    first_hit_hull = None
    for tick in range(600):
        # slow weave + gentle closure so the aim must track the moving pose
        fighter_pos = Vec3(8 * math.sin(tick * 0.002), 60 - tick * 0.03, 0)
        w.set_target_pose("fighter", tuple(fighter_pos))
        player.forward = fighter_pos.normalized()
        w.fixed_update(FIXED_DT, FIRE)
        d.fixed_update(FIXED_DT, FIRE)
        if first_hit_hull is None and w.aim_snapshot().hits == 1:
            first_hit_hull = d.snapshot("fighter").hull_hp
    snap = w.aim_snapshot()
    final_hull = d.snapshot("fighter").hull_hp
    assert snap.hits > 1, snap
    assert first_hit_hull is not None and 0 < first_hit_hull < FIGHTER_PROFILE.hull_max_hp
    assert final_hull == 0.0, final_hull  # sustained fire destroyed it
    return dict(hits=snap.hits, misses=snap.misses,
                first_hit_hull=first_hit_hull, final_hull=final_hull)


def scenario_no_one_hit_kill():
    """A single torpedo (the heaviest shot) must not one-hit a fighter or capital."""
    d = DamageSystem()
    d.spawn("player", FIGHTER_PROFILE)
    d.spawn("fighter", FIGHTER_PROFILE)
    d.spawn("capital", CAPITAL_PROFILE)
    player = Player()
    w = WeaponsSystem(player_entity_id="player", damage=d,
                      pose_provider=player.view)
    w.set_faction("fighter", "enemy")
    w.set_faction("capital", "enemy")
    w.add_target("fighter", "enemy", (0, 40, 0), (2.5, 2.0, 0.4))
    w.add_target("capital", "enemy", (0, 120, 0), (8, 23, 4))
    # torpedo at the fighter
    w.fire("player", (0, 0, 0), (0, 1, 0), weapon=WeaponKind.TORPEDO)
    step(w, d, IDLE, 120)  # 50 m/s reaches y~38 in < 1 s
    fighter = d.snapshot("fighter")
    assert fighter.state is not ShipState.DESTROYED
    assert 0 < fighter.hull_hp < FIGHTER_PROFILE.hull_max_hp
    # torpedo at the capital (clear the fighter's hitbox so it is not re-hit)
    w.remove_target("fighter")
    w.fire("player", (0, 0, 0), (0, 1, 0), weapon=WeaponKind.TORPEDO)
    step(w, d, IDLE, 240)  # reach y~118 in ~2.4 s
    capital = d.snapshot("capital")
    assert capital.state is ShipState.HEALTHY
    assert capital.hull_hp == CAPITAL_PROFILE.hull_max_hp - 90.0
    return dict(fighter_hull_after_torpedo=fighter.hull_hp,
                capital_hull_after_torpedo=capital.hull_hp)


def scenario_frame_intervals():
    """Held fire against a stationary target yields identical combat state at
    30, 60, 144 Hz and jittered intervals (frame-rate independence)."""
    def run(intervals):
        flight = FlightSystem()
        d = DamageSystem(player_entity_id="player",
                         speed_provider=lambda _eid: flight.velocity.length())
        flight.damage = d
        d.spawn("player", FIGHTER_PROFILE)
        d.spawn("capital", CAPITAL_PROFILE)
        w = WeaponsSystem(player_entity_id="player", damage=d,
                          pose_provider=flight.snapshot)
        w.set_faction("capital", "enemy")
        w.add_target("capital", "enemy", (0, 85, 4), (8, 23, 4))
        stepper = FixedStepper(flight, systems=[w, d])
        for dt in intervals:
            stepper.advance(dt, lambda: PilotInput(fire=True))
        snap = w.aim_snapshot()
        return (snap.fired, snap.hits, snap.misses,
                d.snapshot("capital").hull_hp)

    sequences = ([1 / 30] * 180, [1 / 60] * 360, [1 / 144] * 864,
                 [.005, .025, .01, .06] * 60)
    results = [run(s) for s in sequences]
    assert all(r == results[0] for r in results), results
    fired, hits, misses, capital_hp = results[0]
    assert fired > 0 and hits > 0 and misses == 0, results[0]
    assert capital_hp == CAPITAL_PROFILE.hull_max_hp - hits * 12.0
    return dict(fired=fired, hits=hits, misses=misses,
                capital_hull_hp=capital_hp)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = dict(
        miss=scenario_miss(),
        track_and_sustain=scenario_track_and_sustain(),
        no_one_hit_kill=scenario_no_one_hit_kill(),
        frame_intervals=scenario_frame_intervals(),
    )
    (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print("WEAPONS_SIM_PASS " + json.dumps(results))


if __name__ == "__main__":
    main()
