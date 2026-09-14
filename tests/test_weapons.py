"""Deterministic weapons and hit-resolution tests (node 4).

Covers the completion contract: fire cadence/cooldown, projectile kinematics,
hit and miss resolution, ownership filtering, resource (cooldown + heat)
behavior, damaged/repair-impaired weapons, weapon cycling and aim-feedback
state, no-one-hit-kill durability, and frame-interval determinism against the
shared FixedStepper pipeline.
"""
import dataclasses
import unittest

from panda3d.core import Quat, Vec3

from breach.contracts import (DamageEvent, PilotInput, RepairIntent, ShipView,
                              Subsystem, FIXED_DT)
from breach.damage import (CAPITAL_PROFILE, FIGHTER_PROFILE, DamageSystem,
                           ShipState)
from breach.flight import FlightSystem
from breach.timing import FixedStepper
from breach.weapons import (AimState, WeaponKind, WeaponSpec, WeaponsSystem,
                            WEAPONS, MUZZLE_OFFSET)

FIRE = PilotInput(fire=True)
IDLE = PilotInput()


def pose(position=(0, 0, 0), orientation=(1, 0, 0, 0)):
    return lambda: ShipView("player", position, (0, 0, 0), orientation, 0.0)


def system(damage=None, provider=None, specs=None):
    d = damage if damage is not None else DamageSystem()
    if "player" not in d.entities:
        d.spawn("player", FIGHTER_PROFILE)
    w = WeaponsSystem(player_entity_id="player", damage=d,
                      pose_provider=provider or pose(), specs=specs)
    return d, w


def step(d, w, controls, ticks):
    for _ in range(ticks):
        w.fixed_update(FIXED_DT, controls)
        d.fixed_update(FIXED_DT, controls)


class WeaponSpecTests(unittest.TestCase):
    def test_spec_validation_and_catalog(self):
        self.assertEqual(set(WEAPONS), {WeaponKind.CANNON, WeaponKind.SCATTER,
                                        WeaponKind.TORPEDO})
        base = dict(kind=WeaponKind.CANNON, name="x", damage=10, cooldown=0.1,
                    projectile_speed=100, max_range=500, heat_per_shot=5,
                    heat_capacity=50, cool_rate=10)
        for kw in ({"damage": 0}, {"cooldown": -1}, {"projectile_speed": 0},
                   {"max_range": float("nan")}, {"heat_per_shot": 0},
                   {"heat_capacity": -1}, {"cool_rate": float("inf")},
                   {"spread": -0.1}, {"pellets": 0}):
            with self.assertRaises(ValueError, msg=kw):
                WeaponSpec(**{**base, **kw})

    def test_weapons_are_distinct_and_accessible(self):
        specs = [WEAPONS[k] for k in (WeaponKind.CANNON, WeaponKind.SCATTER,
                                      WeaponKind.TORPEDO)]
        self.assertEqual(len({s.damage for s in specs}), 3)
        self.assertEqual(len({s.cooldown for s in specs}), 3)
        self.assertEqual(len({s.projectile_speed for s in specs}), 3)
        cannon, torpedo = WEAPONS[WeaponKind.CANNON], WEAPONS[WeaponKind.TORPEDO]
        self.assertLess(cannon.cooldown, torpedo.cooldown)
        self.assertLess(cannon.damage, torpedo.damage)
        self.assertGreater(cannon.projectile_speed, torpedo.projectile_speed)
        self.assertEqual(WEAPONS[WeaponKind.SCATTER].pellets, 5)


class CadenceTests(unittest.TestCase):
    def test_cannon_cadence_respects_cooldown(self):
        d, w = system()
        step(d, w, FIRE, 60)
        # 0.15 s cooldown == 9 fixed ticks -> shots on ticks 1,10,...,55
        self.assertEqual(w.aim_snapshot().fired, 7)

    def test_fire_rejected_within_cooldown(self):
        d, w = system()
        self.assertTrue(w.fire("player", (0, 0, 0), (0, 1, 0)))
        self.assertFalse(w.fire("player", (0, 0, 0), (0, 1, 0)))
        self.assertGreater(w.aim_snapshot().cooldown_remaining, 0)
        self.assertFalse(w.aim_snapshot().ready)

    def test_scatter_fires_five_pellets(self):
        d, w = system()
        w.select_weapon("player", WeaponKind.SCATTER)
        self.assertTrue(w.fire("player", (0, 0, 0), (0, 1, 0)))
        self.assertEqual(len(w.projectiles), 5)
        self.assertEqual(w.aim_snapshot().fired, 5)


