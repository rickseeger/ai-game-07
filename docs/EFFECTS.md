# Node 7: integrated combat effects and sound driven by real damage events

## Scope and ownership

`src/breach/effects.py` owns every disposable combat particle and audio cue. It
is driven exclusively by the *real* events/state the shared simulation publishes
each fixed tick — it never polls, and it never invents a cosmetic timer decoupled
from damage. `src/breach/audio.py` owns playback and the runtime cue log;
`src/breach/synth.py` owns the procedural sound generation. Effects write only
their own particle/quad state and read-only snapshots; they never mutate flight,
weapon, enemy or damage state (the one exception is clearing the weapons effect
event buffers it consumed).

## Event bindings (authoritative, not polling)

- `weapons.fired_this_tick` -> a muzzle-flash particle and a per-weapon firing
  sound (cannon/scatter/torpedo/turret).
- `weapons.impacts_this_tick` -> impact sparks at the resolved hit point plus an
  impact sound.
- `damage.destroyed_this_tick` -> a scale-appropriate explosion: a capital ship
  produces ~3x the fireball particles of a fighter and a distinct, longer and
  deeper explosion cue, so scale reads as consequential.
- `damage.snapshot(e).state` (`healthy/smoking/burning`) -> persistent smoke
  while smoking, smoke + fire while burning, scaled per ship half-extent, so
  degradation stays visible *before* destruction. A single looping fire crackle
  plays while any ship burns (started/stopped on the state edge).
- `damage.snapshot(e).repairing` (`None <-> Subsystem`) -> repair start/complete
  sound cues plus a soft green glow while a ship is actively repairing.

Two layers keep it testable with no window and no vision: pure window-free
planning (`Particle` + `spawn_*` + `EffectsSystem.fixed_update`) asserted in unit
tests, and a thin billboard renderer (`EffectsSystem.present`) that writes the
particle state into flat, depth-tested, camera-facing colour quads. Particles use
exact flat colours (smoke gray, fire orange/yellow, muzzle near-white, impact
amber, repair green) so automated pixel sampling can verify them.

## Boundedness and audio controls

A global `MAX_PARTICLES` cap with per-emitter rate accumulators and finite
lifetimes bounds every effect. Audio is routed through `AudioEngine`, which
honours `--mute`, `--master-volume` (clamped to [0,1]) and distance falloff, and
degrades gracefully (logs-but-never-plays) when no device is present. Sounds load
lazily and a failed play never breaks the simulation loop.

## Sound assets (redistributable)

`src/breach/audio/*.wav` are original procedural synthesis (no third-party
recordings), regenerable byte-for-byte from `tools/gen_audio.py`, with a
provenance `manifest.json` (path, duration, peak, SHA256). See ASSETS.md.

## Verification

- `tests/test_effects.py`: event-binding and effect-lifecycle assertions — a
  player shot -> muzzle flash + firing sound (and the fire buffer is consumed,
  not leaked); a resolved hit -> sparks + impact sound; destruction -> a
  capital-sized explosion larger than a fighter's; smoking -> smoke only,
  burning -> smoke + fire + fire loop, healthy -> nothing; repair -> start/
  complete cues + green glow; particle cap, aging-out, and same-seed determinism.
- `tests/test_audio.py`: mute/master-volume controls, graceful no-device
  degradation, loop bookkeeping, and that every committed asset resolves.
- `tests/test_synth.py`: determinism, valid 16-bit mono PCM, non-silence,
  distinct lengths, and that a capital explosion is longer and heavier than a
  fighter's; committed assets regenerate byte-identical.
- `tools/effects_runtime.py`: real offscreen Panda3D launches that drive the full
  healthy -> smoking -> burning -> explosion sequence plus repair recovery, and
  validate with *code-level* framebuffer differencing (changed pixels appear
  exactly in the smoking/burning/explosion/repair states, a capital explosion is
  larger than a fighter's) — no image perception. A second unmuted run captures
  the running game's *mixed audio* through the OpenAL Soft `wave` backend into a
  WAV, checked non-silent and correlated with the game's own cue tally. A third
  live run confirms fire/impact/destruction effects fire during combat.

Full rerun (fresh venv, real X11/GLX via Xvfb, llvmpipe):

    xvfb-run -a .venv/bin/python tools/effects_runtime.py --output artifacts/effects-runtime

Evidence: artifacts/effects-runtime/{progression,audio,live} with frame dumps,
traces, reports and the captured emitted-audio.wav. Subjective quality is
deferred to the human playtest node; audible playback through a physical device
still needs a listening human.
