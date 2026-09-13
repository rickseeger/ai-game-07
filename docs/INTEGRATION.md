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
- damage.py: DamageSystem.queue(DamageEvent | RepairIntent) -> None and
  fixed_update(dt, controls) -> None. Sole owner of health/repair state.
  snapshot(entity_id) -> HealthView. Consume queued events once in enqueue order.
- weapons.py: WeaponsSystem.fixed_update(dt, controls) -> None. Own projectile
  positions, cooldowns, hit collision queries and DamageEvent emission; never
  directly reduce health. Repair blocks firing at this boundary too.
- enemies.py: EnemySystem.fixed_update(dt, controls) -> None; own non-player poses,
  steering and attack decisions; emit weapon requests through weapons.fire(entity_id,
  origin: Vec3, direction: Vec3) -> bool, which enforces rate/health restrictions.
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
HealthView schema (add with damage): hull: float [0,1], subsystems: immutable
mapping[Subsystem,float], repairing: Subsystem|None, repair_progress: float [0,1],
engine_multiplier: float, weapons_multiplier: float, sensors_multiplier: float.
Destroyed means hull == 0; removal from registry happens at end of fixed tick.
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
for exact current controls. No repair/weapon effects are implemented.

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
NeutralDamage returns full performance and no lock. The damage-node adapter must
supply actual health-derived values; node 2 does not invent health/repair progress.
R immediately brakes/zeros thrust/locks rotation as a flight-side request guard.
The downstream weapons and damage services must still enforce their own rules.
Turning multipliers do not disable mouse input or the cockpit viewpoint.

The app runs only input -> flight -> snapshot -> presentation at this stage;
insert the sibling systems in the previously specified order when implemented.
