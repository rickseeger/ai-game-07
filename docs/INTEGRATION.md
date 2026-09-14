# Integration contract and compact combat design

## Settled foundation

Engine: Panda3D 1.10.16, CPython 3.12, uv.lock, OpenGL/GLX on Linux. This supplies
real-time 3D, scene graph, collision facilities, event input and OpenAL without
an editor, and is exercised here on Mesa llvmpipe. Prefer this over introducing
a second engine or a custom renderer. Root app owns the only ShowBase/window;
modules must not create a window at import. Procedural meshes are the baseline.
Node 2 now supplies player flight and a ship-anchored camera. Scene enemies
remain staging meshes, not combat. See FLIGHT.md for measured flight evidence.

Coordinates: right-handed, +X right, +Y forward, +Z up; one unit = one metre.
World positions/velocities are metres/metres per second. Entity forward is +Y.
Use Panda Quat internally and Vec3 at engine boundaries; serialized quaternion
order is (w,x,y,z). Panda HPR is degrees (heading about Z, pitch about X, roll
about Y), not aircraft Euler conventions. Positive pilot yaw means nose right,
pitch means nose up, roll means right wing down: flight adapter must unit-test
these semantics rather than blindly assigning HPR signs. Health is normalized
[0,1], damage amount is HP, timers seconds, angular speed degrees/second.
Each ship defines hull_max_hp and subsystem_max_hp, avoiding implicit unit mixing.

Timing (implemented by timing.FixedStepper and app integration):
60 Hz fixed simulation; accumulate min(real_dt, 0.1), at most six fixed steps,
drop excess backlog explicitly with a diagnostic. Render interpolates last/current
poses with alpha in [0,1]. Rendering never changes simulation. Headless simulation
tests use seeded randomness; visual captures also record seed, tick and input.

## Module ownership and boundary signatures

src/breach/contracts.py provides validated immutable PilotInput, DamageEvent,
RepairIntent, Subsystem, SimulationSystem.fixed_update(dt, controls), and
PresentationSystem.present(alpha). Shared IDs are stable strings, never NodePaths.
The following APIs are the handoff contract. InputAdapter, FlightSystem and
ShipView are now implemented; combat services remain future work. Implement
each in its named module, not app.py.

- input.py: InputAdapter.sample() -> PilotInput. Own key/mouse state and focus
  clearing; never mutate ship transforms. PilotInput axes are [-1,1]; throttle
  is signed rate-of-change request, not an absolute throttle value. Fire/repair
  are held levels; target_next is one edge consumed on a single fixed tick.
- flight.py: FlightSystem.fixed_update(dt, controls) -> None; own player pose,
  velocity and throttle. Read damage performance multipliers/repair lock from
  constructor-injected damage service. Expose snapshot() -> immutable ShipView.
- damage.py (IMPLEMENTED, node 3): DamageSystem.queue(DamageEvent | RepairIntent)
  -> None and fixed_update(dt, controls) -> None. Sole owner of health/repair
  state. snapshot(entity_id) -> HealthView. Consume queued events once in enqueue
  order. Also implements FlightDamageService.flight_performance and
  capability()/subsystem_health() queries for the sibling weapons/enemies nodes.
- weapons.py (IMPLEMENTED, node 4): WeaponsSystem.fixed_update(dt, controls)
  -> None. Own projectile positions, cooldowns/heat, hit collision queries and
  DamageEvent emission; never directly reduce health. Repair blocks firing at
  this boundary too. See the Node 4 boundary below and docs/WEAPONS.md.
- enemies.py: EnemySystem.fixed_update(dt, controls) -> None; own non-player poses,
  steering and attack decisions; emit weapon requests through weapons.fire(entity_id,
  origin: Vec3, direction: Vec3, weapon=None) -> bool, which enforces rate/health
  restrictions (implemented, node 4).
- mission.py: MissionSystem.fixed_update(dt, controls) -> None; own spawn registry,
  objectives/win/lose/restart; subscribe to health snapshots. No UI side effects.
