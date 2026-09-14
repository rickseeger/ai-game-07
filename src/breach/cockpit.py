"""First-person cockpit presentation: cockpit frame, HUD and spatial radar.

Node 6 of tree G14. This module owns the *presentation* of the player's ship as
seen from inside it. It is read-only with respect to simulation state: it reads
the validated snapshots (HealthView, AimState, ShipView, enemy ShipViews) and
never writes flight/damage/weapons state.

Three layers, deliberately separated so the math is testable without a window:

1. Pure transforms (no window, no NodePath side effects beyond constructing
   vectors/quaternions):
   - world_to_body / world_to_camera: world point -> ship/camera local frame
     (forward +Y, right +X, up +Z, matching INTEGRATION.md).
   - radar_blip / build_radar: body frame -> spherical radar coordinates
     (azimuth, elevation, range, plus scope position and an above/below
     indicator). This is the "world-to-radar" transform.
   - project_camera_point / ndc_to_aspect2d / target_aspect2d: camera-space
     point -> normalized device coordinates -> aspect2d. This is the
     "targeting" transform (where the locked target's bracket lands).

2. build_hud_state / hud_lines: pure HUD *state binding*. They turn the raw
   snapshots into the exact values and text the HUD displays, so tests can
   assert "damaging the ENGINE changes the ENGINE line and flips the alert"
   without any rendering.

3. CockpitSystem: the presentation owner. It builds a camera-attached cockpit
   frame + instrument gauges (3D, visible first-person), the aspect2d HUD
   (hull/subsystem/heat/weapon/target panels + bars + red alert border), the
   radar scope with enemy blips and elevation stems, and the targeting bracket.
   It renders with exact flat colors so automated pixel sampling can verify it.

The radar is a polar "spherical" scope: forward is up on the disk, right is
right, range is radial distance from the centre, and each blip carries a
vertical stem (green up / magenta down) whose length encodes above/below
elevation. Enemy direction + distance + above/below are all therefore
communicated on one legible display.
"""
from dataclasses import dataclass, field
import math
from typing import Mapping

from panda3d.core import Quat, Vec3, CardMaker

from breach.contracts import Subsystem

# -- radar reach -------------------------------------------------------------
# Base reach in metres at full SENSORS capability. The live reach scales with
# the player's sensors_multiplier (a damaged sensors subsystem shortens it).
RADAR_BASE_RANGE = 1000.0
# Scope layout (aspect2d coords). Shared with tests/tools for pixel checks.
RADAR_CENTER = (-1.36, -0.52)
RADAR_RADIUS = 0.40

# -- exact flat colours (rendered unlit; pixel-sampling asserts these) --------
COCKPIT_COLOR = (0.30, 0.33, 0.40, 1.0)   # steel cockpit frame
GAUGE_OK = (0.15, 0.85, 0.40, 1.0)        # healthy instrument
GAUGE_WARN = (1.0, 0.70, 0.10, 1.0)       # impaired instrument
GAUGE_CRIT = (1.0, 0.10, 0.10, 1.0)       # failed instrument
RADAR_BG = (0.02, 0.06, 0.10, 0.78)       # translucent scope disk
RADAR_RIM = (0.25, 0.60, 0.70, 1.0)       # scope rim
RADAR_BLIP = (1.0, 0.62, 0.16, 1.0)       # enemy blip (amber)
RADAR_ABOVE = (0.30, 0.95, 0.55, 1.0)     # above stem (green)
RADAR_BELOW = (0.90, 0.42, 1.0, 1.0)      # below stem (magenta)
RADAR_FORWARD = (0.60, 0.80, 0.95, 1.0)   # forward tick
ALERT_RED = (1.0, 0.06, 0.06, 1.0)        # red damage-alert border
BAR_BG = (0.05, 0.07, 0.10, 0.85)
TEXT_FG = (0.82, 0.90, 0.96, 1.0)
TEXT_WARN = (1.0, 0.55, 0.30, 1.0)
TEXT_BAD = (1.0, 0.20, 0.20, 1.0)
TEXT_GOOD = (0.35, 0.95, 0.55, 1.0)

SUBSYSTEM_ORDER = (Subsystem.ENGINE, Subsystem.WEAPONS, Subsystem.SENSORS)


def _clamp01(v):
    return max(0.0, min(1.0, v))


