"""Deterministic enemy AI and capital tests (node 5).

Covers the completion contract: pursuit, attack, subsystem impairment (enemies
do NOT ignore penalties), vulnerable repair entry/exit and interruption, capital
danger + prolonged attack, destruction, and frame-interval determinism. All
scenarios reuse the SAME FlightSystem / WeaponsSystem / DamageSystem boundaries
the player uses; there is no separate enemy health or recovery model.
"""
import unittest

from panda3d.core import Quat, Vec3

from breach.contracts import DamageEvent, PilotInput, ShipView, Subsystem, FIXED_DT
from breach.damage import CAPITAL_PROFILE, FIGHTER_PROFILE, DamageSystem, ShipState
from breach.enemies import CAPITAL_MUZZLE, EnemySystem, EnemyTuning, look_quat
from breach.weapons import WeaponKind, WeaponsSystem, WEAPONS_WITH_TURRET

IDLE = PilotInput()


class StaticPlayer:
    """A fixed player pose; the pose_provider the systems read each tick."""
    def __init__(self, position=(0.0, 0.0, 0.0), forward=(0, 1, 0)):
        self.position = Vec3(*position)
        self.forward = Vec3(*forward)

    def view(self):
        return ShipView("player", tuple(self.position), (0, 0, 0),
                        look_quat(self.forward), 0.0)


def build(player_position=(0, 0, 0), player_forward=(0, 1, 0)):
    d = DamageSystem(player_entity_id="player")
    player = StaticPlayer(player_position, player_forward)
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


class PursuitTests(unittest.TestCase):
    def test_fighter_turns_and_closes_on_player(self):
        d, w, e, player = build()
        # Spawn facing directly away; the player is behind it.
        e.spawn_fighter("f1", (0, 50, 0), orientation=look_quat((0, 1, 0)))
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
                forward = flight.orientation.xform(Vec3(0, 1, 0))
                if forward.dot(delta / delta.length()) > 0.95:
                    faced = True
        self.assertTrue(faced, "fighter never turned to face the player")
        self.assertLess(min_dist, 20.0)  # closed from 50 m to within 20 m

    def test_fighter_maneuvers_not_static(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 80, 0), orientation=look_quat((0, -1, 0)))
        start = e.snapshot("f1").position
        step(e, w, d, 240)
        end = e.snapshot("f1").position
        moved = Vec3(*end) - Vec3(*start)
        self.assertGreater(moved.length(), 5.0)


class AttackTests(unittest.TestCase):
    def test_fighter_attacks_and_damages_player(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 30, 0), orientation=look_quat((0, -1, 0)))
        step(e, w, d, 90)
        self.assertGreater(e._ships["f1"].fired, 0)
        self.assertLess(d.snapshot("player").hull_hp, FIGHTER_PROFILE.hull_max_hp)

    def test_fighter_holds_fire_out_of_range(self):
        d, w, e, _ = build()
        # Far outside the fighter engagement range (400 m).
        e.spawn_fighter("f1", (0, 3000, 0), orientation=look_quat((0, -1, 0)))
        step(e, w, d, 60)
        self.assertEqual(e._ships["f1"].fired, 0)
        self.assertEqual(d.snapshot("player").hull_hp, FIGHTER_PROFILE.hull_max_hp)


