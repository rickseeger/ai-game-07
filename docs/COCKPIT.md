# Node 6: first-person cockpit presentation, readable HUD and spatial radar

## Scope and ownership

`src/breach/cockpit.py` owns the *presentation* of the player's ship as seen
from inside it. It is strictly read-only with respect to simulation state: it
reads the validated snapshots (`HealthView`, `AimState`, `ShipView`, enemy
`ShipView`s) and never writes flight/damage/weapons state. The cockpit uses a
dedicated camera-attached root for the 3D frame and `aspect2d` for the HUD, per
docs/INTEGRATION.md. `app.py` constructs it once and calls `present()` every
frame after the fixed simulation step; it replaces the node-2 minimal canopy and
the raw one-line debug HUD.

Three layers are deliberately separated so the math is testable with no window:

1. **Pure transforms** — `world_to_body` / `world_to_camera`, `radar_blip` /
   `build_radar` (world -> radar), and `project_camera_point` /
   `target_aspect2d` (world -> screen).
2. **Pure HUD state binding** — `build_hud_state` and `hud_lines` turn the raw
   snapshots into the exact values and text the HUD displays.
3. **`CockpitSystem`** — the presentation owner (cockpit frame + instruments,
   HUD panels/bars, radar scope, targeting bracket, red alert border).

## The cockpit frame and instruments

A camera-attached root builds a fixed first-person cockpit: a dashboard, left
and right window sills, an overhead canopy bar, and two side struts, all flat
unlit steel (`COCKPIT_COLOR`) so they are visible in every frame and pixel-
sampleable. Four instrument gauges on the dashboard (HULL + ENGINE + WEAPONS +
SENSORS) are tinted by live state: green healthy, amber impaired, red failed/
critical. They are the "instruments"; the 2D HUD bars mirror the same values.

## The HUD (readable, live-bound)

A left status panel reports hull percentage + state, per-subsystem health, throttle,
speed, the selected weapon with a heat percentage, the lock, and a fire-solution
flag. A right target panel reports the selected enemy's hull/state, per-subsystem
health, active repair (subsystem + progress) and range. Horizontal bars (hull,
three subsystems, heat) fill by fraction and recolour by state. A full red border
plus per-subsystem red lines fire when the ship or any subsystem is damaged —
the actionable red alert. No HUD value is stubbed: every field derives from a
`HealthView`/`AimState`/`ShipView` in `build_hud_state`.

## The spatial radar

A polar "spherical" scope in the bottom-left corner: forward is up, right is
right, and range is radial distance from the centre. Each enemy blip is amber
(capital ships draw larger), and a vertical stem (green up / magenta down)
encodes above/below elevation. Direction, distance and above/below are thus all
communicated on one display. Blips are driven by live enemy world positions via
`build_radar`; reach is `RADAR_BASE_RANGE * sensors_multiplier`, so a damaged
SENSORS subsystem shortens the radar exactly as documented in INTEGRATION.md.

## The targeting transform

`target_aspect2d` projects a world point through the camera (forward +Y, right
+X, up +Z) into `aspect2d` coordinates using the live lens FOV/aspect, and is
cross-checked against Panda's own `Lens.project` in tests. The locked target's
position is taken from the live `EnemySystem` pose when present, otherwise from
the weapons hitbox registry, so the bracket works for both live and static
targets.

## Public boundary

    CockpitSystem(render, camera, aspect2d, lens, player_entity_id,
                  damage, weapons, enemies, flight) -> None
    CockpitSystem.present(alpha, pose, player_health, aim, target_health=None) -> HudState
    world_to_body / world_to_camera(world, pos, quat) -> Vec3
    radar_blip(body, max_range, entity_id, kind, state) -> RadarBlip
    build_radar(enemy_positions, enemy_states, pos, quat, sensors_multiplier) -> [RadarBlip]
    project_camera_point(cam_point, hfov_deg, vfov_deg, near) -> (x, y) | None
    target_aspect2d(world, cam_pos, cam_quat, hfov, vfov, aspect) -> (x, y) | None
    build_hud_state(player_health, aim, ship, target_health, ...) -> HudState
    hud_lines(hud) -> (left_lines, alert_lines, right_lines)

`HudState` and `RadarBlip` are frozen dataclasses with `to_dict()` for the trace.
The app records `hud` and `radar` on every frame so HUD/radar state is verifiable
from the deterministic trace alone.

## Verification (no vision tool used)

- `tests/test_cockpit.py` (20 tests): body-frame transform, radar azimuth/
  elevation/range/scope/above-below (including off-camera + out-of-range cases),
  sensors-reach scaling, nearest-first ordering, perspective projection
  cross-checked against `Lens.project`, pixel mapping, and HUD state/text binding
  driven by the real `DamageSystem`/`WeaponsSystem`/`FlightSystem` snapshots.
- `tools/cockpit_runtime.py`: real offscreen Panda3D launches — healthy (cockpit
  frame + green instruments + radar blips at computed pixels, no alert), offscreen
  (enemies spawned behind/above are located via the radar and the above/below
  stem renders), and damaged (red alert border + red hull instrument + target
  panel showing the selected enemy's damage and active repair). All assertions are
  deterministic trace reads plus code-level PNG framebuffer sampling.

Full rerun (fresh `.venv-verify`, real X11/GLX via Xvfb, llvmpipe):

    python3 tools/headless.py --venv .venv-verify --output artifacts/cockpit-release

runs unit tests and `cockpit_runtime.py` after the earlier nodes' checks. All
exit 0. No image perception is used.

## Honest limitations

The radar is a legible polar scope with a vertical elevation stem, not a
true 3D-rendered sphere; above/below is therefore encoded as a stem rather than
a second orthographic view. Subjective readability, motion comfort and the
"readable in the heat of combat" quality are deferred to the node-10 human
playtest. The enemy hitboxes registered at spawn are not yet updated as enemy
fighters move (a node-5 concern); the targeting bracket tracks the live visual
pose, which is what the player sees.
