"""Cockpit presentation transforms and HUD state binding (node 6).

No window, no vision. These tests pin the world-to-radar and world-to-screen
(targeting) math against known body-frame geometry and against Panda's own
Lens.project, and pin the HUD state/text binding against the real
DamageSystem/WeaponsSystem/FlightSystem snapshots (no stub HUD values).
"""
import math
import unittest

from panda3d.core import Point2, Point3, PerspectiveLens, Quat, Vec3

from breach.cockpit import (
    RADAR_BASE_RANGE, SUBSYSTEM_ORDER,
    aspect2d_to_pixel, build_hud_state, build_radar, hud_lines,
    ndc_to_aspect2d, project_camera_point, radar_blip, radar_blip_pixel,
    target_aspect2d, world_to_body, world_to_camera,
)
from breach.contracts import DamageEvent, PilotInput, Subsystem, FIXED_DT
from breach.damage import DamageSystem, FIGHTER_PROFILE
from breach.flight import FlightSystem
from breach.weapons import WeaponsSystem, WEAPONS

ID = (1.0, 0.0, 0.0, 0.0)  # identity quaternion (wxyz)


def qyaw(deg):
    q = Quat()
    q.setHpr((deg, 0, 0))
    return tuple(q)


class WorldToBodyTests(unittest.TestCase):
    def test_ahead_right_up_and_rotated(self):
        # Player at origin, identity orientation: forward +Y, right +X, up +Z.
        self.assertAlmostEqual(world_to_body((0, 10, 0), (0, 0, 0), ID).x, 0.0)
        self.assertAlmostEqual(world_to_body((0, 10, 0), (0, 0, 0), ID).y, 10.0)
        body = world_to_body((5, 0, 0), (0, 0, 0), ID)
        self.assertAlmostEqual(body.x, 5.0)
        self.assertAlmostEqual(body.y, 0.0)
        up = world_to_body((0, 0, 7), (0, 0, 0), ID)
        self.assertAlmostEqual(up.z, 7.0)

    def test_translation_and_yawed_player(self):
        # Player yawed 90 degrees right: its forward is world -X.
        body = world_to_body((-10, 0, 0), (0, 0, 0), qyaw(90))
        # A point at world -X is directly ahead (body +Y).
        self.assertGreater(body.y, 9.0)
        self.assertAlmostEqual(body.x, 0.0, places=4)
        self.assertAlmostEqual(body.z, 0.0, places=4)

    def test_world_to_camera_equals_body_for_aligned_camera(self):
        cam = world_to_camera((1, 2, 3), (0, 0, 0), ID)
        self.assertEqual(tuple(round(v, 6) for v in cam), (1.0, 2.0, 3.0))