class ImpairmentTests(unittest.TestCase):
    def test_engine_damage_impairs_enemy_flight(self):
        d, w, e, _ = build()
        e.spawn_fighter("intact", (0, 120, 0), orientation=look_quat((0, -1, 0)))
        e.spawn_fighter("crippled", (0, 120, 0), orientation=look_quat((0, -1, 0)))
        d.queue(DamageEvent("script", "crippled", 40, Subsystem.ENGINE))
        d.fixed_update(FIXED_DT, IDLE)  # engine -> 0 (full failure)
        step(e, w, d, 120)
        self.assertGreater(e.speed_of("intact"), 1.0)
        self.assertEqual(e.speed_of("crippled"), 0.0)  # cannot thrust at all

    def test_engine_damage_slows_enemy_partially(self):
        d, w, e, _ = build()
        e.spawn_fighter("intact", (0, 120, 0), orientation=look_quat((0, -1, 0)))
        e.spawn_fighter("half", (0, 120, 0), orientation=look_quat((0, -1, 0)))
        d.queue(DamageEvent("script", "half", 20, Subsystem.ENGINE))  # -> 0.5
        d.fixed_update(FIXED_DT, IDLE)
        step(e, w, d, 120)
        self.assertGreater(e.speed_of("intact"), e.speed_of("half"))
        self.assertGreater(e.speed_of("half"), 0.0)

    def test_weapons_damage_scales_enemy_fire(self):
        d, w, e, _ = build()
        e.spawn_fighter("half", (0, 30, 0), orientation=look_quat((0, -1, 0)))
        d.queue(DamageEvent("script", "half", 20, Subsystem.WEAPONS))  # -> 0.5
        d.fixed_update(FIXED_DT, IDLE)
        step(e, w, d, 90)
        # 12 damage cannon at 0.5 capability -> 6 damage per hit
        self.assertGreater(e._ships["half"].fired, 0)
        self.assertAlmostEqual(d.snapshot("player").hull_hp,
                               100.0 - 6.0 * w.aim_snapshot().hits, places=5)

    def test_destroyed_weapons_blocks_enemy_fire(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 30, 0), orientation=look_quat((0, -1, 0)))
        d.queue(DamageEvent("script", "f1", 40, Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, IDLE)
        step(e, w, d, 90)
        self.assertEqual(e._ships["f1"].fired, 0)
        self.assertEqual(d.snapshot("player").hull_hp, FIGHTER_PROFILE.hull_max_hp)


class RepairTests(unittest.TestCase):
    def cripple(self, d, w, e, entity, subsystem=Subsystem.WEAPONS, amount=None):
        amount = FIGHTER_PROFILE.subsystem_max_hp if amount is None else amount
        d.queue(DamageEvent("script", entity, amount, subsystem))
        d.fixed_update(FIXED_DT, IDLE)

    def test_repair_entry_brakes_and_stops_firing(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 40, 0), orientation=look_quat((0, -1, 0)))
        self.cripple(d, w, e, "f1")
        step(e, w, d, 10)
        self.assertEqual(e._ships["f1"].repair_target, Subsystem.WEAPONS)
        self.assertEqual(d.snapshot("f1").repairing, Subsystem.WEAPONS)
        # Repair engagement is a vulnerable state: no shooting, braked to a stop.
        self.assertAlmostEqual(e.speed_of("f1"), 0.0, places=2)
        self.assertEqual(e._ships["f1"].fired, 0)

    def test_repair_restores_then_exits_and_resumes(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 40, 0), orientation=look_quat((0, -1, 0)))
        self.cripple(d, w, e, "f1")
        step(e, w, d, 10)
        self.assertEqual(e._ships["f1"].repair_target, Subsystem.WEAPONS)
        # Timed, not instant: after 1 s of repair health is small, not the cap.
        step(e, w, d, 60)
        early = d.subsystem_health("f1", Subsystem.WEAPONS)
        self.assertGreater(early, 0.0)
        self.assertLess(early, FIGHTER_PROFILE.repair_cap)
        # Full restoration to the cap and clean exit.
        step(e, w, d, 300)
        self.assertAlmostEqual(d.subsystem_health("f1", Subsystem.WEAPONS),
                               FIGHTER_PROFILE.repair_cap, places=3)
        self.assertIsNone(e._ships["f1"].repair_target)
        self.assertIsNone(d.snapshot("f1").repairing)

    def test_repair_interrupted_and_aborted_under_fire(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 40, 0), orientation=look_quat((0, -1, 0)))
        self.cripple(d, w, e, "f1")
        step(e, w, d, 10)
        self.assertEqual(e._ships["f1"].repair_target, Subsystem.WEAPONS)
        before = d.subsystem_health("f1", Subsystem.WEAPONS)
        self.assertLess(before, FIGHTER_PROFILE.repair_cap)  # still crippled
        base = d.repair_interruptions["f1"]
        for _ in range(12):
            d.queue(DamageEvent("player", "f1", 1, None))  # hit every tick
            e.fixed_update(FIXED_DT, IDLE)
            w.fixed_update(FIXED_DT, IDLE)
            d.fixed_update(FIXED_DT, IDLE)
        # Hits interrupt repair progress (no further restoration), then abort.
        self.assertGreater(d.repair_interruptions["f1"], base)
        self.assertEqual(d.subsystem_health("f1", Subsystem.WEAPONS), before)
        self.assertIsNone(e._ships["f1"].repair_target)

    def test_repair_interrupts_without_undoing_completed_restoration(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 40, 0), orientation=look_quat((0, -1, 0)))
        self.cripple(d, w, e, "f1")
        step(e, w, d, 120)  # 2 s of uninterrupted repair -> ~14 HP restored
        before = d.subsystem_health("f1", Subsystem.WEAPONS)
        self.assertGreater(before, 0.0)
        d.queue(DamageEvent("player", "f1", 1, None))
        step(e, w, d, 1)  # one hit interrupts
        self.assertEqual(d.subsystem_health("f1", Subsystem.WEAPONS), before)