class KinematicsTests(unittest.TestCase):
    def test_projectile_moves_along_direction(self):
        d, w = system()
        w.fire("player", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 3)
        p = w.projectiles[0]
        self.assertAlmostEqual(p.position.y, 160.0 / 60.0 * 3, places=5)
        self.assertAlmostEqual(p.position.x, 0.0)
        self.assertAlmostEqual(p.position.z, 0.0)

    def test_player_fire_spawns_from_muzzle_along_forward(self):
        d, w = system()
        step(d, w, FIRE, 1)
        p = w.projectiles[0]
        # spawns at (0, MUZZLE_OFFSET, 0) then advances one tick
        self.assertAlmostEqual(p.position.y, MUZZLE_OFFSET + 160.0 / 60.0, places=4)
        self.assertAlmostEqual(p.position.x, 0.0)

    def test_player_fire_respects_aim_orientation(self):
        q = Quat()
        q.setHpr((180, 0, 0))  # nose -> -Y
        d, w = system(provider=pose(position=(0, 0, 0), orientation=tuple(q)))
        step(d, w, FIRE, 1)
        self.assertLess(w.projectiles[0].position.y, 0)
        self.assertAlmostEqual(w.projectiles[0].position.x, 0, places=4)


class HitResolutionTests(unittest.TestCase):
    def test_hit_routes_damage_through_damage_system(self):
        d, w = system()
        d.spawn("enemy", FIGHTER_PROFILE)
        w.set_faction("enemy", "enemy")
        w.add_target("enemy", "enemy", (0, 30, 0), (2, 2, 2))
        w.fire("player", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 20)
        self.assertEqual(w.aim_snapshot().hits, 1)
        self.assertEqual(len(w.projectiles), 0)
        self.assertAlmostEqual(d.snapshot("enemy").hull_hp, 100.0 - 12.0, places=5)
        self.assertEqual(len(w.last_events), 0)  # consumed on the hit tick

    def test_miss_expires_projectile_without_damage(self):
        d, w = system()
        w.fire("player", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 320)  # > 800 m range at 160 m/s
        self.assertEqual(w.aim_snapshot().misses, 1)
        self.assertEqual(w.aim_snapshot().hits, 0)
        self.assertEqual(len(w.projectiles), 0)
        self.assertEqual(d.events, [])

    def test_off_axis_fire_never_damages_target(self):
        d, w = system()
        d.spawn("enemy", FIGHTER_PROFILE)
        w.set_faction("enemy", "enemy")
        w.add_target("enemy", "enemy", (30, 30, 0), (2, 2, 2))  # off the +Y line
        w.fire("player", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 400)
        self.assertEqual(w.aim_snapshot().misses, 1)
        self.assertAlmostEqual(d.snapshot("enemy").hull_hp, 100.0)


class OwnershipTests(unittest.TestCase):
    def test_projectile_skips_own_faction(self):
        d, w = system()
        d.spawn("ally", FIGHTER_PROFILE)
        d.spawn("enemy", FIGHTER_PROFILE)
        w.set_faction("ally", "player")
        w.set_faction("enemy", "enemy")
        w.add_target("ally", "player", (0, 10, 0), (2, 2, 2))
        w.add_target("enemy", "enemy", (0, 30, 0), (2, 2, 2))
        w.fire("player", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 30)
        self.assertEqual(w.aim_snapshot().hits, 1)
        self.assertAlmostEqual(d.snapshot("ally").hull_hp, 100.0)
        self.assertAlmostEqual(d.snapshot("enemy").hull_hp, 100.0 - 12.0)

    def test_enemy_projectile_does_not_hit_friendly(self):
        d, w = system()
        d.spawn("enemy1", FIGHTER_PROFILE)
        d.spawn("enemy2", FIGHTER_PROFILE)
        w.set_faction("enemy1", "enemy")
        w.set_faction("enemy2", "enemy")
        w.add_target("enemy2", "enemy", (0, 20, 0), (2, 2, 2))
        w.fire("enemy1", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 400)
        self.assertEqual(w.aim_snapshot().hits, 0)
        self.assertEqual(w.aim_snapshot().misses, 1)
        self.assertAlmostEqual(d.snapshot("enemy2").hull_hp, 100.0)

    def test_enemy_projectile_hits_player(self):
        d, w = system()
        w.set_faction("enemy1", "enemy")
        w.add_target("player", "player", (0, 20, 0), (2, 2, 2))
        w.fire("enemy1", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 30)
        self.assertEqual(w.aim_snapshot().hits, 1)
        self.assertAlmostEqual(d.snapshot("player").hull_hp, 100.0 - 12.0)