class RadarBlipTests(unittest.TestCase):
    def test_directly_ahead(self):
        b = radar_blip(Vec3(0, 100, 0), RADAR_BASE_RANGE, "e", "fighter", "healthy")
        self.assertAlmostEqual(b.azimuth_deg, 0.0)
        self.assertAlmostEqual(b.elevation_deg, 0.0)
        self.assertAlmostEqual(b.range, 100.0)
        self.assertAlmostEqual(b.scope_x, 0.0)
        self.assertGreater(b.scope_y, 0.0)
        self.assertAlmostEqual(b.scope_y, 0.1)  # 100/1000
        self.assertAlmostEqual(b.vertical, 0.0)

    def test_right_left_behind(self):
        right = radar_blip(Vec3(50, 0, 0), RADAR_BASE_RANGE, "r")
        self.assertAlmostEqual(right.azimuth_deg, 90.0)
        self.assertGreater(right.scope_x, 0.0)
        left = radar_blip(Vec3(-50, 0, 0), RADAR_BASE_RANGE, "l")
        self.assertAlmostEqual(left.azimuth_deg, -90.0)
        behind = radar_blip(Vec3(0, -100, 0), RADAR_BASE_RANGE, "b")
        self.assertAlmostEqual(abs(behind.azimuth_deg), 180.0)
        self.assertLess(behind.scope_y, 0.0)

    def test_above_and_below(self):
        above = radar_blip(Vec3(0, 0, 100), RADAR_BASE_RANGE, "a")
        self.assertAlmostEqual(above.elevation_deg, 90.0)
        self.assertAlmostEqual(above.vertical, 1.0)
        self.assertAlmostEqual(above.scope_x, 0.0)
        self.assertAlmostEqual(above.scope_y, 0.0)  # directly above -> centre
        below = radar_blip(Vec3(0, 0, -100), RADAR_BASE_RANGE, "d")
        self.assertAlmostEqual(below.vertical, -1.0)

    def test_range_clamps_and_in_range(self):
        far = radar_blip(Vec3(0, 5000, 0), RADAR_BASE_RANGE, "f")
        self.assertFalse(far.in_range)
        self.assertAlmostEqual(far.scope_y, 1.0)  # clamped to scope edge
        near = radar_blip(Vec3(0, 1000, 0), RADAR_BASE_RANGE, "n")
        self.assertTrue(near.in_range)
        self.assertAlmostEqual(near.scope_y, 1.0)

    def test_sensors_reach_scales(self):
        positions = {"near": (0, 100, 0), "far": (0, 800, 0)}
        states = {e: {"kind": "fighter", "state": "healthy"} for e in positions}
        full = build_radar(positions, states, (0, 0, 0), ID, sensors_multiplier=1.0)
        self.assertEqual({b.entity_id for b in full if b.in_range}, {"near", "far"})
        # 50% sensors: 500 m reach -> the 800 m enemy drops out.
        half = build_radar(positions, states, (0, 0, 0), ID, sensors_multiplier=0.5)
        self.assertEqual({b.entity_id for b in half if b.in_range}, {"near"})

    def test_build_radar_sorts_nearest_first(self):
        positions = {"a": (0, 300, 0), "b": (0, 100, 0), "c": (0, 200, 0)}
        states = {e: {"kind": "fighter", "state": "healthy"} for e in positions}
        blips = build_radar(positions, states, (0, 0, 0), ID, sensors_multiplier=1.0)
        self.assertEqual([b.entity_id for b in blips], ["b", "c", "a"])


class ProjectionTests(unittest.TestCase):
    LENS_FOV = (78.0, 78.0)

    def test_project_ahead_center(self):
        self.assertEqual(project_camera_point(Vec3(0, 10, 0), 78.0, 48.978862), (0.0, 0.0))

    def test_project_right_and_up_signs(self):
        x, y = project_camera_point(Vec3(5, 10, 0), 78.0, 48.978862)
        self.assertGreater(x, 0.0)
        self.assertAlmostEqual(y, 0.0, places=9)
        x, y = project_camera_point(Vec3(0, 10, 3), 78.0, 48.978862)
        self.assertGreater(y, 0.0)

    def test_project_behind_is_none(self):
        self.assertIsNone(project_camera_point(Vec3(0, -10, 0), 78.0, 48.978862))
        self.assertIsNone(project_camera_point(Vec3(0, 0.05, 0), 78.0, 48.978862))

    def test_matches_panda_lens_project(self):
        lens = PerspectiveLens()
        lens.setFov(78, 78)
        lens.setAspectRatio(960 / 540)
        hfov, vfov = lens.getFov()
        for p in [Vec3(0, 10, 0), Vec3(5, 10, 0), Vec3(-3, 12, 2),
                  Vec3(0, 8, -2.5), Vec3(2.1, 9.0, 1.1)]:
            mine = project_camera_point(p, hfov, vfov)
            out = Point2()
            ok = lens.project(Point3(*p), out)
            self.assertTrue(ok, p)
            self.assertAlmostEqual(mine[0], out.x, places=6, msg=p)
            self.assertAlmostEqual(mine[1], out.y, places=6, msg=p)

    def test_target_aspect2d_mapping(self):
        # A point dead ahead maps to aspect2d (0, 0) = screen centre.
        self.assertEqual(target_aspect2d((0, 10, 0), (0, 0, 0), ID, 78.0, 48.978862, 960/540), (0.0, 0.0))
        # Off-camera (behind) -> None.
        self.assertIsNone(target_aspect2d((0, -10, 0), (0, 0, 0), ID, 78.0, 48.978862, 960/540))

    def test_pixel_mapping_known_points(self):
        # aspect2d centre -> screen centre.
        self.assertEqual(aspect2d_to_pixel(0.0, 0.0), (480, 270))
        # top-right corner of aspect2d (aspect, 1) -> top-right pixel (959, 0).
        self.assertEqual(aspect2d_to_pixel(960/540, 1.0), (960, 0))
        # radar centre is stable.
        self.assertEqual(radar_blip_pixel(0.0, 0.0), (113, 410))