class DestructionTests(unittest.TestCase):
    def test_destroyed_fighter_is_removed_and_inert(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 30, 0), orientation=look_quat((0, -1, 0)))
        d.queue(DamageEvent("player", "f1", FIGHTER_PROFILE.hull_max_hp, None))
        step(e, w, d, 3)
        self.assertEqual(d.snapshot("f1").state, ShipState.DESTROYED)
        self.assertFalse(e.is_alive("f1"))
        self.assertNotIn("f1", w.targets)
        fired_before = e._ships["f1"].fired
        step(e, w, d, 60)
        self.assertEqual(e._ships["f1"].fired, fired_before)  # no further action


class CapitalTests(unittest.TestCase):
    def test_capital_fights_back_with_turrets(self):
        d, w, e, _ = build()
        e.spawn_capital("capital", (0, 100, 0))
        step(e, w, d, 300)
        self.assertGreater(e._ships["capital"].fired, 0)
        self.assertLess(d.snapshot("player").hull_hp, FIGHTER_PROFILE.hull_max_hp)

    def test_capital_requires_prolonged_attack(self):
        d, w, e, _ = build()
        e.spawn_capital("capital", (0, 100, 0))  # registers the capital hitbox
        # Isolate hull-pool durability: 30 s of sustained cannon fire with no
        # retaliation. A fighter dies to one torpedo; the capital must survive.
        for _ in range(1800):
            w.fixed_update(FIXED_DT, PilotInput(fire=True))
            d.fixed_update(FIXED_DT, IDLE)
        capital = d.snapshot("capital")
        self.assertNotEqual(capital.state, ShipState.DESTROYED)
        self.assertGreater(capital.hull_hp, 0.0)
        self.assertLess(capital.hull_hp, CAPITAL_PROFILE.hull_max_hp)  # but damaged

    def test_capital_turret_impairment_scales_and_blocks(self):
        d, w, e, _ = build()
        e.spawn_capital("capital", (0, 100, 0))
        d.queue(DamageEvent("script", "capital", CAPITAL_PROFILE.subsystem_max_hp,
                            Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, IDLE)
        step(e, w, d, 300)
        self.assertEqual(e._ships["capital"].fired, 0)  # destroyed turrets cannot fire
        self.assertEqual(d.snapshot("player").hull_hp, FIGHTER_PROFILE.hull_max_hp)

    def test_capital_repairs_vulnerably_and_stops_shooting(self):
        d, w, e, _ = build()
        e.spawn_capital("capital", (0, 100, 0))
        d.queue(DamageEvent("script", "capital", CAPITAL_PROFILE.subsystem_max_hp,
                            Subsystem.WEAPONS))
        d.fixed_update(FIXED_DT, IDLE)
        step(e, w, d, 10)
        self.assertEqual(e._ships["capital"].repair_target, Subsystem.WEAPONS)
        fired_at_entry = e._ships["capital"].fired
        step(e, w, d, 120)
        # Vulnerable while repairing: it holds fire and the player is unharmed.
        self.assertEqual(e._ships["capital"].fired, fired_at_entry)
        self.assertEqual(d.snapshot("player").hull_hp, FIGHTER_PROFILE.hull_max_hp)
        early = d.subsystem_health("capital", Subsystem.WEAPONS)
        self.assertGreater(early, 0.0)
        self.assertLess(early, CAPITAL_PROFILE.repair_cap)  # timed, not instant
        step(e, w, d, 2000)
        self.assertAlmostEqual(d.subsystem_health("capital", Subsystem.WEAPONS),
                               CAPITAL_PROFILE.repair_cap, places=2)
        self.assertIsNone(e._ships["capital"].repair_target)


class DeterminismTests(unittest.TestCase):
    def test_encounter_is_frame_interval_independent(self):
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
                    tuple(e.snapshot("capital").position),
                    d.snapshot("player").hull_hp,
                    d.snapshot("f1").hull_hp,
                    e._ships["f1"].fired,
                    d.repair_interruptions["f1"])

        seqs = ([1 / 60] * 600, [1 / 30] * 300, [1 / 144] * 1440,
                [.005, .025, .01, .06] * 100)
        results = [run(s) for s in seqs]
        self.assertTrue(all(r == results[0] for r in results), results)


