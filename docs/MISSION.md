# Combat mission loop and damage-and-repair tuning (node 8)

Node 8 of tree G14. This layer turns the validated sibling systems (flight,
damage, weapons, enemies, effects, cockpit) into an accessible, replayable
sortie with a clear objective, escalating encounters, a capital-ship climax,
comprehensible victory/defeat, and a one-action restart. It also tunes the
damage-and-repair tension against explicit numeric targets, verified by
automated tests and real-renderer lifecycle runs -- never by a vision model.

## What the mission is

A finite, escalating sortie (no infinitely replenishing waves):

| Wave | Enemies | Objective (HUD banner) |
|------|---------|------------------------|
| 1 | one light fighter | "Destroy the patrol fighter" |
| 2 | two fighters | "Hostiles inbound - clear the escort" |
| 3 | three fighters | "Fighters closing - hold them off" |
| 4 | the interdiction frigate (capital) | "Destroy the interdiction frigate" |

The frigate spawns only after the escort waves are cleared, so a brand-new pilot
is not hounded by the capital turret from second zero. The player starts at
(0,-15,4) facing a single fighter ~55 m ahead -- readable, approachable, and an
immediate, safe first kill.

Victory: destroy the frigate. Defeat: the player's hull reaches zero. On either
end state the world freezes, a banner reports the outcome, and pressing N
restarts from wave 1 with the player restored to full health. Escape pauses and
resumes at any time (existing node-2 control); restart and pause are driven
through the same input table as every other key.

## Damage-and-repair tension (the tuned loop)

The repair loop only matters if subsystems actually degrade in ordinary combat.
Node 8 therefore makes every resolved ship hit degrade a subsystem in addition
to hull damage:

- The subsystem is chosen by WHERE the hit lands in the target's local frame:
  front third -> WEAPONS, rear third -> ENGINE, middle -> SENSORS.
- The subsystem amount is SUBSYSTEM_DAMAGE_FRACTION (0.5) of the hull damage.
- Deterministic (no randomness), so a head-on trade blunts both ships' guns.

Consequences, verified by tests/test_mission.py and tools/mission_sim.py:

- Shooting an enemy head-on blunts its WEAPONS, reducing its damage output; a
  crippled fighter then brakes to a stop and tries a vulnerable, hit-interruptible
  repair you can punish (the "exploit the repair window" moment).
- Tail hits slow an enemy's ENGINE.
- The player's own subsystems degrade under sustained fire, forcing the
  "keep fighting impaired or stop to repair while exposed" decision. Hull is
  never repairable; repair restores only the selected subsystem toward its cap
  over the profile's repair_duration, and only below repair_max_speed, with any
  hit interrupting the current interval without undoing already-restored HP.

### Numeric tuning targets (committed in data/mission_balance.json)

Sustained-DPS time-to-kill (heat-limited continuous fire) windows:

- Fighter: 1.2 - 4.0 s (realized ~1.7 s cannon-only sustained).
- Capital: 35 - 75 s (realized ~50 s cannon-only sustained).

Repair vulnerability windows (time a crippled ship sits stationary, holding
fire, restoring a subsystem to its cap):

- Fighter: 2 - 6 s (profile repair_duration 4.0 s).
- Capital: 20 - 40 s (profile repair_duration 30.0 s).

No-one-shot rules: a single torpedo (90 damage) must not kill a fighter (100 HP)
or a capital (3000 HP). The balance test computes the realized values from the
actual weapon specs / damage profiles and fails if they drift outside the
committed windows, so tuning cannot silently diverge from the code.

## Verification (no vision model)

- tests/test_mission.py: 14 deterministic tests covering the mission lifecycle
  (approachable start, escalation, victory, defeat, restart, finite waves), the
  balance targets, and the repair tension (sustained combat degrades subsystems
  and opens repair; enemy repair is exploitable; player repair is interruptible).
- tools/mission_sim.py: headless deterministic playthroughs. A strafing
  autopilot plays the FULL mission (all waves -> capital) using only ordinary
  weapons fire and flight movement -- no scripted damage, no invulnerability --
  and reaches victory. Also demonstrates the exploitable enemy repair and the
  risky, interruptible player repair.
- tools/mission_runtime.py: launches the real Panda3D app offscreen and validates
  the runtime lifecycle from the trace -- defeat via ordinary combat, scripted N
  restart (back to wave 1, full health), scripted Escape pause/resume, and
  code-level PNG framebuffer sampling of the captured frames.

Subjective "is it fun / does the tension feel right" remains the human
playtester's call at node 10, not this node's.

## Tuning observations from automated play (recorded)

The deterministic autopilot playthrough (tools/mission_sim.py) surfaced two
node-5 enemy-AI defects that made the mission unwinnable or soft-lockable, and
both were fixed as part of this node's tuning:

1. Enemy throttle was being treated as an absolute target when the flight model
   treats controls.throttle as a rate of change. Result: every fighter pinned at
   full speed, overshot a head-on player, and could not slow down to turn --
   fleeing kilometres away and soft-locking the mission (a static player could
   never catch it). Fix: enemies.EnemySystem._throttle_rate_to drives the
   throttle setting toward the standoff target with a proportional rate, so a
   misaligned fighter decelerates and turns instead of running away.

2. The exactly-opposed steering case returned a hard PITCH (nose up) instead of
   the documented hard YAW, so a fighter that passed a head-on player looped
   vertically instead of turning back to re-engage. Fix: return yaw in that
   branch (the node-5 tests still pass).

These are recorded in the committed code and covered by the new mission tests
(sustained combat opens repair; the full-mission autopilot reaches victory).
The numeric balance targets in data/mission_balance.json remain the source of
truth for TTK/repair windows, and the tests fail if the realized specs drift.

One visual concern for the human playtest (not verifiable without vision): the
node-7 smoke/fire particles spawn at the player ship's own position, so while
the player is smoking/burning they can occlude the cockpit gauges in-camera.
The 2D HUD (alert border, target panel, radar) is unaffected. This is a
presentation note for node 10, not a logic defect.
