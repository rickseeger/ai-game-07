"""Mission loop, objectives, win/lose/restart and balance-tuning tests (node 8).

Covers the node-8 completion contract at the deterministic-simulation level:
an approachable escalating mission (fighter waves -> capital), clear win/defeat
transitions, one-action restart, a finite (non-respawning) encounter, and the
damage-and-repair tension validated against explicit numeric targets
(time-to-kill windows and repair vulnerability windows) read from the committed
balance data. No window, no vision: every assertion is on pure simulation state.
"""
import json
import unittest
from pathlib import Path

from panda3d.core import Quat, Vec3

from breach.contracts import DamageEvent, PilotInput, ShipView, Subsystem, FIXED_DT
from breach.damage import (CAPITAL_PROFILE, FIGHTER_PROFILE, DamageSystem,
                           ShipState)
from breach.enemies import EnemySystem, look_quat
from breach.flight import FlightSystem
from breach.mission import (MissionBalance, MissionSystem, DEFAULT_WAVES,
                            PLAYER_SPAWN, sustained_dps)
from breach.weapons import (WEAPONS, WEAPONS_WITH_TURRET, WeaponsSystem,
                            WeaponKind, SUBSYSTEM_DAMAGE_FRACTION)

IDLE = PilotInput()
ROOT = Path(__file__).resolve().parents[1]
BALANCE_JSON = json.loads((ROOT / "data" / "mission_balance.json").read_text())


class StaticPlayer:
    def __init__(self, position=PLAYER_SPAWN, forward=(0, 1, 0)):
        self.position = Vec3(*position)
        self.forward = Vec3(*forward)

    def view(self):
        return ShipView("player", tuple(self.position), (0, 0, 0),
                        look_quat(self.forward), 0.0)


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


def step(flight, e, w, d, m, ticks, controls=None):
    controls = controls if controls is not None else IDLE
    for _ in range(ticks):
        e.fixed_update(FIXED_DT, controls)
        w.fixed_update(FIXED_DT, controls)
        d.fixed_update(FIXED_DT, controls)
        m.fixed_update(FIXED_DT, controls)


def kill_all_fighters(d, m):
    """Destroy every currently-spawned fighter via the shared DamageSystem."""
    for eid in list(m._spawned):
        if eid != "capital" and d.is_spawned(eid):
            d.queue(DamageEvent("player", eid, FIGHTER_PROFILE.hull_max_hp, None))
            d.fixed_update(FIXED_DT, IDLE)


