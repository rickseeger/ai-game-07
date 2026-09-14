"""Deterministic accumulated-damage and repair simulation tests (node 3).

Covers the completion contract: thresholds, capability loss, repair
progression, interruption/incoming-damage-during-repair, terminal destruction,
capital-vs-fighter durability, and live flight integration (DamageSystem wired
as FlightSystem's FlightDamageService in a FixedStepper lockstep pipeline).
"""
import dataclasses
import unittest

from breach.contracts import DamageEvent, PilotInput, RepairIntent, Subsystem, FIXED_DT
from breach.damage import (
    CAPITAL_PROFILE, FIGHTER_PROFILE, DamageProfile, DamageSystem, HealthView,
    ShipState,
)
from breach.flight import FlightPerformance, FlightSystem
from breach.timing import FixedStepper
from panda3d.core import Vec3


def run(system, controls, ticks):
    for _ in range(ticks):
        system.fixed_update(FIXED_DT, controls)
    return system


class ProfileTests(unittest.TestCase):
    def test_profiles_and_validation(self):
        self.assertEqual(FIGHTER_PROFILE.repair_rate, 40.0 * 0.7 / 4.0)
        self.assertEqual(CAPITAL_PROFILE.repair_rate, 600.0 * 0.7 / 30.0)
        self.assertGreater(CAPITAL_PROFILE.hull_max_hp, FIGHTER_PROFILE.hull_max_hp * 10)
        base = dict(hull_max_hp=100.0, subsystem_max_hp=40.0)
        for kw in ({"hull_max_hp": 0}, {"subsystem_max_hp": -1},
                   {"repair_duration": 0}, {"repair_cap": 0}, {"repair_cap": 1.5},
                   {"repair_max_speed": -1}, {"smoke_at": 0.5, "burn_at": 0.6},
                   {"smoke_at": 1.0}, {"burn_at": 0.0}):
            with self.assertRaises(ValueError, msg=kw):
                DamageProfile("x", **{**base, **kw})