class ValidationTests(unittest.TestCase):
    def test_tuning_validation(self):
        base = dict(fighter_repair_below=0.25, capital_repair_below=0.4,
                    fighter_engagement_range=400.0, capital_engagement_range=900.0,
                    fire_cone_deg=18.0, steer_gain=3.0, steer_deadzone=0.02,
                    fighter_throttle_far=0.7, fighter_standoff=90.0,
                    fighter_standoff_close=40.0, hit_cooldown=3.0,
                    max_repair_interruptions=4)
        for kw in ({"fighter_repair_below": 0.0}, {"fighter_repair_below": 1.0},
                   {"capital_repair_below": -0.1}, {"fighter_engagement_range": 0},
                   {"fire_cone_deg": 0.0}, {"fire_cone_deg": 90.0},
                   {"max_repair_interruptions": 0}):
            with self.assertRaises(ValueError, msg=kw):
                EnemyTuning(**{**base, **kw})

    def test_spawn_and_update_validation(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 0, 0))
        with self.assertRaises(ValueError):
            e.spawn_fighter("f1", (0, 0, 0))
        e.spawn_capital("cap", (0, 100, 0))
        with self.assertRaises(ValueError):
            e.spawn_capital("cap2", (0, 100, 0))
        for dt in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                e.fixed_update(dt, IDLE)

    def test_ai_snapshot_reports_live_state(self):
        d, w, e, _ = build()
        e.spawn_fighter("f1", (0, 50, 0), orientation=look_quat((0, -1, 0)))
        e.spawn_capital("capital", (0, 100, 0))
        step(e, w, d, 5)
        rows = e.ai_snapshot()
        by_id = {r["entity_id"]: r for r in rows}
        self.assertEqual(set(by_id), {"f1", "capital"})
        self.assertEqual(by_id["capital"]["kind"], "capital")
        self.assertEqual(by_id["f1"]["kind"], "fighter")
        self.assertIn("repairing", by_id["f1"])
        self.assertIn("state", by_id["f1"])


if __name__ == "__main__":
    unittest.main()