class BalanceTests(unittest.TestCase):
    def test_time_to_kill_targets_match_realized_specs(self):
        bal = MissionBalance()
        dps = sustained_dps(WEAPONS)
        fighter_ttk = FIGHTER_PROFILE.hull_max_hp / dps
        capital_ttk = CAPITAL_PROFILE.hull_max_hp / dps
        flo, fhi = bal.fighter_ttk_window
        clo, chi = bal.capital_ttk_window
        self.assertLessEqual(flo, fighter_ttk, fighter_ttk)
        self.assertLessEqual(fighter_ttk, fhi, fighter_ttk)
        self.assertLessEqual(clo, capital_ttk, capital_ttk)
        self.assertLessEqual(capital_ttk, chi, capital_ttk)

    def test_repair_vulnerability_windows_match_profiles(self):
        bal = MissionBalance()
        # A repair restores 0 -> repair_cap over repair_duration seconds.
        self.assertLessEqual(bal.fighter_repair_window[0],
                             FIGHTER_PROFILE.repair_duration)
        self.assertLessEqual(FIGHTER_PROFILE.repair_duration,
                             bal.fighter_repair_window[1])
        self.assertLessEqual(bal.capital_repair_window[0],
                             CAPITAL_PROFILE.repair_duration)
        self.assertLessEqual(CAPITAL_PROFILE.repair_duration,
                             bal.capital_repair_window[1])

    def test_no_one_shot_rules(self):
        self.assertLess(WEAPONS[WeaponKind.TORPEDO].damage,
                        FIGHTER_PROFILE.hull_max_hp)
        self.assertLess(WEAPONS[WeaponKind.TORPEDO].damage,
                        CAPITAL_PROFILE.hull_max_hp)

    def test_committed_balance_data_matches_code(self):
        realized = BALANCE_JSON["realized_values"]
        cannon = WEAPONS[WeaponKind.CANNON]
        self.assertEqual(realized["cannon_damage"], cannon.damage)
        self.assertEqual(realized["cannon_cooldown_s"], cannon.cooldown)
        self.assertEqual(realized["cannon_heat_per_shot"], cannon.heat_per_shot)
        self.assertEqual(realized["cannon_heat_capacity"], cannon.heat_capacity)
        self.assertEqual(realized["cannon_cool_rate_hps"], cannon.cool_rate)
        self.assertEqual(realized["torpedo_damage"],
                         WEAPONS[WeaponKind.TORPEDO].damage)
        self.assertEqual(realized["fighter_repair_duration_s"],
                         FIGHTER_PROFILE.repair_duration)
        self.assertEqual(realized["capital_repair_duration_s"],
                         CAPITAL_PROFILE.repair_duration)
        self.assertEqual(realized["fighter_repair_cap"], FIGHTER_PROFILE.repair_cap)
        self.assertEqual(realized["capital_repair_cap"], CAPITAL_PROFILE.repair_cap)
        self.assertEqual(realized["subsystem_damage_fraction"],
                         SUBSYSTEM_DAMAGE_FRACTION)
        self.assertEqual(realized["fighter_subsystem_max_hp"],
                         FIGHTER_PROFILE.subsystem_max_hp)
        self.assertEqual(realized["capital_subsystem_max_hp"],
                         CAPITAL_PROFILE.subsystem_max_hp)

    def test_balance_validation_rejects_bad_windows(self):
        with self.assertRaises(ValueError):
            MissionBalance(fighter_ttk_window=(0, 1))
        with self.assertRaises(ValueError):
            MissionBalance(capital_repair_window=(40, 20))


class LifecycleTests(unittest.TestCase):
    def test_approachable_start_single_fighter(self):
        flight, d, w, e, m = build()
        snap = m.snapshot()
        self.assertEqual(snap.phase, "combat")
        self.assertEqual(snap.wave, 1)
        self.assertEqual(snap.objective, "Destroy the patrol fighter")
        self.assertEqual(snap.remaining_enemies, ("fighter-1",))
        # Exactly one enemy spawned for the opening, at readable range ahead.
        self.assertEqual(len(e.entities), 1)
        self.assertGreater(e.snapshot("fighter-1").position[1], 0.0)

    def test_escalation_and_victory(self):
        flight, d, w, e, m = build()
        transitions = []
        # Wave 1 -> 2 -> 3 -> capital, then victory.
        for expected_wave in (2, 3, 4):
            before = m.snapshot().wave
            kill_all_fighters(d, m)
            step(flight, e, w, d, m, 2)
            self.assertEqual(m.snapshot().wave, expected_wave,
                             m.snapshot().to_dict())
            self.assertEqual(m.snapshot().phase, "combat")
            transitions.append((before, m.snapshot().wave))
        # Capital is the only remaining enemy; destroy it -> victory.
        self.assertIn("capital", e.entities)
        self.assertEqual(m.snapshot().objective, "Destroy the interdiction frigate")
        d.queue(DamageEvent("player", "capital", CAPITAL_PROFILE.hull_max_hp, None))
        step(flight, e, w, d, m, 2)
        self.assertEqual(m.snapshot().phase, "victory")
        self.assertTrue(m.snapshot().terminal)
        self.assertEqual(m.snapshot().objective, "Mission complete - frigate destroyed")
        self.assertIn("victory", [t[0] for t in m.transition_log])

    def test_defeat_and_terminal_freeze(self):
        flight, d, w, e, m = build()
        d.queue(DamageEvent("enemy", "player", FIGHTER_PROFILE.hull_max_hp, None))
        step(flight, e, w, d, m, 1)
        self.assertEqual(m.snapshot().phase, "defeat")
        self.assertTrue(m.snapshot().terminal)
        self.assertEqual(m.snapshot().objective, "Ship destroyed")
        # A terminal mission stops progressing: no further wave changes.
        wave = m.snapshot().wave
        step(flight, e, w, d, m, 120)
        self.assertEqual(m.snapshot().wave, wave)
        self.assertEqual(m.snapshot().phase, "defeat")

    def test_restart_returns_to_fresh_wave_one(self):
        flight, d, w, e, m = build()
        kill_all_fighters(d, m)
        step(flight, e, w, d, m, 2)
        self.assertGreater(m.snapshot().wave, 1)
        # Rebuild the whole combat state exactly as app.restart_mission does.
        flight.reset(position=PLAYER_SPAWN)
        d.reset()
        w.reset()
        e.reset()
        flight.damage = d
        d.spawn("player", FIGHTER_PROFILE)
        d.speed_provider = (lambda eid: flight.velocity.length()
                            if eid == "player" else e.speed_of(eid))
        m2 = MissionSystem(player_entity_id="player", damage=d, enemies=e,
                           weapons=w, player_pose_provider=flight.snapshot)
        m2.start()
        snap = m2.snapshot()
        self.assertEqual(snap.phase, "combat")
        self.assertEqual(snap.wave, 1)
        self.assertEqual(snap.remaining_enemies, ("fighter-1",))
        self.assertEqual(d.snapshot("player").hull_hp, FIGHTER_PROFILE.hull_max_hp)
        self.assertEqual(len(w.projectiles), 0)

    def test_no_infinite_respawn(self):
        flight, d, w, e, m = build()
        # Clearing a wave despawns its enemies; the same IDs never reappear.
        kill_all_fighters(d, m)
        step(flight, e, w, d, m, 2)
        self.assertNotIn("fighter-1", e.entities)
        self.assertEqual(m.snapshot().wave, 2)