class ThresholdTests(unittest.TestCase):
    def test_hull_state_thresholds(self):
        # fighter hull 100, smoke_at 0.66, burn_at 0.33
        cases = [
            (0, ShipState.HEALTHY),
            (33, ShipState.HEALTHY),    # 0.67 > 0.66
            (34, ShipState.SMOKING),    # 0.66 <= 0.66
            (66, ShipState.SMOKING),    # 0.34
            (67, ShipState.BURNING),    # 0.33 <= 0.33
            (99, ShipState.BURNING),    # 0.01
            (100, ShipState.DESTROYED), # 0.0
            (150, ShipState.DESTROYED), # overkill clamps to 0
        ]
        for amount, expected in cases:
            d = DamageSystem()
            d.spawn("player", FIGHTER_PROFILE)
            d.queue(DamageEvent("e", "player", amount, None))
            d.fixed_update(FIXED_DT, PilotInput())
            self.assertEqual(d.snapshot("player").state, expected, amount)

    def test_subsystem_destroyed_is_full_failure(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        self.assertEqual(d.capability("player", Subsystem.ENGINE), 0.0)
        perf = d.flight_performance("player")
        self.assertEqual((perf.thrust_multiplier, perf.turning_multiplier), (0.0, 0.0))
        v = d.snapshot("player")
        self.assertEqual((v.engine_multiplier, v.turning_multiplier), (0.0, 0.0))


class CapabilityLossTests(unittest.TestCase):
    def test_each_subsystem_measurably_impairs_its_capability(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 20, Subsystem.ENGINE))  # half engine
        d.fixed_update(FIXED_DT, PilotInput())
        v = d.snapshot("player")
        self.assertAlmostEqual(v.engine_multiplier, 0.5)
        self.assertAlmostEqual(v.turning_multiplier, 0.5)
        self.assertAlmostEqual(v.weapons_multiplier, 1.0)
        self.assertAlmostEqual(v.sensors_multiplier, 1.0)
        d.queue(DamageEvent("e", "player", 20, Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, PilotInput())
        v = d.snapshot("player")
        self.assertAlmostEqual(v.weapons_multiplier, 0.5)
        self.assertAlmostEqual(v.engine_multiplier, 0.5)
        d.queue(DamageEvent("e", "player", 20, Subsystem.SENSORS))
        d.fixed_update(FIXED_DT, PilotInput())
        v = d.snapshot("player")
        self.assertAlmostEqual(v.sensors_multiplier, 0.5)

    def test_capability_is_monotone_with_sustained_damage(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        previous = 1.0
        for hit in range(1, 5):
            d.queue(DamageEvent("e", "player", 8, Subsystem.WEAPONS))
            d.fixed_update(FIXED_DT, PilotInput())
            mult = d.snapshot("player").weapons_multiplier
            self.assertLess(mult, previous)
            previous = mult
        self.assertAlmostEqual(previous, 0.2)  # 4 hits of 8 = 32/40 = 0.8 gone


class RepairProgressionTests(unittest.TestCase):
    def make_damaged(self, speed=None):
        d = DamageSystem(speed_provider=(speed if speed is not None
                                         else (lambda _eid: 0.0)))
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, PilotInput())
        return d

    def test_repair_restores_over_time_up_to_cap(self):
        d = self.make_damaged()
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        controls = PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE)
        run(d, controls, 120)  # 2 s * 7 HP/s = 14 HP -> 0.35
        self.assertAlmostEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.35, places=3)
        mid = d.snapshot("player")
        self.assertEqual(mid.repairing, Subsystem.ENGINE)
        self.assertGreater(mid.repair_progress, 0.0)
        self.assertLess(mid.repair_progress, 1.0)
        run(d, controls, 240)  # cap reached at 0.70 (28 HP)
        self.assertAlmostEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.70, places=3)
        run(d, controls, 600)  # no further restoration beyond cap
        self.assertAlmostEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.70, places=3)
        self.assertIsNone(d.snapshot("player").repairing)

    def test_repair_requires_low_mobility(self):
        speed = {"player": 0.0}
        d = self.make_damaged(speed=lambda eid: speed[eid])
        controls = PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE)
        speed["player"] = 10.0
        run(d, controls, 120)
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        speed["player"] = 2.0  # exactly at threshold is allowed
        run(d, controls, 60)
        self.assertGreater(d.subsystem_health("player", Subsystem.ENGINE), 0.0)

    def test_repair_target_switch_resets_progress(self):
        d = self.make_damaged()
        d.queue(DamageEvent("e", "player", 40, Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, PilotInput())
        run(d, PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE), 60)
        self.assertGreater(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        # switching target starts a fresh interval for the new subsystem
        engine_healed = d.subsystem_health("player", Subsystem.ENGINE)
        run(d, PilotInput(repair=True, repair_subsystem=Subsystem.WEAPONS), 1)
        self.assertEqual(d.snapshot("player").repairing, Subsystem.WEAPONS)
        self.assertGreater(d.subsystem_health("player", Subsystem.WEAPONS), 0.0)
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), engine_healed)
        self.assertLess(d.snapshot("player").repair_progress, 0.1)

    def test_release_cancels_repair(self):
        d = self.make_damaged()
        run(d, PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE), 60)
        self.assertGreater(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        healed = d.subsystem_health("player", Subsystem.ENGINE)
        run(d, PilotInput(), 60)  # released R
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), healed)
        self.assertIsNone(d.snapshot("player").repairing)


class InterruptionTests(unittest.TestCase):
    def test_incoming_damage_interrupts_repair_tick(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, PilotInput())
        controls = PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE)
        run(d, controls, 60)
        before = d.subsystem_health("player", Subsystem.ENGINE)
        self.assertGreater(before, 0.0)
        d.queue(DamageEvent("e", "player", 5, None))  # a hit, even hull, interrupts
        d.fixed_update(FIXED_DT, controls)
        self.assertEqual(d.repair_interruptions["player"], 1)
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), before)
        self.assertEqual(d.snapshot("player").repair_progress, 0.0)
        # completed restoration is not undone: progress resumes from 'before'
        run(d, controls, 60)
        self.assertGreater(d.subsystem_health("player", Subsystem.ENGINE), before)

    def test_continuous_damage_blocks_repair_completion(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, PilotInput())
        controls = PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE)
        for _ in range(120):
            d.queue(DamageEvent("e", "player", 1, Subsystem.SENSORS))  # hit every tick
            d.fixed_update(FIXED_DT, controls)
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        self.assertEqual(d.repair_interruptions["player"], 120)