# -- world -> local (body/camera) frame --------------------------------------
def world_to_body(world, player_pos, player_quat):
    """World point -> player body frame (forward +Y, right +X, up +Z)."""
    rel = Vec3(*world) - Vec3(*player_pos)
    return Quat(*player_quat).conjugate().xform(rel)


def world_to_camera(world, cam_pos, cam_quat):
    """World point -> camera space (forward +Y, right +X, up +Z)."""
    rel = Vec3(*world) - Vec3(*cam_pos)
    return Quat(*cam_quat).conjugate().xform(rel)


# -- world -> radar -----------------------------------------------------------
@dataclass(frozen=True)
class RadarBlip:
    entity_id: str
    kind: str               # 'fighter' | 'capital'
    state: str              # healthy/smoking/burning/destroyed
    range: float            # metres
    azimuth_deg: float      # -180..180, 0 forward, + right
    elevation_deg: float    # -90..90, 0 level, + above
    above: float            # metres above the player (body +Z)
    scope_x: float          # disk coords: right (+x), forward (+y), radial in [0,1]
    scope_y: float
    vertical: float         # sin(elevation): + above, - below, in [-1,1]
    in_range: bool

    def to_dict(self):
        return dict(entity_id=self.entity_id, kind=self.kind, state=self.state,
                    range=self.range, azimuth_deg=self.azimuth_deg,
                    elevation_deg=self.elevation_deg, above=self.above,
                    scope_x=self.scope_x, scope_y=self.scope_y,
                    vertical=self.vertical, in_range=self.in_range)


def radar_blip(body, max_range, entity_id, kind="fighter", state="healthy"):
    """Body-frame offset -> radar blip. `body` is a Vec3 (forward +Y)."""
    dist = body.length()
    in_range = dist <= max_range
    if dist <= 1e-6:
        return RadarBlip(entity_id, kind, state, 0.0, 0.0, 0.0, 0.0,
                         0.0, 0.0, 0.0, True)
    azimuth = math.degrees(math.atan2(body.x, body.y))
    elevation = math.degrees(math.atan2(body.z, math.hypot(body.x, body.y)))
    radial = min(1.0, dist / max_range)
    horiz = math.hypot(body.x, body.y)
    if horiz < 1e-6:
        sx = sy = 0.0
    else:
        sx = radial * body.x / horiz
        sy = radial * body.y / horiz
    vertical = max(-1.0, min(1.0, body.z / dist))
    return RadarBlip(entity_id, kind, state, dist, azimuth, elevation,
                     body.z, sx, sy, vertical, in_range)


def build_radar(enemy_positions, enemy_states, player_pos, player_quat,
                sensors_multiplier, max_range=RADAR_BASE_RANGE):
    """Map enemy world positions -> radar blips (nearest first)."""
    reach = max_range * _clamp01(sensors_multiplier)
    if reach <= 1e-6:
        return []  # sensors destroyed: radar is blind
    blips = []
    for eid, pos in enemy_positions.items():
        meta = enemy_states.get(eid, {})
        body = world_to_body(pos, player_pos, player_quat)
        blips.append(radar_blip(body, reach, eid,
                                meta.get("kind", "fighter"),
                                meta.get("state", "healthy")))
    return sorted(blips, key=lambda b: b.range)


# -- camera -> screen (targeting transform) ----------------------------------
def project_camera_point(cam_point, hfov_deg, vfov_deg, near=0.1):
    """Camera-space point (forward +Y) -> NDC (x right, y up), each in [-1,1].
    Returns None when behind the near plane. Matches Panda Lens.project."""
    cx, cy, cz = cam_point
    if cy <= near:
        return None
    hx = math.tan(math.radians(hfov_deg) / 2.0)
    hy = math.tan(math.radians(vfov_deg) / 2.0)
    return (cx / cy / hx, cz / cy / hy)


def ndc_to_aspect2d(ndc, aspect):
    """NDC (x right, y up, each [-1,1]) -> aspect2d (x in [-aspect, aspect])."""
    return (ndc[0] * aspect, ndc[1])


def target_aspect2d(world, cam_pos, cam_quat, hfov_deg, vfov_deg, aspect,
                    near=0.1):
    """World point -> aspect2d coordinates, or None when off-camera/behind."""
    cam = world_to_camera(world, cam_pos, cam_quat)
    ndc = project_camera_point(cam, hfov_deg, vfov_deg, near)
    if ndc is None:
        return None
    return ndc_to_aspect2d(ndc, aspect)


