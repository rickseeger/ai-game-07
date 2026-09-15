# Root-Contract Evidence Report — Breach Flight 1.0.0 (G14 node 11)

Independent verification of the Linux release candidate
`breach-flight-1.0.0-linux-x86_64.tar.gz` against the G14 root contract.
Method: extract and launch on Linux under Xvfb/llvmpipe, run the full automated
suite on the bundled runtime, inspect the source, and confirm every feature
with automated evidence only (physics/logic assertions, code-level pixel and
trace sampling, frame dumps). No image-perception/vision was used. Subjective
fun/visual/audible quality is explicitly OUT of scope (that is node 12, the
human playtest).

Artifact under test
  tarball:  breach-flight-1.0.0-linux-x86_64.tar.gz
  sha256:   671d5c98c0ff1e1392f3dbac8b685c44b3de7282c07ffcd16ee9872aad890cd2
  size:     86,559,075 bytes
  extracted: cleanroom/breach-flight-1.0.0-linux-x86_64/  (VERSION = 1.0.0)
  runtime:   bundled CPython 3.12.14 + Panda3D 1.10.16 (self-contained, no pip)

## 0. Extract + launch on Linux (Xvfb) — PASS
- `tar xzf` succeeds; `VERSION` = 1.0.0; `./launch.sh --version` = 1.0.0.
- Bundled interpreter: `runtime/bin/python3.12 --version` = Python 3.12.14;
  `import panda3d` = 1.10.16; `import breach.app` = ok.
- Real GLX render (offscreen buffer): engine Panda3D, output_type
  glxGraphicsBuffer, renderer llvmpipe (LLVM 21.1.8), Mesa 4.5, 960x540.
- Real GLX window + X11/XTest input: FLIGHT_RUNTIME_PASS (see controls below).
- Clean F10 quit (process exits before --frames; no RUNTIME_PASS emitted).

## 1. Automated test suite — PASS
`runtime/bin/python3.12 -m unittest discover -s tests` -> 165 tests, OK (3.63s).
Coverage maps directly to the contract: flight, damage/repair, weapons/hit
resolution, enemies/capital, cockpit/HUD/radar, effects, audio, synth,
mission/balance, foundation.

## 2. Linux-runnable real-time 3D space combat — PASS
Source: src/breach/app.py (FixedStepper 60 Hz pipeline
enemies -> weapons -> damage -> mission -> effects; FlightCamera; build_scene).
Runtime: 520-frame scripted XTest flight, 495 simulation ticks, 23 input
events, 1 mouse event, peak speed 26.6 m/s, brake stop 0.0 m/s, camera anchor
error 0.0 (eye fixed inside the hull every frame). Deterministic 30/60/144 Hz
frame-interval independence covered by tests (test_flight, test_weapons,
test_enemies determinism tests).

## 3. First-person cockpit view — PASS
Source: src/breach/cockpit.py CockpitSystem._build_cockpit_frame (dashboard,
left/right sills, top-bar, left/right struts, all camera-attached, unlit);
src/breach/camera.py FlightCamera (EYE_OFFSET (0,0,0.65) inside hull envelope,
camera reparented to ship root).
Runtime: cockpit steel colour (0.30,0.33,0.40) sampled in every capture —
291,154 px (healthy frame 10), 150,606 px (damaged frame 60), 280,143 px
(radar frame 14). Camera never leaves the hull (anchor error 0.0).

## 4. HUD — ship status, instruments, targeting — PASS
Source: src/breach/cockpit.py build_hud_state / hud_lines / _build_hud
(left panel: hull %, per-subsystem %, throttle, speed, weapon, heat, lock,
fire-solution; right panel: target hull/subsystems/repair/range; bars;
red alert border), _tint_gauge (4 instruments tinted by live state).
Runtime sampled HUD pixels (code-level, exact flat colours):
  healthy frame 10: text_fg 1103, gauge_ok 18108, alert_red 0 (no alert),
                     text_good 102 (green fire-solution bracket).
  burning frame 70: alert_red 17731 (red border), gauge_crit 143 (failed
                     weapons instrument), text_good 101, smoke 202647.
  damaged frame 60: alert_red 21634, gauge_crit 4016 (failed engine
                     instrument).
Tests: test_cockpit.py (HUD state binding driven by real DamageSystem/
WeaponsSystem/FlightSystem snapshots; alert flips on damage; per-subsystem
actionable lines).