- cockpit.py: CockpitSystem.present(alpha) -> None; own cockpit mesh, HUD and radar,
  read-only snapshots. World entities remain under render; cockpit uses a dedicated
  camera-attached root, HUD aspect2d. Must not write flight/damage state.
- effects.py: EffectsSystem.present(alpha) -> None; consume a copy of combat events,
  own disposable particles/audio/camera feedback; --mute must be supported.
- app.py integration owner: create services once, inject narrow dependencies,
  schedule systems, dispose/recreate mission state on restart; no global ShowBase
  singleton accesses from simulation modules. Packaging wraps this same entry point.

ShipView schema (implemented immutable dataclass): entity_id: str,
position: tuple[float,float,float], velocity: tuple[float,float,float],
orientation: tuple[float,float,float,float] in wxyz order, throttle: float [0,1].
HealthView schema (implemented, node 3): hull, hull_hp, state (ShipState
healthy/smoking/burning/destroyed), subsystems (immutable mapping), repairing,
repair_progress, and engine/turning/weapons/sensors multipliers. Destroyed means
hull == 0 and zeroes every multiplier. Destroyed entities remain queryable with
state DESTROYED (performance zeroed); the future mission system despawns them.
Snapshots are copies/read-only, never references to mutable authoritative state.

Fixed order: input -> flight -> enemies -> weapons/hit queries -> damage/repair
-> mission -> snapshot publication -> presentation. Flight reads prior-tick damage;
this intentional one-tick latency also applies to newly completed repairs. The
repair entry request immediately prevents weapons firing; damage service decides
whether speed is low enough to begin restoring health. Damage resolves before
repair; a hit interrupts repair progress on that tick. UI/effects see the same
published state. Queue copies let presentation observe events without consuming
simulation events. Choose collision masks centrally: environment=1, player=2,
enemy=4, projectile=8; projectiles query environment and opposing ships, not self.
Add tests of these boundaries before integrating each sibling subsystem.

## Input approach

Panda accept(key/key-up), mouseWatcherNode and window focus events, not raw /dev
reads. Intended controls: mouse aims pitch/yaw with a bounded soft deadzone;
W/S adjust throttle, A/D roll, arrows provide keyboard pitch/yaw fallback,
left mouse or Space fires, Tab cycles targets, hold R repairs selected subsystem,
1/2/3 select engine/weapons/sensors, Escape pauses/releases mouse. Relative mouse
capture only during play; focus loss clears all held controls and pauses. Remap
through one binding table, with sensitivity and inversion settings. Controller
support is deferred; do not advertise it. Node 2 replaces inspection Space with the reserved fire level; Escape now
pauses, F10 exits, B brakes, C centers aim, Home levels pitch/roll. See README
for exact current controls. Node 3 implements damage and repair; weapon effects
remain future work.

## Scenario: Breach Flight (design target, not implemented gameplay)

A compact, replayable 5–8 minute cockpit sortie: disable an interdiction frigate
and escape its field. No campaign, crafting, open world or friendly fleet AI.
Begin facing a clearly marked hostile patrol at useful range with immediate
pitch/yaw/throttle guidance. Forgiving assisted flight, aim lead and visible hit
feedback make first contact accessible; retain momentum and positional decisions.

Player survives several bursts rather than dying to a single surprise. Hits
persist: engine damage reduces acceleration/top speed, weapons damage lowers
sustained firing output, sensors damage shortens radar/lock reach but never hides
the basic escape/repair instructions. No automatic health refill after combat.
Hull gives a separate readable survival margin. Provide directional impact cues,
per-subsystem condition bars and a plain explanation of the current impairment.

Core decision: keep fighting impaired or stop to repair while still exposed.
Hold R below 2 m/s to repair the selected subsystem; ramp thrust to zero, disable
weapons and turning assistance, show an unmistakable progress/countdown and danger
state. Releasing R instantly cancels and permits escape; acceleration still takes
time. Initial tuning target: 4 seconds per subsystem restoration to 70%, limited
repair resource shared with partial hull restoration (cap hull at 60%). Incoming
hits interrupt the current uninterrupted repair interval, without undoing already
completed restoration. Tune resource capacity so one sortie permits a few useful
repairs, never infinite safe healing. These values are configuration, not constants
buried in HUD logic. Cover reduces enemy line of sight but grants no invulnerability.