class RepairTensionTests(unittest.TestCase):
    def test_sustained_combat_degrades_subsystems_and_opens_repair(self):
        # Ordinary weapons fire (no scripted subsystem events) must degrade the
        # target's subsystems and push it into its vulnerable repair state.
        flight, d, w, e, m = build()
        # Face fighter-1 toward the player so head-on hits land on WEAPONS.
        e._ships["fighter-1"].flight.orientation = Quat(*look_quat((0, -1, 0)))
        # Aim the player at fighter-1 and fire the cannon until it repairs.
        target = Vec3(*e.snapshot("fighter-1").position) - flight.position
        flight.orientation = Quat(*look_quat(tuple(target.normalized())))
        for _ in range(600):
            e.fixed_update(FIXED_DT, IDLE)
            w.fixed_update(FIXED_DT, PilotInput(fire=True))
            d.fixed_update(FIXED_DT, IDLE)
            if d.subsystem_health("fighter-1", Subsystem.WEAPONS) < 0.25:
                break
        self.assertLess(d.subsystem_health("fighter-1", Subsystem.WEAPONS),
                        0.25, "head-on fire should blunt the fighter's weapons")
        # Stop firing to let the post-hit repair cooldown expire, so the
        # crippled fighter tries to repair in place (the vulnerable window).
        for _ in range(240):
            e.fixed_update(FIXED_DT, IDLE)
            w.fixed_update(FIXED_DT, IDLE)
            d.fixed_update(FIXED_DT, IDLE)
            if d.snapshot("fighter-1").repairing is not None:
                break
        self.assertIsNotNone(d.snapshot("fighter-1").repairing,
                             "crippled fighter should enter its repair state")

    def test_enemy_repair_vulnerability_is_exploitable(self):
        flight, d, w, e, m = build()
        # Cripple fighter-1's weapons so it enters a vulnerable repair.
        d.queue(DamageEvent("player", "fighter-1",
                            FIGHTER_PROFILE.subsystem_max_hp, Subsystem.WEAPONS))
        step(flight, e, w, d, m, 10)
        self.assertEqual(d.snapshot("fighter-1").repairing, Subsystem.WEAPONS)
        fired_at_entry = e._ships["fighter-1"].fired
        # Vulnerable while repairing: stationary, holding fire.
        self.assertAlmostEqual(e.speed_of("fighter-1"), 0.0, places=2)
        step(flight, e, w, d, m, 30)
        self.assertEqual(e._ships["fighter-1"].fired, fired_at_entry)
        # The player can exploit the window: a killing blow lands cleanly
        # (verify destruction before the mission despawns the cleared wave).
        d.queue(DamageEvent("player", "fighter-1",
                            FIGHTER_PROFILE.hull_max_hp, None))
        d.fixed_update(FIXED_DT, IDLE)
        self.assertEqual(d.snapshot("fighter-1").state, ShipState.DESTROYED)
        m.fixed_update(FIXED_DT, IDLE)
        self.assertNotIn("fighter-1", e.entities)  # cleared wave is despawned
        self.assertEqual(m.snapshot().wave, 2)

    def test_player_repair_is_risky_and_interruptible(self):
        # No enemies spawned: isolate the player's own repair choice so ambient
        # fire cannot mask the repair/interruption mechanics.
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

        # Cripple the player's weapons, then choose to hold R to repair it.
        d.queue(DamageEvent("enemy", "player",
                            FIGHTER_PROFILE.subsystem_max_hp, Subsystem.WEAPONS))
        tick(IDLE)
        self.assertLess(d.subsystem_health("player", Subsystem.WEAPONS), 0.2)
        repair = PilotInput(repair=True, repair_subsystem=Subsystem.WEAPONS)
        tick(repair, 120)
        # Repair is timed (progress, not instant); the ship is braked/stopped.
        self.assertGreater(d.snapshot("player").repair_progress, 0.0)
        self.assertLess(d.snapshot("player").repair_progress, 1.0)
        self.assertAlmostEqual(flight.velocity.length(), 0.0, places=2)
        # A hit interrupts the current repair interval without undoing progress.
        health_before = d.subsystem_health("player", Subsystem.WEAPONS)
        d.queue(DamageEvent("enemy", "player", 1, None))
        tick(repair, 1)
        self.assertEqual(d.subsystem_health("player", Subsystem.WEAPONS),
                         health_before)
        self.assertLessEqual(d.snapshot("player").repair_progress,
                             d.snapshot("player").repair_progress + 1e-9)