# -- HUD state binding (pure) -------------------------------------------------
@dataclass(frozen=True)
class HudState:
    # player hull / systems
    hull: float
    hull_pct: int
    state: str
    subsystems: Mapping[Subsystem, float]
    repairing: str | None
    repair_progress: float
    sensors_multiplier: float
    # flight
    throttle: float
    speed: float
    # weapon
    weapon_name: str
    heat: float
    heat_capacity: float
    overheated: bool
    firing_blocked: bool
    ready: bool
    # targeting
    locked_target: str | None
    lock_in_range: bool
    on_target: bool
    # selected enemy (locked target) status
    target_kind: str | None
    target_state: str | None
    target_hull: float | None
    target_subsystems: Mapping[Subsystem, float] | None
    target_repairing: str | None
    target_repair_progress: float | None
    target_range: float | None
    # alert
    red_alert: bool
    damaged_subsystems: tuple = field(default_factory=tuple)

    def to_dict(self):
        return dict(
            hull=self.hull, hull_pct=self.hull_pct, state=self.state,
            subsystems={s.value: round(v, 4) for s, v in self.subsystems.items()},
            repairing=self.repairing, repair_progress=self.repair_progress,
            sensors_multiplier=self.sensors_multiplier, throttle=self.throttle,
            speed=self.speed, weapon_name=self.weapon_name, heat=self.heat,
            heat_capacity=self.heat_capacity, overheated=self.overheated,
            firing_blocked=self.firing_blocked, ready=self.ready,
            locked_target=self.locked_target, lock_in_range=self.lock_in_range,
            on_target=self.on_target, target_kind=self.target_kind,
            target_state=self.target_state, target_hull=self.target_hull,
            target_subsystems=(None if self.target_subsystems is None else
                               {s.value: round(v, 4) for s, v in self.target_subsystems.items()}),
            target_repairing=self.target_repairing,
            target_repair_progress=self.target_repair_progress,
            target_range=self.target_range, red_alert=self.red_alert,
            damaged_subsystems=[s.value for s in self.damaged_subsystems],
        )


def build_hud_state(player_health, aim, ship, target_health=None,
                    target_kind=None, target_range=None):
    subs = dict(player_health.subsystems)
    damaged = tuple(s for s in SUBSYSTEM_ORDER if subs[s] < 1.0 - 1e-9)
    red_alert = player_health.hull < 1.0 - 1e-9 or bool(damaged)
    speed = math.sqrt(sum(v * v for v in ship.velocity))
    if target_health is not None:
        tsubs = dict(target_health.subsystems)
        target_state = target_health.state.value
        target_hull = target_health.hull
        target_repairing = target_health.repairing.value if target_health.repairing else None
        target_repair_progress = target_health.repair_progress
    else:
        tsubs = None
        target_state = target_hull = None
        target_repairing = target_repair_progress = None
    return HudState(
        hull=player_health.hull,
        hull_pct=round(player_health.hull * 100),
        state=player_health.state.value,
        subsystems=subs,
        repairing=player_health.repairing.value if player_health.repairing else None,
        repair_progress=player_health.repair_progress,
        sensors_multiplier=player_health.sensors_multiplier,
        throttle=ship.throttle,
        speed=speed,
        weapon_name=aim.weapon_name,
        heat=aim.heat,
        heat_capacity=aim.heat_capacity,
        overheated=aim.overheated,
        firing_blocked=aim.firing_blocked,
        ready=aim.ready,
        locked_target=aim.locked_target,
        lock_in_range=aim.lock_in_range,
        on_target=aim.on_target,
        target_kind=target_kind,
        target_state=target_state,
        target_hull=target_hull,
        target_subsystems=tsubs,
        target_repairing=target_repairing,
        target_repair_progress=target_repair_progress,
        target_range=target_range,
        red_alert=red_alert,
        damaged_subsystems=damaged,
    )


def _pct(v):
    return f"{round(v * 100)}%"


