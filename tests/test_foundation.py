import unittest
from breach.contracts import DamageEvent, PilotInput, Subsystem, FIXED_DT, MAX_FRAME_DT, MAX_STEPS
from breach.scene import box, build_scene
from panda3d.core import NodePath

class FoundationTests(unittest.TestCase):
    def test_neutral_input(self):
        self.assertEqual(PilotInput().throttle, 0)
        self.assertFalse(PilotInput().fire)
    def test_input_rejects_invalid_axis(self):
        for v in (-2, 2, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                PilotInput(yaw=v)
    def test_damage_validation(self):
        self.assertEqual(DamageEvent("a", "b", 12, Subsystem.ENGINE).amount, 12)
        for v in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                DamageEvent("a", "b", v)
    def test_fixed_step_budget(self):
        self.assertAlmostEqual(FIXED_DT * MAX_STEPS, MAX_FRAME_DT)
    def test_mesh_is_3d_triangles(self):
        mesh = box("test", (2, 4, 6), (1, 1, 1, 1))
        geom = mesh.node().getGeom(0)
        self.assertEqual(geom.getVertexData().getNumRows(), 24)
        self.assertEqual(geom.getPrimitive(0).getNumPrimitives(), 12)
        low, high = mesh.getTightBounds()
        self.assertEqual(tuple(high-low), (2, 4, 6))
    def test_scene_contains_depth_and_capital(self):
        root = build_scene(NodePath("test-world"))
        self.assertFalse(root.find("**/capital-hull").isEmpty())
        self.assertEqual(root.findAllMatches("**/fighter-placeholder").getNumPaths(), 3)
        low, high = root.getTightBounds()
        self.assertGreater(high.y-low.y, 100)
if __name__ == "__main__":
    unittest.main()
