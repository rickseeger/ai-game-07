"""Window-independent input state. All hardware bindings live in this table."""
from dataclasses import dataclass
from math import isfinite
from breach.contracts import PilotInput, Subsystem

BINDINGS = {
    "w": "throttle_up", "s": "throttle_down",
    "arrow_left": "yaw_left", "arrow_right": "yaw_right",
    "arrow_up": "pitch_up", "arrow_down": "pitch_down",
    "a": "roll_left", "d": "roll_right", "b": "brake",
    "home": "level", "c": "center", "escape": "pause",
    "mouse1": "fire", "space": "fire", "tab": "target_next",
    "r": "repair", "1": "engine", "2": "weapons", "3": "sensors",
    "q": "cycle_weapon",
    "f10": "quit",
}
# The HUD and README use these exact strings (no staging bindings remain).
CONTROL_LINES = (
    "W/S raise/lower throttle | B brake and zero throttle",
    "A/D roll left/right | Arrows: nose up/down/left/right",
    "Mouse: aim stick (up = nose up) | C: center aim",
    "Hold Home: level horizon | Esc: pause/resume | F10: quit",
    "R: hold to repair selected subsystem (brakes + locks)",
    "1/2/3 engine/weapons/sensors | Space/LMB fire | Q cycle weapon",
    "Tab: target lock | reticle lights when the locked target is in range",
)


@dataclass(frozen=True)
class InputSettings:
    sensitivity: float = 0.012
    deadzone: float = 0.12
    invert_y: bool = False
    def __post_init__(self):
        if not isfinite(self.sensitivity) or self.sensitivity <= 0:
            raise ValueError("sensitivity must be finite and positive")
        if not 0 <= self.deadzone < 1:
            raise ValueError("deadzone must be in [0, 1)")

def clamp(value):
    return max(-1.0, min(1.0, value))

class InputAdapter:
    def __init__(self, bindings=None, settings=None):
        self.bindings = dict(BINDINGS if bindings is None else bindings)
        self.settings = settings or InputSettings()
        self.held = set()
        self.edges = set()
        self.aim = [0.0, 0.0]
        self.paused = False
        self.focused = True
        self.selected = Subsystem.ENGINE

    def clear(self):
        self.held.clear()
        self.edges.clear()
        self.aim[:] = [0.0, 0.0]

    def set_paused(self, value):
        self.paused = bool(value)
        self.clear()

    def set_focus(self, value):
        self.focused = bool(value)
        if not value:
            self.set_paused(True)
        # Focus return never resumes automatically.

    def key(self, key, down):
        action = self.bindings.get(key)
        if not action:
            return
        if not down:
            self.held.discard(key)
            return
        if self.paused or not self.focused or key in self.held:
            return
        self.held.add(key)
        if action in ("target_next", "cycle_weapon"):
            self.edges.add(action)
        elif action in ("engine", "weapons", "sensors"):
            self.selected = Subsystem(action)
        elif action in ("center", "level"):
            self.aim[:] = [0.0, 0.0]

    def mouse_delta(self, dx, dy):
        if self.paused or not self.focused:
            return
        if not isfinite(dx) or not isfinite(dy):
            raise ValueError("mouse displacement must be finite")
        self.aim[0] = clamp(self.aim[0] + dx * self.settings.sensitivity)
        self.aim[1] = clamp(self.aim[1] - dy * self.settings.sensitivity *
                            (-1 if self.settings.invert_y else 1))

    def sample(self):
        if self.paused or not self.focused:
            return PilotInput(repair_subsystem=self.selected)
        actions = {self.bindings[k] for k in self.held}
        def axis(pos, neg):
            return float((pos in actions) - (neg in actions))
        def soft(value):
            d = self.settings.deadzone
            return (1 if value >= 0 else -1) * max(0, abs(value)-d) / (1-d)
        if "center" in actions or "level" in actions:
            self.aim[:] = [0.0, 0.0]
        result = PilotInput(
            yaw=clamp(axis("yaw_right", "yaw_left") + soft(self.aim[0])),
            pitch=clamp(axis("pitch_up", "pitch_down") + soft(self.aim[1])),
            roll=axis("roll_right", "roll_left"),
            throttle=axis("throttle_up", "throttle_down"),
            brake="brake" in actions, level="level" in actions,
            fire="fire" in actions, repair="repair" in actions,
            target_next="target_next" in self.edges,
            cycle_weapon="cycle_weapon" in self.edges, repair_subsystem=self.selected)
        self.edges.clear()
        return result


def control_lines(settings, keyboard_only=False):
    lines = list(CONTROL_LINES)
    if keyboard_only:
        lines[2] = "Mouse aim disabled (--keyboard-only); use arrow keys"
    elif settings.invert_y:
        lines[2] = lines[2].replace("up = nose up", "up = nose down; inverted")
    return tuple(lines)