## 5. 3D spherical radar — PASS (with a scope caveat)
Source: src/breach/cockpit.py radar_blip / build_radar (world -> body ->
azimuth/elevation/range), _draw_blip (amber dot, vertical stem: green up /
magenta down, reach scales with sensors_multiplier).
Runtime (radar frame 14, code-level sample + trace):
  radar_blip 77 px (amber), radar_above 69 px (green), radar_below 3 px
  (magenta), radar_rim 207 px. Trace blips: fighter-1 elev 0.0/vertical 0.0
  (no stem); scout-low elev -14.9 deg, above -15.6 m, vertical -0.26 (magenta);
  scout-high elev +23.4 deg, above +25.4 m, vertical +0.40 (green).
  Destroyed player -> sensors_multiplier 0 -> radar blind (no blips): observed
  in damaged frame 60, matching build_radar's reach clamp.
Tests: test_cockpit.py (azimuth/elevation/range/above-below, sensors-reach
scaling, nearest-first).
CAVEAT (truthful, not glossed): the radar is a legible polar "spherical" scope
with an elevation stem, NOT a true 3D-rendered sphere (second orthographic
view). Root contract says "3D spherical preferred" — direction+distance+
above/below are all conveyed on one display, but a literal 3D sphere is not
rendered. Flagged; subjective legibility is node 12's call.

## 6. Accumulated damage + smoke-then-burn degradation — PASS
Source: src/breach/damage.py DamageSystem (healthy >0.66, smoking <=0.66,
burning <=0.33, destroyed ==0); src/breach/effects.py _process_damage_states
(smoking -> smoke only; burning -> smoke + fire; fire_loop while burning).
Runtime: deterministic damage timeline captures capital healthy -> smoking
(frame 19, smoke only) -> burning (frame 31, smoke+fire, fire_loop True) ->
explosion (frame 35, 110 explosion particles). Code-level frame differencing
(my own, threshold 30/channel): smoking->burning 191 px; smoking frame smoke
colour directly sampled (40 px). effects_runtime differencing agrees
(smoke_changed 40, burning_changed 191). Tests: test_damage (thresholds),
test_effects (smoking emits smoke no fire; burning emits fire + loop).

## 7. Per-system damage (thrusters/turning/weapons) — PASS
Source: src/breach/damage.py flight_performance (ENGINE -> thrust+turning
multiplier), _view (weapons_multiplier, sensors_multiplier);
src/breach/weapons.py weapons_multiplier + _firing_blocked (destroyed WEAPONS
blocks fire), classify_subsystem_hit (front=WEAPONS, rear=ENGINE, middle=
SENSORS, half the hull damage). Applied on every resolved hit, so sustained
combat degrades subsystems (SUBSYSTEM_DAMAGE_FRACTION 0.5).
Runtime: combat hit run — 67 shots / 65 hits reduced capital hull 3000->2220
and its ENGINE subsystem to 0.35 (0.35 thrust/turning multiplier) through
ordinary fire. Scripted run B: ENGINE 40 HP removed -> engine_multiplier 0.0
and red engine gauge + alert border. Tests: test_damage
(test_engine_damage_impairs_live_thrust_and_turning, test_destroyed_engine_
freezes_live_flight), test_weapons (test_weapon_impairment_reduces_damage,
test_destroyed_weapons_cannot_fire), test_enemies (engine/weapons impairment
mirrors on enemy ships).

## 8. In-place repair of a crippled ship — PASS
Source: src/breach/damage.py _advance_repair / _resolve_player_intent (repair
one selected subsystem at repair_rate toward 0.70 cap, gated to <=2 m/s, any
hit interrupts the interval without undoing restored HP); player holds R.
Runtime: repair timeline captures fighter-2 with destroyed ENGINE entering
repair — green glow particles + repair_start cue, subsystem restored with
repair_progress 0.05 -> 0.12 across frames (trace), repair_complete cue on
cap; frame differencing explosion->repair-glow 1200 px (effects_runtime
glow_changed 1046). Mission run demonstrates defeat -> N restart to full
health. Tests: test_damage (repair progression, low-mobility gate,
interruption, continuous-damage-block), test_enemies (repair entry brakes +
stops firing; interrupted/aborted under fire), test_mission (enemy repair
exploitable; player repair risky + interruptible). Crippled-while-repairing
vulnerability is enforced in source (repair_locked blocks fire, brakes to
stop) and tested.