Encounter beats: two light fighters teach aiming/damage; approach the capital ship
under telegraphed turret arcs; disable two exposed power modules from distinct
angles; only then its core becomes vulnerable. Capital HP must survive multiple
attack passes; turret blind spots/debris create risky repair opportunities while
fighters pressure stationary players. Destroyed modules remain destroyed through
repairs. No infinitely replenishing fighter wave. Core destruction opens an exit
marker; reach it to win. Hull zero loses; clear debrief and one-action restart.

Acceptance targets for sibling integration: first hit within 30 seconds for a
new pilot; impairment visibly changes each affected system; a full uninterrupted
repair cannot finish while taking hits; repair cannot fire/thrust simultaneously;
capital cannot be removed by one burst or damage to an invulnerable core; both
win and loss restart cleanly. Human Linux playtest, not unit tests alone, judges
readability, repair tension, motion comfort and the capital encounter pacing.
Performance target is stable 60 fps at 1280x720 on the target laptop, not claimed
from this 960x540 software-rendered staging check.

## Node 2 concrete flight boundary

PilotInput adds brake and level held flags. InputAdapter has no engine imports;
all bindings are in BINDINGS, with sensitivity/deadzone/inversion InputSettings.
control_lines renders settings-correct help, shared by app and documented defaults.
FlightSystem.snapshot returns detached frozen ShipView; fixed_update alone moves
simulation. timing.FixedStepper publishes previous/current, alpha and dropped-time
diagnostics; input edges are consumed only when a fixed tick executes.
camera.FlightCamera presents interpolated pose through player-presentation and
keeps the eye local (0,0,0.65) with identity local orientation, inside the declared
hull envelope. Minimal canopy/HUD are flight references; future CockpitSystem
should replace presentation, never own/mutate authoritative flight state.

Damage integration uses constructor-injected FlightDamageService.flight_performance
(entity_id) -> FlightPerformance(thrust_multiplier, turning_multiplier, repair_locked).
NeutralDamage returns full performance and no lock. Node 3's DamageSystem is now
the real adapter: engine health drives thrust AND turning, repair engagement or
destruction sets repair_locked, and destruction zeroes all performance.
R immediately brakes/zeros thrust/locks rotation as a flight-side request guard;
the damage service independently gates repair on low speed and absence of hits.
Turning multipliers do not disable mouse input or the cockpit viewpoint.

## Node 3 concrete damage/repair boundary

damage.DamageSystem owns hull + per-subsystem HP and repair progress; it is pure
and deterministic. Profiles (DamageProfile) are explicit configuration:
FIGHTER (hull 100 / subsystem 40 / 4 s repair to 70% / <=2 m/s) and CAPITAL
(hull 3000 / subsystem 600 / 30 s / <=0.5 m/s). Hull thresholds map to
healthy -> smoking (<=0.66) -> burning (<=0.33) -> destroyed (==0). Subsystem
capability is linear in health (0 = failed). Repair restores the selected
subsystem at repair_rate HP/s up to repair_cap, gated on speed and no incoming
damage that tick; a hit interrupts the current interval without undoing
already-restored HP. The player's R intent is edge-triggered so it composes
with queued RepairIntent events. app.py spawns the player, injects the system
into FlightSystem and FixedStepper, and records health in the trace; the
--damage-script flag drives scripted damage/repair for validation. See
docs/DAMAGE.md and tests/test_damage.py for the full contract.

The app now runs input -> flight -> weapons -> damage -> snapshot ->
presentation (node 4 inserts weapons before damage so hits resolve the same
tick). Remaining siblings (enemies, mission, cockpit, effects) insert in the
previously specified order when implemented.

## Node 4 concrete weapons/hit-resolution boundary