class ResourceTests(unittest.TestCase):
    def test_heat_throttles_sustained_fire(self):
        spec = WeaponSpec(WeaponKind.CANNON, "test", damage=10, cooldown=0.1,
                          projectile_speed=100, max_range=500, heat_per_shot=6,
                          heat_capacity=10, cool_rate=0.0)
        d, w = system(specs={WeaponKind.CANNON: spec})
        step(d, w, FIRE, 20)
        self.assertEqual(w.aim_snapshot().fired, 1)  # second shot refused by heat
        self.assertTrue(w.aim_snapshot().overheated)

    def test_heat_cools_over_time(self):
        d, w = system()
        w.fire("player", (0, 0, 0), (0, 1, 0))
        self.assertAlmostEqual(w._heat[WeaponKind.CANNON], 6.0)
        step(d, w, IDLE, 60)  # 1 s at 30 heat/s cools the 6 heat to zero
        self.assertAlmostEqual(w._heat[WeaponKind.CANNON], 0.0)


class ImpairmentTests(unittest.TestCase):
    def test_weapon_impairment_reduces_damage(self):
        d, w = system()
        d.queue(DamageEvent("e", "player", 20, Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertAlmostEqual(w.weapons_multiplier("player"), 0.5)
        d.spawn("enemy", FIGHTER_PROFILE)
        w.set_faction("enemy", "enemy")
        w.add_target("enemy", "enemy", (0, 30, 0), (2, 2, 2))
        w.fire("player", (0, 0, 0), (0, 1, 0))
        step(d, w, IDLE, 20)
        self.assertAlmostEqual(d.snapshot("enemy").hull_hp, 100.0 - 6.0, places=5)

    def test_destroyed_weapons_cannot_fire(self):
        d, w = system()
        d.queue(DamageEvent("e", "player", 40, Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertAlmostEqual(w.weapons_multiplier("player"), 0.0)
        self.assertFalse(w.fire("player", (0, 0, 0), (0, 1, 0)))
        self.assertEqual(w.aim_snapshot().fired, 0)
        self.assertTrue(w.aim_snapshot().firing_blocked)

    def test_repair_blocks_firing(self):
        d, w = system()
        # damage weapons first so the repair target stays engaged (full-health
        # repair completes immediately and would clear the lock)
        d.queue(DamageEvent("e", "player", 40, Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, PilotInput())
        d.queue(RepairIntent("player", Subsystem.WEAPONS, True))
        d.fixed_update(FIXED_DT, PilotInput())
        self.assertTrue(d.flight_performance("player").repair_locked)
        self.assertFalse(w.fire("player", (0, 0, 0), (0, 1, 0)))
        self.assertTrue(w.aim_snapshot().firing_blocked)


class SelectionAndFeedbackTests(unittest.TestCase):
    def test_cycle_weapon_wraps_through_three(self):
        d, w = system()
        self.assertEqual(w.weapon, WeaponKind.CANNON)
        w.fixed_update(FIXED_DT, PilotInput(cycle_weapon=True))
        self.assertEqual(w.weapon, WeaponKind.SCATTER)
        w.fixed_update(FIXED_DT, PilotInput(cycle_weapon=True))
        self.assertEqual(w.weapon, WeaponKind.TORPEDO)
        w.fixed_update(FIXED_DT, PilotInput(cycle_weapon=True))
        self.assertEqual(w.weapon, WeaponKind.CANNON)

    def test_target_lock_cycles_and_reports_fire_solution(self):
        d, w = system()
        w.add_target("fighter-1", "enemy", (0, 50, 0), (2, 2, 2))
        w.add_target("fighter-2", "enemy", (0, 80, 0), (2, 2, 2))
        w.add_target("ally", "player", (0, 20, 0), (2, 2, 2))
        w.fixed_update(FIXED_DT, PilotInput(target_next=True))
        snap = w.aim_snapshot()
        self.assertIn(snap.locked_target, ("fighter-1", "fighter-2"))
        self.assertTrue(snap.lock_in_range)
        self.assertTrue(snap.lock_in_front)
        self.assertTrue(snap.on_target)

    def test_aim_feedback_state_changes_when_target_moves(self):
        d, w = system()
        w.add_target("fighter-1", "enemy", (0, 50, 0), (2, 2, 2))
        w.fixed_update(FIXED_DT, PilotInput(target_next=True))
        self.assertTrue(w.aim_snapshot().on_target)
        w.set_target_pose("fighter-1", (60, 50, 0))
        self.assertFalse(w.aim_snapshot().on_target)


class MovingTargetTests(unittest.TestCase):
    def test_hit_resolves_against_moving_target(self):
        d, w = system()
        d.spawn("enemy", FIGHTER_PROFILE)
        w.set_faction("enemy", "enemy")
        w.add_target("enemy", "enemy", (0, 60, 0), (2, 2, 2))
        w.fire("player", (0, 0, 0), (0, 1, 0))
        for _ in range(40):
            # target closes toward the player each tick (moves -Y by 1 m)
            target = w._targets["enemy"]
            target.position = target.position + Vec3(0, -1, 0)
            w.fixed_update(FIXED_DT, IDLE)
            d.fixed_update(FIXED_DT, IDLE)
            if w.aim_snapshot().hits:
                break
        self.assertEqual(w.aim_snapshot().hits, 1)
        self.assertAlmostEqual(d.snapshot("enemy").hull_hp, 100.0 - 12.0)

    def test_dodging_target_is_a_miss(self):
        d, w = system()
        d.spawn("enemy", FIGHTER_PROFILE)
        w.set_faction("enemy", "enemy")
        w.add_target("enemy", "enemy", (0, 50, 0), (2, 2, 2))
        w.fire("player", (0, 0, 0), (0, 1, 0))
        # move the target off the projectile's +Y line before it arrives
        w.set_target_pose("enemy", (50, 0, 0))
        step(d, w, IDLE, 400)
        self.assertEqual(w.aim_snapshot().misses, 1)
        self.assertAlmostEqual(d.snapshot("enemy").hull_hp, 100.0)


class DurabilityTests(unittest.TestCase):
    def test_no_one_hit_kill_for_fighter_or_capital(self):
        self.assertLess(WEAPONS[WeaponKind.TORPEDO].damage,
                        FIGHTER_PROFILE.hull_max_hp)
        self.assertLess(WEAPONS[WeaponKind.TORPEDO].damage,
                        CAPITAL_PROFILE.hull_max_hp)

    def test_single_torpedo_leaves_fighter_alive(self):
        d, w = system()
        d.spawn("enemy", FIGHTER_PROFILE)
        w.set_faction("enemy", "enemy")
        w.add_target("enemy", "enemy", (0, 30, 0), (2, 2, 2))
        w.fire("player", (0, 0, 0), (0, 1, 0), weapon=WeaponKind.TORPEDO)
        step(d, w, IDLE, 60)
        self.assertGreater(d.snapshot("enemy").hull_hp, 0.0)
        self.assertNotEqual(d.snapshot("enemy").state, ShipState.DESTROYED)

    def test_capital_withstands_single_shot(self):
        d, w = system()
        d.spawn("capital", CAPITAL_PROFILE)
        w.set_faction("capital", "enemy")
        w.add_target("capital", "enemy", (0, 30, 0), (8, 23, 4))
        w.fire("player", (0, 0, 0), (0, 1, 0), weapon=WeaponKind.TORPEDO)
        step(d, w, IDLE, 60)
        self.assertGreater(d.snapshot("capital").hull_hp, 2000)
        self.assertEqual(d.snapshot("capital").state, ShipState.HEALTHY)


class FrameIntervalTests(unittest.TestCase):
    def test_weapons_deterministic_across_frame_intervals(self):
        def run(intervals):
            flight = FlightSystem()
            d = DamageSystem(player_entity_id="player",
                             speed_provider=lambda _eid: flight.velocity.length())
            flight.damage = d
            d.spawn("player", FIGHTER_PROFILE)
            d.spawn("enemy", FIGHTER_PROFILE)
            w = WeaponsSystem(player_entity_id="player", damage=d,
                              pose_provider=flight.snapshot)
            w.set_faction("enemy", "enemy")
            w.add_target("enemy", "enemy", (0, 30, 4), (2, 2, 2))
            stepper = FixedStepper(flight, systems=[w, d])
            for dt in intervals:
                stepper.advance(dt, lambda: PilotInput(fire=True))
            snap = w.aim_snapshot()
            return (snap.fired, snap.hits, snap.misses,
                    d.snapshot("enemy").hull_hp)

        sequences = ([1 / 30] * 180, [1 / 60] * 360, [1 / 144] * 864,
                     [.005, .025, .01, .06] * 60)
        results = [run(s) for s in sequences]
        self.assertTrue(all(r == results[0] for r in results), results)
        self.assertGreater(results[0][1], 0)  # sustained hits actually occurred


class ValidationTests(unittest.TestCase):
    def test_fixed_update_rejects_invalid_dt(self):
        d, w = system()
        for dt in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                w.fixed_update(dt, PilotInput())

    def test_fire_rejects_zero_direction(self):
        d, w = system()
        with self.assertRaises(ValueError):
            w.fire("player", (0, 0, 0), (0, 0, 0))

    def test_target_and_weapon_registry_validation(self):
        d, w = system()
        with self.assertRaises(KeyError):
            w.set_target_pose("ghost", (0, 0, 0))
        w.add_target("a", "enemy", (0, 0, 0), (1, 1, 1))
        with self.assertRaises(ValueError):
            w.add_target("a", "enemy", (0, 0, 0), (1, 1, 1))
        with self.assertRaises(ValueError):
            w.select_weapon("player", "nope")
        with self.assertRaises(ValueError):
            w.fire("player", (0, 0, 0), (0, 1, 0), weapon="nope")

    def test_aim_snapshot_is_frozen(self):
        d, w = system()
        snap = w.aim_snapshot()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.ready = True


if __name__ == "__main__":
    unittest.main()