class DestructionTests(unittest.TestCase):
    def test_terminal_destruction_zeroes_capability_and_locks(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 100, None))
        d.fixed_update(FIXED_DT, PilotInput())
        v = d.snapshot("player")
        self.assertEqual(v.state, ShipState.DESTROYED)
        self.assertEqual(v.hull, 0.0)
        self.assertEqual((v.engine_multiplier, v.turning_multiplier,
                          v.weapons_multiplier, v.sensors_multiplier), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(d.flight_performance("player"), FlightPerformance(0.0, 0.0, True))
        self.assertEqual(d.destroyed_this_tick, ["player"])

    def test_destroyed_ship_cannot_repair(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.queue(DamageEvent("e", "player", 100, None))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(d.snapshot("player").state, ShipState.DESTROYED)
        run(d, PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE), 240)
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)

    def test_damage_to_destroyed_ship_is_ignored(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 100, None))
        d.fixed_update(FIXED_DT, PilotInput())
        d.queue(DamageEvent("e", "player", 50, None))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(d.snapshot("player").hull_hp, 0.0)


class DurabilityTests(unittest.TestCase):
    def test_capital_withstands_substantially_longer_attack(self):
        d = DamageSystem()
        d.spawn("fighter", FIGHTER_PROFILE)
        d.spawn("capital", CAPITAL_PROFILE)
        for eid in ("fighter", "capital"):
            d.queue(DamageEvent("e", eid, 200, None))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(d.snapshot("fighter").state, ShipState.DESTROYED)
        capital = d.snapshot("capital")
        self.assertEqual(capital.state, ShipState.HEALTHY)
        self.assertAlmostEqual(capital.hull, 2800.0 / 3000.0, places=5)

    def test_hits_to_destroy_fighter_vs_capital(self):
        for name, profile, hits in (("fighter", FIGHTER_PROFILE, 1),
                                    ("capital", CAPITAL_PROFILE, 30)):
            d = DamageSystem()
            d.spawn(name, profile)
            for _ in range(hits - 1):
                d.queue(DamageEvent("e", name, 100, None))
                d.fixed_update(FIXED_DT, PilotInput())
                self.assertNotEqual(d.snapshot(name).state, ShipState.DESTROYED, name)
            d.queue(DamageEvent("e", name, 100, None))
            d.fixed_update(FIXED_DT, PilotInput())
            self.assertEqual(d.snapshot(name).state, ShipState.DESTROYED, name)