def hud_lines(hud):
    """Canonical HUD text lines (left status, alert, right target)."""
    left = [
        f"HULL {hud.hull_pct}% {hud.state.upper()}",
    ]
    for sub in SUBSYSTEM_ORDER:
        v = hud.subsystems[sub]
        left.append(f"{sub.value.upper():7s} {_pct(v)}")
    left.append(f"THROTTLE {round(hud.throttle*100)}%  SPD {hud.speed:.0f}")
    heat_pct = round(hud.heat / hud.heat_capacity * 100) if hud.heat_capacity else 0
    heat_line = f"{hud.weapon_name.upper()}  HEAT {heat_pct}%"
    if hud.overheated:
        heat_line += " OVERHEAT"
    if hud.firing_blocked:
        heat_line += " BLOCKED"
    left.append(heat_line)
    if hud.locked_target:
        left.append(f"LOCK {hud.locked_target}")
        if hud.on_target:
            left.append("FIRE SOLUTION")

    alert = []
    if hud.red_alert:
        if hud.hull < 1.0 - 1e-9:
            alert.append(f"HULL {hud.hull_pct}%")
        for sub in hud.damaged_subsystems:
            alert.append(f"{sub.value.upper()} {_pct(hud.subsystems[sub])}")

    right = []
    if hud.locked_target is not None:
        kind = hud.target_kind or "ship"
        right.append(f"TARGET {hud.locked_target} [{kind}]")
        if hud.target_hull is not None:
            right.append(f"HULL {round(hud.target_hull*100)}% "
                         f"{hud.target_state.upper()}")
        if hud.target_subsystems is not None:
            sub = "  ".join(f"{s.value[:3].upper()} {_pct(v)}"
                            for s in SUBSYSTEM_ORDER for v in [hud.target_subsystems[s]])
            right.append(sub)
        if hud.target_repairing:
            right.append(f"REPAIRING {hud.target_repairing} "
                         f"{round(hud.target_repair_progress*100)}%")
        if hud.target_range is not None:
            right.append(f"RANGE {hud.target_range:.0f} m")
    return left, alert, right


def aspect2d_to_pixel(x, y, width=960, height=540):
    """aspect2d coords (x in [-aspect, aspect], y in [-1, 1]) -> pixel (y down)."""
    aspect = width / height
    px = (x / aspect + 1) / 2 * width
    py = (1 - y) / 2 * height
    return round(px), round(py)


def radar_blip_pixel(scope_x, scope_y, width=960, height=540):
    """Radar scope coords -> pixel position of a blip dot centre."""
    cx, cy = RADAR_CENTER
    x = cx + scope_x * RADAR_RADIUS
    y = cy + scope_y * RADAR_RADIUS
    return aspect2d_to_pixel(x, y, width, height)


# -- presentation system ------------------------------------------------------
def _quad(parent, name, color, x, y, w, h, sort=1):
    cm = CardMaker(name)
    cm.setFrame(0, 1, 0, 1)
    np = parent.attachNewNode(cm.generate())
    np.setBin("fixed", sort)
    np.setDepthTest(False)
    np.setDepthWrite(False)
    np.setTransparency(True)
    np.setColor(*color)
    np.setPos(x, 0, y)
    np.setScale(w, 1, h)
    return np


def _px_size(w_px, h_px, width=960, height=540):
    """Pixel size -> aspect2d size (x, y)."""
    aspect = width / height
    return (w_px / width) * 2 * aspect, (h_px / height) * 2