class HudBindingTests(unittest.TestCase):
    def build(self):
        dmg = DamageSystem(player_entity_id="player")
        dmg.spawn("player", FIGHTER_PROFILE)
        flight = FlightSystem(damage=dmg)
        weapons = WeaponsSystem(player_entity_id="player", damage=dmg,
                                pose_provider=flight.snapshot)
        return dmg, flight, weapons

    def test_healthy_player_has_no_alert(self):
        dmg, flight, weapons = self.build()
        hud = build_hud_state(dmg.snapshot("player"), weapons.aim_snapshot(),
                              flight.snapshot())
        self.assertEqual(hud.hull_pct, 100)
        self.assertEqual(hud.state, "healthy")
        self.assertFalse(hud.red_alert)
        self.assertEqual(hud.damaged_subsystems, ())

    def test_hull_damage_flips_alert_and_state(self):
        dmg, flight, weapons = self.build()
        dmg.queue(DamageEvent("src", "player", 40.0))  # hull 100 -> 60 (smoking)
        dmg.fixed_update(FIXED_DT, PilotInput())
        hud = build_hud_state(dmg.snapshot("player"), weapons.aim_snapshot(),
                              flight.snapshot())
        self.assertEqual(hud.hull_pct, 60)
        self.assertEqual(hud.state, "smoking")
        self.assertTrue(hud.red_alert)

    def test_subsystem_damage_reported_and_actionable(self):
        dmg, flight, weapons = self.build()
        dmg.queue(DamageEvent("src", "player", 24.0, Subsystem.ENGINE))
        dmg.fixed_update(FIXED_DT, PilotInput())
        hud = build_hud_state(dmg.snapshot("player"), weapons.aim_snapshot(),
                              flight.snapshot())
        self.assertAlmostEqual(hud.subsystems[Subsystem.ENGINE], 0.4)
        self.assertTrue(hud.red_alert)
        self.assertIn(Subsystem.ENGINE, hud.damaged_subsystems)
        lines = hud_lines(hud)
        text = "\n".join(sum(lines, []))
        self.assertIn("ENGINE", text)
        self.assertIn("40%", text)

    def test_weapon_heat_and_target_binding(self):
        dmg, flight, weapons = self.build()
        dmg.spawn("fighter-1", FIGHTER_PROFILE)
        weapons.set_faction("fighter-1", "enemy")
        weapons.add_target("fighter-1", "enemy", (0, 50, 0), (2.5, 2.0, 0.4))
        # damage the target and lock it
        dmg.queue(DamageEvent("src", "fighter-1", 70.0))
        dmg.queue(DamageEvent("src", "fighter-1", 20.0, Subsystem.WEAPONS))
        dmg.fixed_update(FIXED_DT, PilotInput())
        weapons.fixed_update(FIXED_DT, PilotInput(target_next=True))
        aim = weapons.aim_snapshot()
        self.assertEqual(aim.locked_target, "fighter-1")
        hud = build_hud_state(dmg.snapshot("player"), aim, flight.snapshot(),
                              target_health=dmg.snapshot("fighter-1"),
                              target_kind="fighter", target_range=50.0)
        self.assertEqual(hud.locked_target, "fighter-1")
        self.assertAlmostEqual(hud.target_hull, 0.30)
        self.assertEqual(hud.target_state, "burning")
        self.assertAlmostEqual(hud.target_subsystems[Subsystem.WEAPONS], 0.5)
        self.assertAlmostEqual(hud.target_range, 50.0)
        text = "\n".join(sum(hud_lines(hud), []))
        self.assertIn("fighter-1", text)
        self.assertIn("30%", text)
        self.assertIn("50", text)

    def test_hud_lines_no_target_has_empty_right(self):
        dmg, flight, weapons = self.build()
        hud = build_hud_state(dmg.snapshot("player"), weapons.aim_snapshot(),
                              flight.snapshot())
        left, alert, right = hud_lines(hud)
        self.assertEqual(right, [])
        self.assertEqual(alert, [])


if __name__ == "__main__":
    unittest.main()