class EventOrderingAndSnapshotTests(unittest.TestCase):
    def test_events_consumed_once_in_enqueue_order(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        first = DamageEvent("e", "player", 30, Subsystem.ENGINE)
        second = DamageEvent("e", "player", 10, Subsystem.ENGINE)
        d.queue(first)
        d.queue(second)
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)  # 30+10=40
        # published copy exposes this tick's consumed events to effects
        self.assertEqual(d.events, [first, second])
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(d.events, [])
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)

    def test_snapshot_is_immutable_copy(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        v = d.snapshot("player")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            v.hull = 0.5
        self.assertEqual(dict(v.subsystems), {s: 1.0 for s in Subsystem})
        # mutating the caller's dict view cannot affect authoritative state
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(v.engine_multiplier, 1.0)  # snapshot taken earlier unchanged

    def test_unknown_entity_and_invalid_input(self):
        d = DamageSystem()
        self.assertEqual(d.capability("ghost", Subsystem.ENGINE), 1.0)
        self.assertEqual(d.flight_performance("ghost"), FlightPerformance())
        with self.assertRaises(KeyError):
            d.snapshot("ghost")
        with self.assertRaises(ValueError):
            d.spawn("player", FIGHTER_PROFILE)
            d.spawn("player", FIGHTER_PROFILE)
        d2 = DamageSystem()
        d2.spawn("player", FIGHTER_PROFILE)
        with self.assertRaises(TypeError):
            d2.queue("not an event")
        for dt in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                d2.fixed_update(dt, PilotInput())

    def test_player_repair_intent_composes_with_external_events(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, PilotInput())
        # an externally queued RepairIntent repairs the player even with R unpressed
        d.queue(RepairIntent("player", Subsystem.ENGINE, True))
        run(d, PilotInput(), 120)
        self.assertGreater(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        d.queue(RepairIntent("player", Subsystem.ENGINE, False))
        d.fixed_update(FIXED_DT, PilotInput())
        healed = d.subsystem_health("player", Subsystem.ENGINE)
        run(d, PilotInput(), 60)
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), healed)

    def test_external_repair_intent_drives_non_player(self):
        d = DamageSystem()
        d.spawn("enemy", FIGHTER_PROFILE)
        d.queue(DamageEvent("e", "enemy", 40, Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertEqual(d.subsystem_health("enemy", Subsystem.WEAPONS), 0.0)
        d.queue(RepairIntent("enemy", Subsystem.WEAPONS, True))
        run(d, PilotInput(), 120)
        self.assertGreater(d.subsystem_health("enemy", Subsystem.WEAPONS), 0.0)
        d.queue(RepairIntent("enemy", Subsystem.WEAPONS, False))
        d.fixed_update(FIXED_DT, PilotInput())
        healed = d.subsystem_health("enemy", Subsystem.WEAPONS)
        run(d, PilotInput(), 60)
        self.assertEqual(d.subsystem_health("enemy", Subsystem.WEAPONS), healed)


class LiveFlightIntegrationTests(unittest.TestCase):
    def test_engine_damage_impairs_live_thrust_and_turning(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        intact = FlightSystem(damage=d)
        c = PilotInput(throttle=1, yaw=1)
        for _ in range(60):
            intact.fixed_update(FIXED_DT, c)
        d.queue(DamageEvent("e", "player", 30, Subsystem.ENGINE))  # engine -> 0.25
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertAlmostEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.25)
        damaged = FlightSystem(damage=d)
        for _ in range(60):
            damaged.fixed_update(FIXED_DT, c)
        self.assertLess(damaged.velocity.length(), intact.velocity.length() * 0.5)
        self.assertLess(damaged.orientation.xform(Vec3(0, 1, 0)).x,
                        intact.orientation.xform(Vec3(0, 1, 0)).x * 0.5)

    def test_destroyed_engine_freezes_live_flight(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        flight = FlightSystem(damage=d)
        d.queue(DamageEvent("e", "player", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, PilotInput())
        for _ in range(60):
            flight.fixed_update(FIXED_DT, PilotInput(throttle=1, yaw=1))
        self.assertEqual(flight.velocity.length(), 0.0)
        self.assertEqual(tuple(flight.orientation), (1, 0, 0, 0))

    def test_repair_engagement_locks_live_flight(self):
        d = DamageSystem()
        d.spawn("player", FIGHTER_PROFILE)
        flight = FlightSystem(damage=d)
        d.queue(DamageEvent("e", "player", 20, Subsystem.ENGINE))
        d.queue(RepairIntent("player", Subsystem.ENGINE, True))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertTrue(d.flight_performance("player").repair_locked)
        for _ in range(30):
            flight.fixed_update(FIXED_DT, PilotInput(throttle=1, yaw=1))
        self.assertEqual(flight.throttle, 0.0)
        self.assertEqual(flight.velocity.length(), 0.0)

    def test_lockstep_scenario_damage_then_repair_then_escape(self):
        flight = FlightSystem()
        d = DamageSystem(player_entity_id="player",
                         speed_provider=lambda _eid: flight.velocity.length())
        flight.damage = d
        d.spawn("player", FIGHTER_PROFILE)
        stepper = FixedStepper(flight, systems=[d])

        def step(n, controls):
            for _ in range(n):
                stepper.advance(FIXED_DT, lambda: controls)

        step(120, PilotInput(throttle=1))
        self.assertGreater(flight.velocity.length(), 5.0)
        d.queue(DamageEvent("enemy", "player", 40, Subsystem.ENGINE))
        step(1, PilotInput())
        self.assertEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.0)
        self.assertEqual(d.flight_performance("player").thrust_multiplier, 0.0)
        # hold repair: flight brakes to a stop, then restores engine to cap
        step(300, PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE))
        self.assertLess(flight.velocity.length(), 0.01)
        self.assertAlmostEqual(d.subsystem_health("player", Subsystem.ENGINE), 0.70, places=3)
        # release repair: escape thrust is restored (at reduced capability)
        step(60, PilotInput(throttle=1))
        self.assertGreater(flight.velocity.length(), 0.0)


if __name__ == "__main__":
    unittest.main()