class CockpitSystem:
    """Owns the cockpit mesh, HUD panels/bars, radar and targeting bracket.

    Read-only: it receives snapshot providers at construction and calls them in
    present(); it never mutates flight/damage/weapons state.
    """

    def __init__(self, render, camera, aspect2d, lens,
                 player_entity_id, damage, weapons, enemies, flight,
                 window=(960, 540)):
        self.render = render
        self.camera = camera
        self.aspect2d = aspect2d
        self.lens = lens
        self.player_id = player_entity_id
        self.damage = damage
        self.weapons = weapons
        self.enemies = enemies
        self.flight = flight
        self.window = window
        self.aspect = window[0] / window[1]

        self.last_blips = []
        self.last_hud = None
        self.cockpit_root = self.camera.attachNewNode("cockpit")
        self._build_cockpit_frame()
        self._build_gauges()

        self.hud_root = self.aspect2d.attachNewNode("cockpit-hud")
        self._build_hud()
        self._build_radar()
        self._build_alert()
        self._build_bracket()

    # -- cockpit frame (3D, camera-attached) ---------------------------------
    def _build_cockpit_frame(self):
        from breach.scene import box
        root = self.cockpit_root
        parts = (
            # name, size, pos, color
            ("dashboard", (2.7, 0.24, 0.16), (0, 1.5, -0.50), COCKPIT_COLOR),
            ("left-sill", (0.11, 2.4, 0.60), (-0.62, 1.1, 0.02), COCKPIT_COLOR),
            ("right-sill", (0.11, 2.4, 0.60), (0.62, 1.1, 0.02), COCKPIT_COLOR),
            ("top-bar", (2.0, 0.13, 0.13), (0, 1.05, 0.44), COCKPIT_COLOR),
            ("left-strut", (0.10, 0.10, 1.4), (-0.85, 1.2, 0.0), COCKPIT_COLOR),
            ("right-strut", (0.10, 0.10, 1.4), (0.85, 1.2, 0.0), COCKPIT_COLOR),
        )
        for name, size, pos, color in parts:
            mesh = box(f"cockpit-{name}", size, color)
            mesh.reparentTo(root)
            mesh.setPos(*pos)
            mesh.setLightOff()

    def _build_gauges(self):
        from breach.scene import box
        # Four instruments on the dashboard front face, tinted by live state.
        self.gauges = {}
        positions = {
            "hull": (-0.05, 1.42, -0.44),
            Subsystem.ENGINE.value: (0.25, 1.42, -0.44),
            Subsystem.WEAPONS.value: (0.55, 1.42, -0.44),
            Subsystem.SENSORS.value: (0.85, 1.42, -0.44),
        }
        for key, pos in positions.items():
            mesh = box(f"gauge-{key}", (0.26, 0.05, 0.10), GAUGE_OK)
            mesh.reparentTo(self.cockpit_root)
            mesh.setPos(*pos)
            mesh.setLightOff()
            self.gauges[key] = mesh

    # -- HUD panels -----------------------------------------------------------
    def _text(self, parent, text, x, y, scale=0.042, fg=TEXT_FG, align=None):
        from direct.gui.OnscreenText import OnscreenText
        from panda3d.core import TextNode
        kw = dict(pos=(x, y), scale=scale, fg=fg, mayChange=True)
        if align is not None:
            kw["align"] = align
        return OnscreenText(text=text, parent=parent, **kw)

    def _build_hud(self):
        self.left_lines = []
        y = 0.90
        for _ in range(8):
            t = self._text(self.hud_root, "", -1.72, y, scale=0.044)
            self.left_lines.append(t)
            y -= 0.062
        self.alert_lines = []
        y = 0.30
        for _ in range(4):
            t = self._text(self.hud_root, "", -1.72, y, scale=0.052, fg=TEXT_BAD)
            self.alert_lines.append(t)
            y -= 0.075
        self.right_lines = []
        y = 0.90
        for _ in range(6):
            t = self._text(self.hud_root, "", 0.10, y, scale=0.044)
            self.right_lines.append(t)
            y -= 0.062
        # Hull bar (top-left), subsystem bars, heat bar.
        bx, by = -1.72, 0.40
        bw, bh = 1.1, 0.035
        _quad(self.hud_root, "hullbar-bg", BAR_BG, bx, by, bw, bh, 0)
        self.hull_bar = _quad(self.hud_root, "hullbar-fill", GAUGE_OK, bx, by, bw, bh, 2)
        self.sub_bars = {}
        by -= 0.062
        for sub in SUBSYSTEM_ORDER:
            _quad(self.hud_root, f"{sub.value}-bar-bg", BAR_BG, bx, by, bw, bh, 0)
            self.sub_bars[sub] = _quad(self.hud_root, f"{sub.value}-bar-fill",
                                       GAUGE_OK, bx, by, bw, bh, 2)
            by -= 0.055
        _quad(self.hud_root, "heatbar-bg", BAR_BG, bx, by, bw, bh, 0)
        self.heat_bar = _quad(self.hud_root, "heatbar-fill", RADAR_RIM, bx, by, bw, bh, 2)

    # -- radar ----------------------------------------------------------------
    def _build_radar(self):
        # Scope disk, bottom-left. centre in aspect2d coords, radius in y-units.
        self.radar_center = RADAR_CENTER
        self.radar_radius = RADAR_RADIUS
        cx, cy = self.radar_center
        # background disk (a square, alpha low) + a rim ring approximation
        _quad(self.hud_root, "radar-bg", RADAR_BG, cx - self.radar_radius,
              cy - self.radar_radius, self.radar_radius * 2, self.radar_radius * 2, 0)
        # forward tick at top of the scope
        tick_w, tick_h = _px_size(6, 16)
        self.radar_forward = _quad(self.hud_root, "radar-forward", RADAR_FORWARD,
                                   cx - tick_w / 2, cy + self.radar_radius - tick_h,
                                   tick_w, tick_h, 1)
        self.blips = {}   # entity_id -> dict of quads

    def _draw_blip(self, blip):
        entry = self.blips.get(blip.entity_id)
        if entry is None:
            size = 10 if blip.kind == "capital" else 8
            w, h = _px_size(size, size)
            dot = _quad(self.hud_root, f"blip-{blip.entity_id}", RADAR_BLIP, 0, 0, w, h, 3)
            stem_w, stem_h = _px_size(3, 20)
            stem = _quad(self.hud_root, f"stem-{blip.entity_id}", RADAR_ABOVE, 0, 0, stem_w, stem_h, 3)
            entry = {"dot": dot, "stem": stem}
            self.blips[blip.entity_id] = entry
        cx, cy = self.radar_center
        r = self.radar_radius
        px = cx + blip.scope_x * r
        py = cy + blip.scope_y * r
        dot_w, dot_h = _px_size(10 if blip.kind == "capital" else 8,
                                10 if blip.kind == "capital" else 8)
        entry["dot"].setPos(px - dot_w / 2, 0, py - dot_h / 2)
        # elevation stem
        stem_len = abs(blip.vertical) * r * 0.55
        stem_w, _ = _px_size(3, 20)
        if abs(blip.vertical) < 0.02:
            entry["stem"].hide()
        else:
            entry["stem"].show()
            entry["stem"].setColor(RADAR_ABOVE if blip.vertical > 0 else RADAR_BELOW)
            base_y = py + dot_h / 2 if blip.vertical > 0 else py - dot_h / 2
            entry["stem"].setPos(px - stem_w / 2, 0, base_y)
            entry["stem"].setScale(stem_w, 1, stem_len)

    def _build_alert(self):
        # Four thin red border quads around the screen edges, hidden unless alert.
        w, h = self.window
        ax, ay = self.aspect, 1.0
        th = _px_size(6, 6)[1]
        full_w = 2 * ax
        self.alert_quads = [
            _quad(self.hud_root, "alert-top", ALERT_RED, -ax, 1 - th, full_w, th, 4),
            _quad(self.hud_root, "alert-bot", ALERT_RED, -ax, -1, full_w, th, 4),
            _quad(self.hud_root, "alert-left", ALERT_RED, -ax, -1, th, 2, 4),
            _quad(self.hud_root, "alert-right", ALERT_RED, ax - th, -1, th, 2, 4),
        ]
        for q in self.alert_quads:
            q.hide()

    def _build_bracket(self):
        # Targeting bracket: 4 corner quads around the locked target.
        self.bracket_quads = []
        for name in ("b-tl", "b-tr", "b-bl", "b-br"):
            w, h = _px_size(10, 3)
            q = _quad(self.hud_root, name, TEXT_FG, 0, 0, w, h, 3)
            q.hide()
            self.bracket_quads.append(q)

    # -- per-frame update -----------------------------------------------------
    def _set_lines(self, nodes, lines):
        for i, node in enumerate(nodes):
            node.setText(lines[i] if i < len(lines) else "")

    def present(self, alpha, pose, player_health, aim, target_health=None):
        locked = aim.locked_target
        target_kind = target_range = None
        if locked is not None:
            target_kind = self._target_kind(locked)
            tpos = self._target_world_position(locked)
            target_range = (Vec3(*tpos) - Vec3(*pose.position)).length()
        hud = build_hud_state(player_health, aim, pose, target_health,
                              target_kind, target_range)
        left, alert, right = hud_lines(hud)
        self._set_lines(self.left_lines, left)
        self._set_lines(self.alert_lines, alert)
        self._set_lines(self.right_lines, right)

        # bars
        self.hull_bar.setScale(1.1 * hud.hull, 1, 0.035)
        self.hull_bar.setColor(self._hull_color(hud.hull, hud.state))
        for sub in SUBSYSTEM_ORDER:
            v = hud.subsystems[sub]
            self.sub_bars[sub].setScale(1.1 * v, 1, 0.035)
            self.sub_bars[sub].setColor(self._sub_color(v))
        self.heat_bar.setScale(1.1 * (hud.heat / hud.heat_capacity), 1, 0.035)
        self.heat_bar.setColor(TEXT_WARN if hud.overheated else RADAR_RIM)

        # gauges (3D instruments) tint by live state
        self._tint_gauge("hull", self._hull_color(hud.hull, hud.state))
        for sub in SUBSYSTEM_ORDER:
            self._tint_gauge(sub.value, self._sub_color(hud.subsystems[sub]))

        # alert border
        for q in self.alert_quads:
            if hud.red_alert:
                q.show()
            else:
                q.hide()

        # radar
        self._update_radar(pose, hud)

        # targeting bracket
        self._update_bracket(pose, hud)
        self.last_hud = hud
        return hud

    @staticmethod
    def _hull_color(hull, state):
        if state == "destroyed":
            return GAUGE_CRIT
        if state == "burning":
            return GAUGE_CRIT
        if state == "smoking":
            return GAUGE_WARN
        return GAUGE_OK

    @staticmethod
    def _sub_color(v):
        if v <= 0.0:
            return GAUGE_CRIT
        if v < 0.5:
            return GAUGE_WARN
        return GAUGE_OK

    def _tint_gauge(self, key, color):
        gauge = self.gauges.get(key)
        if gauge is not None:
            gauge.setColor(*color)

    def _update_radar(self, pose, hud):
        enemy_positions = {}
        enemy_states = {}
        for eid in self.enemies.entities:
            view = self.enemies.snapshot(eid)
            enemy_positions[eid] = view.position
            enemy_states[eid] = {
                "kind": "capital" if self.enemies.is_capital(eid) else "fighter",
                "state": self.damage.snapshot(eid).state.value,
            }
        blips = build_radar(enemy_positions, enemy_states,
                            pose.position, pose.orientation,
                            hud.sensors_multiplier)
        self.last_blips = blips
        seen = set()
        for blip in blips:
            if not blip.in_range:
                continue
            seen.add(blip.entity_id)
            self._draw_blip(blip)
        # hide blips for despawned enemies
        for eid, entry in self.blips.items():
            if eid not in seen:
                entry["dot"].hide()
                entry["stem"].hide()
            else:
                entry["dot"].show()

    def _target_kind(self, entity_id):
        if entity_id in self.enemies.entities:
            return "capital" if self.enemies.is_capital(entity_id) else "fighter"
        return self.damage.ship_profile(entity_id).name

    def _target_world_position(self, entity_id):
        if entity_id in self.enemies.entities:
            return self.enemies.snapshot(entity_id).position
        return self.weapons.target_position(entity_id)

    def _update_bracket(self, pose, hud):
        locked = hud.locked_target
        if locked is None:
            for q in self.bracket_quads:
                q.hide()
            return
        tpos = self._target_world_position(locked)
        cam_pos = Vec3(*pose.position) + Quat(*pose.orientation).xform(Vec3(0, 0, 0.65))
        cam_quat = pose.orientation
        hfov, vfov = self.lens.getFov()
        pos = target_aspect2d(tpos, tuple(cam_pos), cam_quat,
                              hfov, vfov, self.aspect)
        if pos is None:
            for q in self.bracket_quads:
                q.hide()
            return
        color = TEXT_GOOD if hud.on_target else TEXT_FG
        half = _px_size(18, 18)
        hw, hh = half[0] / 2, half[1] / 2
        lw, lh = _px_size(12, 3)
        corners = [
            (pos[0] - hw, pos[1] + hh, 0),   # top-left
            (pos[0] + hw - lw, pos[1] + hh, 0),
            (pos[0] - hw, pos[1] - hh, 0),
            (pos[0] + hw - lw, pos[1] - hh, 0),
        ]
        for q, (cx, cy, _z) in zip(self.bracket_quads, corners):
            q.show()
            q.setColor(*color)
            q.setPos(cx, 0, cy)
            q.setScale(lw, 1, lh)


__all__ = [
    "RADAR_BASE_RANGE", "COCKPIT_COLOR", "RADAR_BLIP", "RADAR_ABOVE",
    "RADAR_BELOW", "ALERT_RED", "GAUGE_OK", "GAUGE_WARN", "GAUGE_CRIT",
    "SUBSYSTEM_ORDER",
    "world_to_body", "world_to_camera", "radar_blip", "build_radar",
    "RadarBlip", "project_camera_point", "ndc_to_aspect2d", "target_aspect2d",
    "build_hud_state", "hud_lines", "HudState", "CockpitSystem",
    "RADAR_CENTER", "RADAR_RADIUS", "aspect2d_to_pixel", "radar_blip_pixel",
]