weapons.WeaponsSystem owns projectiles, cadence/resources, target lock/aim
feedback, deterministic segment-vs-oriented-box hit detection and DamageEvent
emission; it never reduces health directly. Three WeaponSpec kinds (CANNON,
SCATTER, TORPEDO) differ in damage/cooldown/projectile speed/range/heat. Two
declared resources gate firing: cooldown and heat (0..capacity, cooling at
cool_rate). Weapon impairment reads the shared damage model: WEAPONS capability
scales damage per shot, a destroyed WEAPONS subsystem blocks firing, and repair
engagement blocks firing (repair_locked). fire(entity_id, origin, direction,
weapon=None) -> bool is the shared entry point for the player's held fire and
future enemy AI. Projectiles sweep per tick and test the nearest target's
oriented box; ownership filtering (never hit the owner's faction) separates
friendly/enemy fire; environment blocks without damage. aim_snapshot() ->
AimState exposes weapon/ready/cooldown/heat/impairment/locked target/fire
solution for the cockpit. app.py registers scene.combat_targets() hitboxes and
spawns their damage profiles; --fire-script and --no-combat-targets drive
deterministic offscreen combat runs. See docs/WEAPONS.md and tests/test_weapons.py.

## Node 5 concrete enemies/capital boundary

enemies.EnemySystem owns every non-player ship: pose, steering, attack and
repair decisions. It reuses the SAME flight, weapon, subsystem-impairment and
repair rules as the player — no separate enemy model. Each fighter is a real
FlightSystem sharing FlightTuning and the shared DamageSystem as its
FlightDamageService, so ENGINE damage impairs its thrust/turning and destruction
zeroes it. Enemies fire through weapons.fire(entity_id, origin, direction,
weapon) -> bool, so WEAPONS damage scales their damage, a destroyed WEAPONS
subsystem blocks them, and repair engagement blocks them. A crippled enemy
brakes to a stop, stops shooting and restores one subsystem via RepairIntent at
the same timed, hit-interruptible rate as the player; entry/exit are explicit
trace transitions (never silent/instant recovery). The capital (CAPITAL_PROFILE)
is anchored with a turret battery (WeaponKind.TURRET, in WEAPONS_WITH_TURRET;
the player still cycles only CANNON/SCATTER/TORPEDO) and requires ~40-50 s of
sustained fire. EnemyTuning holds all AI knobs. app.py runs enemies -> weapons
-> damage after flight, registers the player hitbox from flight.snapshot, wires
the damage speed gate to per-ship speeds, and records enemy_state/enemy_health/
enemy_events. --static-targets keeps the node-4 static hitboxes for isolated
weapon tests; --no-combat-targets registers nothing. See docs/ENEMIES.md and
tests/test_enemies.py.

## Node 6 concrete cockpit/HUD/radar boundary

cockpit.CockpitSystem owns the first-person presentation and is strictly
read-only: it reads HealthView/AimState/ShipView/enemy ShipViews and never writes
flight/damage/weapons state. It is constructed in app.py with (render, camera,
aspect2d, camLens, player_entity_id, damage, weapons, enemies, flight) and driven
by present(alpha, pose, player_health, aim, target_health=None) each frame after
the fixed step; pose is the interpolated ShipView from FlightCamera.present. The
camera-attached root builds the cockpit frame + four state-tinted instrument
gauges; the aspect2d root owns the HUD panels/bars, the radar scope, the targeting
bracket and the red alert border.

Pure, window-free helpers are exported for tests and tools: world_to_body /
world_to_camera (forward +Y, right +X, up +Z), radar_blip / build_radar (world ->
spherical scope: azimuth, elevation, range, scope_x/scope_y, vertical), and
project_camera_point / target_aspect2d (camera -> NDC -> aspect2d, matching
Lens.project). build_hud_state / hud_lines bind the HUD to the real snapshots.
The app records hud and radar on every frame trace, so the HUD and radar are
verifiable from the deterministic trace plus code-level PNG sampling (no vision).

The radar reach is RADAR_BASE_RANGE * sensors_multiplier (a destroyed SENSORS
subsystem blinds the radar); the targeting bracket reads the target's live enemy
pose when present and the weapons hitbox registry otherwise. --target-script
(JSON [{tick}]) injects Tab edges and --radar-spawns (JSON [{id, position}])
spawns extra enemy fighters for off-screen radar validation.
