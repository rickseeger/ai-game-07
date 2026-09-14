"""Independent deterministic enemy/capital scenarios (no window, no vision).

Drives the public EnemySystem / WeaponsSystem / DamageSystem boundary headlessly
to prove the node-5 completion contract independently of the unit tests:
pursuit, attack, subsystem impairment (enemies do NOT ignore penalties), repair
entry/exit and interruption, destruction, capital danger + prolonged attack, and
frame-interval determinism. Every scenario reuses the SAME flight/weapon/damage
rules as the player; there is no separate enemy health or recovery model.

Run from the repository root (needs the project environment):
    .venv/bin/python tools/enemies_sim.py --output artifacts/enemies-sim
"""
import argparse
import json
from pathlib import Path

from panda3d.core import Vec3

from breach.contracts import DamageEvent, PilotInput, ShipView, Subsystem, FIXED_DT
from breach.damage import CAPITAL_PROFILE, FIGHTER_PROFILE, DamageSystem, ShipState
from breach.enemies import EnemySystem, look_quat
from breach.weapons import WeaponsSystem, WEAPONS_WITH_TURRET

IDLE = PilotInput()


class StaticPlayer:
    def __init__(self, position=(0.0, 0.0, 0.0), forward=(0, 1, 0)):
        self.position = Vec3(*position)
        self.forward = Vec3(*forward)

    def view(self):
        return ShipView("player", tuple(self.position), (0, 0, 0),
                        look_quat(self.forward), 0.0)


def build(player_position=(0, 0, 0)):
    d = DamageSystem(player_entity_id="player")
    player = StaticPlayer(player_position)
    w = WeaponsSystem(player_entity_id="player", damage=d, pose_provider=player.view,
                      specs=WEAPONS_WITH_TURRET)
    d.spawn("player", FIGHTER_PROFILE)
    e = EnemySystem(player_entity_id="player", damage=d, weapons=w,
                    player_pose_provider=player.view)
    d.speed_provider = e.speed_of
    return d, w, e, player


def step(e, w, d, ticks, controls=None):
    controls = controls if controls is not None else IDLE
    for _ in range(ticks):
        e.fixed_update(FIXED_DT, controls)
        w.fixed_update(FIXED_DT, controls)
        d.fixed_update(FIXED_DT, controls)


def scenario_pursuit():
    d, w, e, _ = build()
    e.spawn_fighter("f1", (0, 60, 0), orientation=look_quat((0, 1, 0)))  # faces away
    flight = e._ships["f1"].flight
    min_dist = flight.position.length()
    faced = False
    for _ in range(480):
        e.fixed_update(FIXED_DT, IDLE)
        w.fixed_update(FIXED_DT, IDLE)
        d.fixed_update(FIXED_DT, IDLE)
        delta = Vec3(0, 0, 0) - flight.position
        min_dist = min(min_dist, delta.length())
        if delta.length() > 1e-6:
            if flight.orientation.xform(Vec3(0, 1, 0)).dot(delta / delta.length()) > 0.95:
                faced = True
    assert faced, "fighter never turned to face the player"
    assert min_dist < 20.0, min_dist
    return dict(initial_distance=60.0, min_distance=round(min_dist, 2),
                turned_to_face=faced)


def scenario_attack():
    d, w, e, _ = build()
    e.spawn_fighter("f1", (0, 30, 0), orientation=look_quat((0, -1, 0)))
    step(e, w, d, 120)
    fired = e._ships["f1"].fired
    player_hull = d.snapshot("player").hull_hp
    assert fired > 0, fired
    assert player_hull < FIGHTER_PROFILE.hull_max_hp, player_hull
    return dict(fighter_shots=fired, player_hull_hp=player_hull)