## 9. Capital ships require sustained fire — PASS
Source: src/breach/damage.py CAPITAL_PROFILE (hull 3000 = 30x fighter,
subsystem 600 = 15x); src/breach/enemies.py spawn_capital (anchored, turret
battery via shared fire boundary); src/breach/weapons.py TURRET_SPEC (40 dmg,
1.2s cooldown); src/breach/mission.py MissionBalance (capital_ttk_window
35-75 s, validated against realized specs).
Runtime: combat hit run — 65 resolved cannon hits moved the capital only from
3000 -> 2220 HP (0.74 hull), i.e. a long sustained attack; a single torpedo
(90) cannot one-shot it (test_no_one_shot_rules, test_capital_withstands_
single_shot). mission_sim full-mission autopilot reaches victory through
ordinary fire (committed in node 8/9). Tests: test_enemies
(test_capital_requires_prolonged_attack, test_capital_repairs_vulnerably_and_
stops_shooting), test_mission (TTK windows).

## 10. Weapons, explosions, and sound — PASS
Source: src/breach/weapons.py (cannon/scatter/torpedo + capital turret,
cooldown + heat gating, projectile sweep vs oriented box); src/breach/effects.py
(muzzle flash, impact sparks, scale-appropriate explosions: capital ~3x
fighter fireball + distinct cues); src/breach/audio.py + synth.py (10
procedural WAV cues, MIT, regenerable).
Runtime: hit run fired 67 / hit 65 / miss 0; miss run fired 67 / hit 0 /
miss 35 (hitbox resolution). Live effects run spawns fire/impact/destroy
particles during combat. Audio: unmuted OpenAL "wave" capture of the running
game -> emitted-audio.wav 2ch 48 kHz 7.13 s, peak 18575, rms 2396.8, 53.5%
non-zero samples (NON_SILENT), cue tally {cannon 3, impact 3, fire_loop 1,
explosion_capital 1, explosion_fighter 1, repair_start 1, repair_complete 1}.
Tests: test_weapons (32), test_effects, test_audio, test_synth (capital
explosion longer/heavier; determinism; valid PCM).

## Honest gaps / flags (none hidden)
1. Radar is a polar scope with elevation stem, not a literal 3D sphere
   (feature 5 caveat). Direction/distance/above-below are all conveyed.
2. Fire/explosion/glow particles are alpha-flickered transparent billboards,
   so my exact-flat-colour probe detects smoke and the 2D HUD/radar colours
   but not fire/explosion/glow directly; those are instead verified by trace
   particle counts + frame differencing (all reproduced independently here).
3. Smoke spawns at the player's own position and can occlude the cockpit
   gauges in-camera (cockpit steel pixels drop 291154 -> 36 between healthy
   and burning frames). 2D HUD/radar unaffected. Presentation note for
   node 12, not a logic defect (documented node-7 concern, re-confirmed).
4. Enemy hitboxes registered at spawn are not updated as fighters move
   (node-5 concern); the targeting bracket tracks the live visual pose, which
   is what the player sees.
5. No human playtest, no physical-GPU/native-Wayland/Windows evidence, no
   audible physical playback — all explicitly deferred to later nodes.

## Verdict
Every root-contract feature is traced to source + automated test + runtime
evidence. The distribution extracts and launches on Linux, the 165-test suite
passes on the bundled runtime, and the release candidate is a genuine,
deterministic 3D space-combat build with cockpit/HUD/radar, first-class
accumulated damage + repair, capital endurance, and weapons/explosions/sound.
The only contract caveat is the radar being a polar scope rather than a true
3D sphere (flagged above). No feature is claimed without matching evidence.

Evidence layout (committed alongside this report):
  distribution-validation-summary.txt, unit-tests.log
  sampled-pixels/  (probe JSON + graphics_sample.txt)
  frame-dumps/     (hud healthy/burning/damaged, radar elevation, gfx,
                    combat hit/miss, effects progression smoking/burning/
                    capital-explosion/fighter-explosion/repair, mission)
  runtime-summaries/ (controls/combat/effects/mission summary JSON)
  audio/emitted-audio.wav
  pixel_probe.py (the code-level sampler used above)