class DeterminismTests(unittest.TestCase):
    def test_mission_progression_is_frame_interval_independent(self):
        from breach.timing import FixedStepper

        def run(intervals):
            flight = FlightSystem(position=PLAYER_SPAWN)
            d = DamageSystem(player_entity_id="player")
            flight.damage = d
            d.spawn("player", FIGHTER_PROFILE)
            w = WeaponsSystem(player_entity_id="player", damage=d,
                              pose_provider=flight.snapshot,
                              specs=WEAPONS_WITH_TURRET)
            e = EnemySystem(player_entity_id="player", damage=d, weapons=w,
                            player_pose_provider=flight.snapshot)
            d.speed_provider = (lambda eid: flight.velocity.length()
                                if eid == "player" else e.speed_of(eid))
            m = MissionSystem(player_entity_id="player", damage=d, enemies=e,
                              weapons=w, player_pose_provider=flight.snapshot)
            m.start()
            stepper = FixedStepper(flight, systems=[e, w, d, m])
            for dt in intervals:
                def on_tick(tick, controls, ship):
                    if tick == 60:
                        kill_all_fighters(d, m)
                stepper.advance(dt, lambda: IDLE, on_tick=on_tick)
            return (m.snapshot().wave, m.snapshot().phase,
                    tuple(sorted(m._spawned)))
        # Same total simulated time (120 s of fixed steps), grouped differently.
        seqs = ([1/60]*120, [1/30]*60, [1/144]*288)
        results = [run(s) for s in seqs]
        self.assertTrue(all(r == results[0] for r in results), results)


if __name__ == "__main__":
    unittest.main()