def scenario_impairment():
    d, w, e, _ = build()
    e.spawn_fighter("intact", (0, 120, 0), orientation=look_quat((0, -1, 0)))
    e.spawn_fighter("no_engine", (0, 120, 0), orientation=look_quat((0, -1, 0)))
    e.spawn_fighter("no_weapons", (0, 30, 0), orientation=look_quat((0, -1, 0)))
    d.queue(DamageEvent("script", "no_engine", 40, Subsystem.ENGINE))
    d.queue(DamageEvent("script", "no_weapons", 40, Subsystem.WEAPONS))
    d.fixed_update(FIXED_DT, IDLE)
    step(e, w, d, 120)
    intact_speed = e.speed_of("intact")
    no_engine_speed = e.speed_of("no_engine")
    assert intact_speed > 1.0, intact_speed
    assert no_engine_speed == 0.0, no_engine_speed  # engine failure -> cannot thrust
    assert e._ships["no_weapons"].fired == 0, "destroyed weapons must not fire"
    return dict(intact_speed=round(intact_speed, 2),
                engine_destroyed_speed=no_engine_speed,
                weapons_destroyed_shots=e._ships["no_weapons"].fired)


def scenario_repair_entry_exit():
    d, w, e, _ = build()
    e.spawn_fighter("f1", (0, 40, 0), orientation=look_quat((0, -1, 0)))
    d.queue(DamageEvent("script", "f1", FIGHTER_PROFILE.subsystem_max_hp, Subsystem.WEAPONS))
    d.fixed_update(FIXED_DT, IDLE)
    step(e, w, d, 10)
    assert e._ships["f1"].repair_target is Subsystem.WEAPONS, "must enter repair"
    assert e._ships["f1"].fired == 0, "must not fire while repairing"
    step(e, w, d, 60)
    early = d.subsystem_health("f1", Subsystem.WEAPONS)
    assert 0.0 < early < FIGHTER_PROFILE.repair_cap, early  # timed, not instant
    step(e, w, d, 300)
    final = d.subsystem_health("f1", Subsystem.WEAPONS)
    assert abs(final - FIGHTER_PROFILE.repair_cap) < 1e-3, final
    assert e._ships["f1"].repair_target is None, "must exit repair"
    return dict(repair_cap=FIGHTER_PROFILE.repair_cap,
                early_health_1s=round(early, 4), final_health=round(final, 4),
                exited=True)


def scenario_repair_interrupted():
    d, w, e, _ = build()
    e.spawn_fighter("f1", (0, 40, 0), orientation=look_quat((0, -1, 0)))
    d.queue(DamageEvent("script", "f1", FIGHTER_PROFILE.subsystem_max_hp, Subsystem.WEAPONS))
    d.fixed_update(FIXED_DT, IDLE)
    step(e, w, d, 10)
    before = d.subsystem_health("f1", Subsystem.WEAPONS)
    base = d.repair_interruptions["f1"]
    for _ in range(12):
        d.queue(DamageEvent("player", "f1", 1, None))
        e.fixed_update(FIXED_DT, IDLE)
        w.fixed_update(FIXED_DT, IDLE)
        d.fixed_update(FIXED_DT, IDLE)
    interruptions = d.repair_interruptions["f1"] - base
    assert interruptions > 0, interruptions
    assert d.subsystem_health("f1", Subsystem.WEAPONS) == before, "no hidden recovery"
    assert e._ships["f1"].repair_target is None, "aborted under sustained fire"
    return dict(interruptions=interruptions, health_unchanged=True, aborted=True)


def scenario_destruction():
    d, w, e, _ = build()
    e.spawn_fighter("f1", (0, 30, 0), orientation=look_quat((0, -1, 0)))
    d.queue(DamageEvent("player", "f1", FIGHTER_PROFILE.hull_max_hp, None))
    step(e, w, d, 3)
    assert d.snapshot("f1").state is ShipState.DESTROYED
    assert not e.is_alive("f1")
    assert "f1" not in w.targets
    fired = e._ships["f1"].fired
    step(e, w, d, 60)
    assert e._ships["f1"].fired == fired, "destroyed ship must be inert"
    return dict(destroyed=True, removed_from_targets=True, inert=True)


def scenario_capital_danger():
    d, w, e, _ = build()
    e.spawn_capital("capital", (0, 100, 0))
    step(e, w, d, 360)
    fired = e._ships["capital"].fired
    player_hull = d.snapshot("player").hull_hp
    assert fired > 0, fired
    assert player_hull < FIGHTER_PROFILE.hull_max_hp, player_hull
    return dict(capital_shots=fired, player_hull_hp=player_hull)


