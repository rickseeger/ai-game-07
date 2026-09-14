"""Event-binding and effect-lifecycle tests (node 7, no window, no vision).

Pins the exact binding between real simulation events and effects:
- a player shot -> muzzle flash particle + the per-weapon firing sound,
  and the fire-event buffer is consumed (not leaked) after effects read it;
- a resolved hit -> impact spark particles + impact sound at the hit position;
- destruction -> a scale-appropriate explosion (more particles + capital cue
  for a capital) with the flash/fire/smoke layers;
- damage state -> persistent smoke (smoking) then smoke + fire + fire loop
  (burning), so degradation is visible before destruction;
- repair -> start/complete audio cues plus a green glow while repairing;
- boundedness (particle cap) and determinism (same seed -> same effect plan).

All assertions read the pure particle/audio state; no NodePath rendering and no
image perception is involved.
"""
import unittest

from panda3d.core import NodePath, Vec3

from breach.audio import AudioEngine
from breach.contracts import DamageEvent, PilotInput, RepairIntent, Subsystem, FIXED_DT
from breach.damage import CAPITAL_PROFILE, FIGHTER_PROFILE, DamageSystem, ShipState
from breach.effects import EffectsSystem
from breach.flight import FlightSystem
from breach.weapons import WeaponKind, WeaponsSystem, WEAPONS_WITH_TURRET

IDLE = PilotInput()


def build(player_pos=(0, -15, 4), max_particles=600, seed=14):
    flight = FlightSystem()
    damage = DamageSystem(player_entity_id="player")
    flight.damage = damage
    damage.spawn("player", FIGHTER_PROFILE)
    weapons = WeaponsSystem(player_entity_id="player", damage=damage,
                            pose_provider=flight.snapshot,
                            specs=WEAPONS_WITH_TURRET)
    audio = AudioEngine(muted=True)
    fx = EffectsSystem(NodePath("render"), NodePath("camera"), damage, weapons,
                       None, flight, audio=audio,
                       tracked_entities=["player"],
                       max_particles=max_particles, seed=seed)
    return flight, damage, weapons, audio, fx


def add_enemy(damage, weapons, fx, eid, pos, profile=FIGHTER_PROFILE):
    damage.spawn(eid, profile)
    weapons.set_faction(eid, "enemy")
    weapons.add_target(eid, "enemy", pos, (2.5, 2.0, 0.4))
    fx.tracked_entities.append(eid)


def step(flight, weapons, damage, fx, controls=IDLE, ticks=1):
    for _ in range(ticks):
        flight.fixed_update(FIXED_DT, controls)
        weapons.fixed_update(FIXED_DT, controls)
        damage.fixed_update(FIXED_DT, controls)
        fx.fixed_update(FIXED_DT, controls)


def kinds(fx):
    return [p.kind for p in fx.particles]


def audio_names(audio):
    return [e["name"] for e in audio.events]


class FiringFeedbackTests(unittest.TestCase):
    def test_player_shot_spawns_muzzle_flash_and_fire_sound(self):
        flight, damage, weapons, audio, fx = build()
        step(flight, weapons, damage, fx, PilotInput(fire=True), ticks=1)
        self.assertIn("muzzle", kinds(fx))
        self.assertIn("cannon", audio_names(audio))  # default weapon
        self.assertIn("cannon", [a["name"] for a in fx.last_audio])
        # fire event consumed, not leaked
        self.assertEqual(weapons.fired_this_tick, [])

    def test_each_weapon_has_its_own_sound(self):
        flight, damage, weapons, audio, fx = build()
        for kind, name in ((WeaponKind.SCATTER, "scatter"),
                           (WeaponKind.TORPEDO, "torpedo"),
                           (WeaponKind.CANNON, "cannon")):
            weapons.select_weapon("player", kind)
            audio.events.clear()
            step(flight, weapons, damage, fx, PilotInput(fire=True), ticks=1)
            self.assertIn(name, audio_names(audio), kind)


class ImpactTests(unittest.TestCase):
    def test_hit_spawns_sparks_and_impact_sound(self):
        flight, damage, weapons, audio, fx = build()
        add_enemy(damage, weapons, fx, "f1", (0, 30, 4))
        # Place the enemy directly ahead of the player's muzzle.
        for _ in range(30):
            weapons.fixed_update(FIXED_DT, PilotInput(fire=True))
            damage.fixed_update(FIXED_DT, IDLE)
            fx.fixed_update(FIXED_DT, IDLE)
        self.assertGreater(weapons.aim_snapshot().hits, 0)
        self.assertIn("spark", kinds(fx))
        self.assertIn("impact", audio_names(audio))


class DestructionTests(unittest.TestCase):
    def test_fighter_destruction_explodes(self):
        flight, damage, weapons, audio, fx = build()
        add_enemy(damage, weapons, fx, "f1", (0, 30, 4))
        damage.queue(DamageEvent("e", "f1", FIGHTER_PROFILE.hull_max_hp, None))
        damage.fixed_update(FIXED_DT, IDLE)
        self.assertEqual(damage.destroyed_this_tick, ["f1"])
        fx.fixed_update(FIXED_DT, IDLE)
        self.assertIn("flash", kinds(fx))
        self.assertIn("explosion", kinds(fx))
        self.assertIn("explosion_fighter", audio_names(audio))

    def test_capital_destruction_is_substantially_larger(self):
        flight, damage, weapons, audio, fx = build()
        add_enemy(damage, weapons, fx, "cap", (0, 100, 4), CAPITAL_PROFILE)
        damage.queue(DamageEvent("e", "cap", CAPITAL_PROFILE.hull_max_hp, None))
        damage.fixed_update(FIXED_DT, IDLE)
        fx.fixed_update(FIXED_DT, IDLE)
        self.assertIn("explosion_capital", audio_names(audio))
        cap_count = len(fx.particles)

        flight2, d2, w2, a2, fx2 = build()
        add_enemy(d2, w2, fx2, "f1", (0, 30, 4), FIGHTER_PROFILE)
        d2.queue(DamageEvent("e", "f1", FIGHTER_PROFILE.hull_max_hp, None))
        d2.fixed_update(FIXED_DT, IDLE)
        fx2.fixed_update(FIXED_DT, IDLE)
        self.assertGreater(cap_count, len(fx2.particles) * 2)


