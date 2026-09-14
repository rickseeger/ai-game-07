# Node 4: weapons, projectile kinematics and hit resolution

## Scope and ownership

`src/breach/weapons.py` is the sole owner of projectile state, fire cadence,
cooldown/heat resource accounting, target lock/aim feedback, deterministic
segment-vs-oriented-box hit detection, and `DamageEvent` emission. It never
reduces health directly: every resolved ship hit is routed through the injected
damage service's `queue()` as a `DamageEvent`, so sustained combat accumulates
in the shared `DamageSystem` exactly like scripted damage (no bypass). It is
pure and deterministic (no randomness, no NodePaths, no window).

## Weapon choices

Three accessible weapons (cycle with Q) with distinct cadence, damage, projectile
speed and range so they feel different:

| key | kind        | damage | cooldown | projectile | range  | heat/shot | pellets |
|-----|-------------|--------|----------|------------|--------|-----------|---------|
| —   | cannon      | 12     | 0.15 s  | 160 m/s    | 800 m  | 6         | 1       |
| —   | scatter     | 8      | 0.50 s  | 120 m/s    | 500 m  | 12        | 5       |
| —   | torpedo     | 90     | 1.40 s  | 50 m/s     | 1200 m | 40        | 1       |

Cannon is the rapid low-damage dogfight weapon; scatter is a short-range shotgun
(5 pellets at a 0.12 rad deterministic spread); torpedo is the slow heavy hit
against capital ships. All values are `WeaponSpec` configuration, never constants
buried in HUD logic.

## Firing model (resources and impairment)

Two declared resources gate every shot, both fully deterministic:

- **Cooldown** (seconds per weapon): a shot is refused until the per-weapon
  cooldown has elapsed. Cadence is therefore `1 / cooldown` at full capability.
- **Heat** (`0..heat_capacity`, cooling at `cool_rate` heat/s): firing adds
  `heat_per_shot`; a shot is refused when it would push heat over capacity.
  Sustained full-auto fire is throttled by cooling rather than a hard lockout.

Weapon impairment reads the shared damage model: the firing ship's WEAPONS
capability (linear in subsystem health) scales damage per shot, and a destroyed
WEAPONS subsystem (capability 0) blocks firing entirely. The repair entry request
also blocks firing at this boundary (`flight_performance().repair_locked`), so a
repairing ship cannot shoot. Projectiles are spawned at the ship's muzzle (2 m
forward of centre) along its aim direction (+Y forward), so the aim stick and
ship orientation define the fire direction.

## Hit resolution

Each fixed tick a projectile is swept as a line segment (previous -> next position,
`speed * dt` long) and tested against every target's **oriented box** in the box's
local frame (slab method), so rotated ships are hit correctly. The nearest
intersection along the segment wins. On a ship hit the projectile is removed and a
`DamageEvent(source, target, damage, subsystem=None)` is queued to the damage
service; hull damage accumulates there. Projectiles that travel `max_range`
without hitting a ship are counted as misses. Environment targets block and remove
the projectile without emitting damage.

**Ownership filtering**: a projectile never hits its owner's faction, so friendly
fire and self-hits are impossible and enemy projectiles hit only the player's
faction. This is the projectile/owner collision separation required by
docs/INTEGRATION.md.

## Aim feedback (reticle / target lock)

Tab cycles target lock among enemy targets. `aim_snapshot()` exposes a frozen
`AimState` for the cockpit: current weapon, `ready` (off cooldown and not
blocked/overheated), cooldown remaining, heat/capacity, `overheated`,
`weapons_multiplier`, `firing_blocked`, `locked_target`, `lock_in_range`,
`lock_in_front`, `on_target` (the forward ray actually intersects the locked
target's box within range — the reticle fire solution), plus projectiles/fired/
hits/misses counters. The app colours the reticle green on a fire solution and
yellow on a lock.

## Public boundary

    WeaponsSystem.fixed_update(dt, controls) -> None
    WeaponsSystem.fire(entity_id, origin: Vec3, direction: Vec3, weapon=None) -> bool
    WeaponsSystem.add_target / remove_target / set_target_pose / set_faction
    WeaponsSystem.select_weapon(entity_id, kind) / weapons_multiplier(entity_id)
    WeaponsSystem.aim_snapshot() -> AimState

`fire()` is the shared entry point for both the player's held fire input and
future enemy AI: it enforces cooldown, heat and impairment/repair restrictions
and returns whether a projectile was actually spawned. The player's fire level is
routed through it inside `fixed_update`.

## Live integration

`app.py` constructs `WeaponsSystem` (damage service + `flight.snapshot` as the
pose provider), registers the default enemy fighters + capital as targets (from
`scene.combat_targets()`), spawns their damage profiles, and runs it in the
`FixedStepper` pipeline **before** damage, so weapons emit `DamageEvent`s that the
shared `DamageSystem` consumes the same tick. The trace records `weapons`,
`weapon_hits` and `enemy_health` on every frame; `--fire-script` (JSON
`{tick, fire}` toggles) drives deterministic fire for offscreen combat runs, and
`--no-combat-targets` skips target registration for the miss run.

## Verification

- `tests/test_weapons.py` (32 tests): cadence/cooldown, projectile kinematics,
  hit and miss resolution, ownership filtering (ally/enemy/friendly-fire), heat
  and cooldown resources, damaged/destroyed/repair-impaired weapons, weapon
  cycling, target-lock/aim-feedback state, moving-target hit/miss, durability
  (no one-hit kills), and frame-interval determinism.
- `tools/weapons_sim.py`: independent headless combat scenarios (miss, track +
  sustain against a moving fighter, no-one-hit-kill for fighter/capital, and
  30/60/144 Hz + jittered frame-interval determinism).
- `tools/weapons_runtime.py`: real offscreen Panda3D launches with scripted fire —
  a hit run (sustained fire reduces the capital's hull through DamageSystem) and a
  miss run (`--no-combat-targets`) — validated from the deterministic trace plus
  framebuffer captures. No image perception is used.

Full rerun (fresh `.venv-verify`, real X11/GLX via Xvfb, llvmpipe):

    python3 tools/headless.py --venv .venv-verify --output artifacts/weapons-release

runs unit tests, window/offscreen smoke, the 520-frame XTest flight, the 30/144 Hz
independent pilots, `damage_runtime.py`, `weapons_sim.py` and
`weapons_runtime.py`. All exit 0.