def scenario_capital_prolonged():
    d, w, e, _ = build()
    e.spawn_capital("capital", (0, 100, 0))  # registers the capital hitbox
    # Isolate hull-pool durability: sustained cannon fire with no retaliation.
    for _ in range(1800):  # 30 s of continuous fire
        w.fixed_update(FIXED_DT, PilotInput(fire=True))
        d.fixed_update(FIXED_DT, IDLE)
    capital = d.snapshot("capital")
    assert capital.state is not ShipState.DESTROYED, "capital fell too fast"
    assert 0.0 < capital.hull_hp < CAPITAL_PROFILE.hull_max_hp
    # Even 40 s of fire does not destroy it (destruction is deliberately long).
    for _ in range(600):
        w.fixed_update(FIXED_DT, PilotInput(fire=True))
        d.fixed_update(FIXED_DT, IDLE)
    still = d.snapshot("capital")
    assert still.state is not ShipState.DESTROYED, "capital fell inside 40 s"
    return dict(hull_after_30s=round(capital.hull_hp, 1),
                hull_after_40s=round(still.hull_hp, 1),
                hull_max=CAPITAL_PROFILE.hull_max_hp,
                state_after_40s=still.state.value)


def scenario_capital_repair():
    d, w, e, _ = build()
    e.spawn_capital("capital", (0, 100, 0))
    d.queue(DamageEvent("script", "capital", CAPITAL_PROFILE.subsystem_max_hp, Subsystem.WEAPONS))
    d.fixed_update(FIXED_DT, IDLE)
    step(e, w, d, 10)
    assert e._ships["capital"].repair_target is Subsystem.WEAPONS
    fired_at_entry = e._ships["capital"].fired
    step(e, w, d, 120)
    assert e._ships["capital"].fired == fired_at_entry, "capital must not fire while repairing"
    early = d.subsystem_health("capital", Subsystem.WEAPONS)
    assert 0.0 < early < CAPITAL_PROFILE.repair_cap, early
    step(e, w, d, 2000)
    final = d.subsystem_health("capital", Subsystem.WEAPONS)
    assert abs(final - CAPITAL_PROFILE.repair_cap) < 1e-2, final
    assert e._ships["capital"].repair_target is None
    return dict(early_health_2s=round(early, 4), final_health=round(final, 4),
                repair_cap=CAPITAL_PROFILE.repair_cap, held_fire=True)


def scenario_determinism():
    from breach.flight import FlightSystem
    from breach.timing import FixedStepper

    def run(intervals):
        flight = FlightSystem()
        d = DamageSystem(player_entity_id="player")
        w = WeaponsSystem(player_entity_id="player", damage=d,
                          pose_provider=flight.snapshot, specs=WEAPONS_WITH_TURRET)
        d.spawn("player", FIGHTER_PROFILE)
        e = EnemySystem(player_entity_id="player", damage=d, weapons=w,
                        player_pose_provider=flight.snapshot)
        d.speed_provider = (lambda eid: flight.velocity.length()
                            if eid == "player" else e.speed_of(eid))
        flight.damage = d
        e.spawn_fighter("f1", (0, 60, 0), orientation=look_quat((0, -1, 0)))
        e.spawn_capital("capital", (0, 140, 0))
        stepper = FixedStepper(flight, systems=[e, w, d])
        for dt in intervals:
            stepper.advance(dt, lambda: IDLE)
        return (tuple(e.snapshot("f1").position),
                d.snapshot("player").hull_hp,
                e._ships["f1"].fired,
                d.repair_interruptions["f1"])

    seqs = ([1 / 60] * 600, [1 / 30] * 300, [1 / 144] * 1440,
            [.005, .025, .01, .06] * 100)
    results = [run(s) for s in seqs]
    assert all(r == results[0] for r in results), results
    return dict(sequences=4, identical=results[0] == results[1] == results[2] == results[3])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = dict(
        pursuit=scenario_pursuit(),
        attack=scenario_attack(),
        impairment=scenario_impairment(),
        repair_entry_exit=scenario_repair_entry_exit(),
        repair_interrupted=scenario_repair_interrupted(),
        destruction=scenario_destruction(),
        capital_danger=scenario_capital_danger(),
        capital_prolonged=scenario_capital_prolonged(),
        capital_repair=scenario_capital_repair(),
        determinism=scenario_determinism(),
    )
    (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print("ENEMIES_SIM_PASS " + json.dumps(results))


if __name__ == "__main__":
    main()
