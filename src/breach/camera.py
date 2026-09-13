"""Read-only cockpit pose presentation; testable without a window."""
from panda3d.core import Quat
from breach.flight import interpolate

EYE_OFFSET = (0.0, 0.0, 0.65)
# Explicit local occupied hull envelope; the eye is inside, never a chase camera.
HULL_MIN = (-1.2, -2.0, -0.5)
HULL_MAX = (1.2, 2.0, 1.4)

class FlightCamera:
    def __init__(self, render, camera):
        self.ship_root = render.attachNewNode("player-presentation")
        self.camera = camera
        camera.reparentTo(self.ship_root)
        camera.setPos(*EYE_OFFSET)
        camera.setQuat(Quat.identQuat())

    def present(self, previous, current, alpha):
        pose = interpolate(previous, current, alpha)
        self.ship_root.setPos(*pose.position)
        self.ship_root.setQuat(Quat(*pose.orientation))
        return pose

    def add_canopy(self):
        # Minimal flight reference frame only; future CockpitSystem owns full HUD.
        from breach.scene import box
        self.canopy = self.camera.attachNewNode("flight-canopy")
        for name, size, pos in (
            ("left-sill", (.07, 1.8, .07), (-.85, .8, -.48)),
            ("right-sill", (.07, 1.8, .07), (.85, .8, -.48)),
            ("dashboard", (1.8, .15, .08), (0, 1.3, -.50)),
        ):
            mesh = box(name, size, (.12, .32, .34, 1))
            mesh.reparentTo(self.canopy)
            mesh.setPos(*pos)
            mesh.setLightOff()