class DamageStateTests(unittest.TestCase):
    def test_smoking_emits_smoke_but_no_fire(self):
        flight, damage, weapons, audio, fx = build()
        add_enemy(damage, weapons, fx, "f1", (0, 30, 4))
        damage.queue(DamageEvent("e", "f1", 50.0, None))  # hull 0.5 -> smoking
        damage.fixed_update(FIXED_DT, IDLE)
        self.assertEqual(damage.snapshot("f1").state, ShipState.SMOKING)
        step(flight, weapons, damage, fx, IDLE, ticks=20)
        self.assertIn("smoke", kinds(fx))
        self.assertNotIn("fire", kinds(fx))
        self.assertFalse(fx.counts()["fire_loop"])

    def test_burning_emits_fire_and_fire_loop(self):
        flight, damage, weapons, audio, fx = build()
        add_enemy(damage, weapons, fx, "f1", (0, 30, 4))
        damage.queue(DamageEvent("e", "f1", 70.0, None))  # hull 0.3 -> burning
        damage.fixed_update(FIXED_DT, IDLE)
        self.assertEqual(damage.snapshot("f1").state, ShipState.BURNING)
        step(flight, weapons, damage, fx, IDLE, ticks=20)
        self.assertIn("fire", kinds(fx))
        self.assertIn("smoke", kinds(fx))
        self.assertTrue(fx.counts()["fire_loop"])
        self.assertIn("fire_loop", [a["name"] for a in audio.events if a["loop"]])

    def test_healthy_ship_emits_nothing(self):
        flight, damage, weapons, audio, fx = build()
        add_enemy(damage, weapons, fx, "f1", (0, 30, 4))
        step(flight, weapons, damage, fx, IDLE, ticks=30)
        self.assertEqual(fx.particles, [])
        self.assertEqual(audio.events, [])


class RepairCueTests(unittest.TestCase):
    def test_repair_start_and_complete_emit_cues_and_glow(self):
        flight, damage, weapons, audio, fx = build()
        damage.queue(DamageEvent("e", "player", 40.0, Subsystem.ENGINE))
        damage.fixed_update(FIXED_DT, IDLE)
        self.assertEqual(damage.subsystem_health("player", Subsystem.ENGINE), 0.0)
        repair = PilotInput(repair=True, repair_subsystem=Subsystem.ENGINE)
        step(flight, weapons, damage, fx, repair, ticks=2)
        self.assertIn("repair_start", audio_names(audio))
        # A few more ticks accumulate the green repair glow (0.07 s cadence).
        step(flight, weapons, damage, fx, repair, ticks=10)
        self.assertIn("glow", kinds(fx))
        # Complete the repair (4 s to cap); repair_complete must fire on exit.
        step(flight, weapons, damage, fx, repair, ticks=260)
        self.assertIn("repair_complete", audio_names(audio))

    def test_repair_does_not_cue_without_transition(self):
        flight, damage, weapons, audio, fx = build()
        step(flight, weapons, damage, fx, IDLE, ticks=10)
        self.assertNotIn("repair_start", audio_names(audio))
        self.assertNotIn("repair_complete", audio_names(audio))


class LifecycleTests(unittest.TestCase):
    def test_particles_age_out(self):
        flight, damage, weapons, audio, fx = build()
        add_enemy(damage, weapons, fx, "f1", (0, 30, 4))
        damage.queue(DamageEvent("e", "f1", FIGHTER_PROFILE.hull_max_hp, None))
        damage.fixed_update(FIXED_DT, IDLE)
        fx.fixed_update(FIXED_DT, IDLE)
        spawned = len(fx.particles)
        self.assertGreater(spawned, 0)
        step(flight, weapons, damage, fx, IDLE, ticks=300)  # 5 s: all expired
        self.assertEqual(fx.particles, [])

    def test_particle_cap_is_enforced(self):
        flight, damage, weapons, audio, fx = build(max_particles=10)
        add_enemy(damage, weapons, fx, "cap", (0, 100, 4), CAPITAL_PROFILE)
        damage.queue(DamageEvent("e", "cap", CAPITAL_PROFILE.hull_max_hp, None))
        damage.fixed_update(FIXED_DT, IDLE)
        fx.fixed_update(FIXED_DT, IDLE)
        self.assertLessEqual(len(fx.particles), 10)


class DeterminismTests(unittest.TestCase):
    def test_same_seed_and_script_yield_same_plan(self):
        def run():
            flight, damage, weapons, audio, fx = build(seed=14)
            add_enemy(damage, weapons, fx, "f1", (0, 30, 4))
            damage.queue(DamageEvent("e", "f1", 50.0, None))
            step(flight, weapons, damage, fx, PilotInput(fire=True), ticks=20)
            damage.queue(DamageEvent("e", "f1", 60.0, None))
            step(flight, weapons, damage, fx, PilotInput(fire=True), ticks=20)
            return (kinds(fx), audio_names(audio))
        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
