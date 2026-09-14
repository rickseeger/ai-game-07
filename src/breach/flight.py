"""Fixed-step assisted flight; no NodePaths, window, or combat implementation."""
from dataclasses import dataclass
import math
from typing import Protocol
from panda3d.core import Quat, Vec3
from breach.contracts import ShipView

@dataclass(frozen=True)
class FlightPerformance:
    thrust_multiplier: float = 1.0
    turning_multiplier: float = 1.0
    repair_locked: bool = False
    def __post_init__(self):
        for v in (self.thrust_multiplier, self.turning_multiplier):
            if not 0 <= v <= 1:
                raise ValueError("flight multipliers must be in [0, 1]")

class FlightDamageService(Protocol):
    def flight_performance(self, entity_id: str) -> FlightPerformance: ...

class NeutralDamage:
    """Node 3 adapter may replace this; no invented health/repair simulation."""
    def flight_performance(self, entity_id):
        return FlightPerformance()

@dataclass(frozen=True)
class FlightTuning:
    max_speed: float = 45.0
    acceleration: float = 24.0
    brake_acceleration: float = 60.0
    throttle_rate: float = 0.6
    yaw_rate: float = 65.0
    pitch_rate: float = 55.0
    roll_rate: float = 90.0
    level_rate: float = 100.0
    idle_drag: float = 0.35


def quaternion(values):
    return Quat(*values)


def interpolate(a: ShipView, b: ShipView, alpha: float) -> ShipView:
    """Shortest-arc normalized interpolation, including q/-q equivalence."""
    alpha = max(0.0, min(1.0, alpha))
    q0, q1 = a.orientation, b.orientation
    if sum(x*y for x, y in zip(q0, q1)) < 0:
        q1 = tuple(-x for x in q1)
    q = [x+(y-x)*alpha for x, y in zip(q0, q1)]
    norm = math.sqrt(sum(x*x for x in q))
    blend = lambda x, y: tuple(v+(w-v)*alpha for v, w in zip(x, y))
    return ShipView(b.entity_id, blend(a.position, b.position),
                    blend(a.velocity, b.velocity), tuple(x/norm for x in q),
                    a.throttle+(b.throttle-a.throttle)*alpha)

class FlightSystem:
    def __init__(self, damage: FlightDamageService | None = None, tuning=None,
                 entity_id="player", position=(0, -15, 4), orientation=(1, 0, 0, 0)):
        self.damage = damage if damage is not None else NeutralDamage()
        self.tuning = tuning or FlightTuning()
        self.entity_id = entity_id
        self.position = Vec3(*position)
        self.velocity = Vec3(0)
        self.orientation = Quat(*orientation)
        self.orientation.normalize()
        self.throttle = 0.0

    def reset(self, position=None, orientation=(1, 0, 0, 0)):
        """Return the player to a pristine spawn state for a mission restart."""
        if position is not None:
            self.position = Vec3(*position)
        self.velocity = Vec3(0)
        self.orientation = Quat(*orientation)
        self.orientation.normalize()
        self.throttle = 0.0

    def snapshot(self):
        return ShipView(self.entity_id, tuple(self.position), tuple(self.velocity),
                        tuple(self.orientation), self.throttle)

    def fixed_update(self, dt, controls):
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("dt must be finite and positive")
        perf = self.damage.flight_performance(self.entity_id)
        t = self.tuning
        locked = controls.repair or perf.repair_locked
        if locked or controls.brake:
            self.throttle = 0.0
        else:
            self.throttle = max(0, min(1, self.throttle + controls.throttle*t.throttle_rate*dt))
        if not locked:
            if controls.level:
                # Preserve current compass heading, recover pitch and roll together.
                f = self.orientation.xform(Vec3(0, 1, 0))
                goal = Quat()
                goal.setHpr((math.degrees(math.atan2(-f.x, f.y)), 0, 0))
                now = tuple(self.orientation)
                dest = tuple(goal)
                dot = sum(x*y for x, y in zip(now, dest))
                if dot < 0:
                    dest = tuple(-x for x in dest)
                angle = 2*math.acos(min(1, abs(dot)))
                if angle > 1e-6:
                    fraction = min(1, math.radians(t.level_rate)*perf.turning_multiplier*dt/angle)
                    # Slerp at a bounded angular rate.
                    weights = (math.sin((1-fraction)*angle/2)/math.sin(angle/2),
                               math.sin(fraction*angle/2)/math.sin(angle/2))
                    self.orientation = Quat(*(x*weights[0]+y*weights[1] for x,y in zip(now,dest)))
            else:
                # Body rotation vector: +pitch about X, +roll about Y, -yaw about Z.
                rotation = Vec3(controls.pitch*t.pitch_rate, controls.roll*t.roll_rate,
                                -controls.yaw*t.yaw_rate)
                speed = rotation.length()
                if speed:
                    delta = Quat()
                    delta.setFromAxisAngle(speed*perf.turning_multiplier*dt, rotation/speed)
                    self.orientation = delta * self.orientation
            self.orientation.normalize()
        if controls.brake or locked:
            speed = self.velocity.length()
            if speed:
                self.velocity *= max(0, 1-t.brake_acceleration*dt/speed)
        elif self.throttle > 0:
            target = self.orientation.xform(Vec3(0, 1, 0))*self.throttle*t.max_speed*perf.thrust_multiplier
            change = target - self.velocity
            length = change.length()
            if length:
                self.velocity += change * min(1, t.acceleration*perf.thrust_multiplier*dt/length)
        else:
            self.velocity *= math.exp(-t.idle_drag*dt)
        self.position += self.velocity*dt
