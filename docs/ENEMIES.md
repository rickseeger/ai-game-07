# Node 5: enemy fighters and the dangerous capital ship

## Scope and ownership

`src/breach/enemies.py` owns every non-player ship's pose, steering, attack
decisions and repair decisions. It is pure deterministic simulation (no
NodePaths, no window, no randomness) and reuses the SAME flight, weapon,
subsystem-impairment and repair rules that govern the player — there is no
separate enemy health, recovery or firing model.

## What reuses what

- **Flight** — every enemy fighter is a real `FlightSystem` sharing the player's
  `FlightTuning` and the shared `DamageSystem` as its `FlightDamageService`. So
  ENGINE damage impairs an enemy's thrust and turning exactly as it impairs the
  player's (one-tick latency included), and destruction zeroes its performance.
- **Weapons** — enemies attack by requesting shots through
  `weapons.fire(entity_id, origin, direction, weapon)`, the same cadence/heat/
  impairment boundary the player uses. WEAPONS damage scales an enemy's per-shot
  damage; a destroyed WEAPONS subsystem blocks its fire; repair engagement blocks
  its fire (`repair_locked`). No bypass.
- **Damage/repair** — a crippled enemy enters a vulnerable repair by emitting a
  `RepairIntent` through the shared `DamageSystem`: it brakes to a stop, stops
  shooting, and restores one subsystem at the same timed, hit-interruptible rate
  the player uses. Entry and exit are explicit state transitions recorded in the
  trace — never silent or instantaneous hidden recovery.

## The AI (deterministic)

Steering decomposes the world rotation axis `forward x desired` into the flight
adapter's yaw (nose right) and pitch (nose up); a directly-opposed nose breaks
the symmetry with a hard yaw so a fighter can turn around. Throttle is scaled by
nose alignment so a fighter tightens its turn instead of orbiting at high speed.
Fire opens inside an engagement range and a nose-alignment cone. All tuning lives
in `EnemyTuning`, not buried constants.

Repair is a two-state decision per ship: a ship whose WEAPONS/ENGINE/SENSORS
health falls below a threshold (and which has not been hit in the last
`hit_cooldown` seconds) enters repair on the most-damaged subsystem; it exits
when the subsystem reaches the repair cap or after `max_repair_interruptions`
hits (abort under sustained fire). A completed repair resumes combat
immediately; an aborted one pauses before reconsidering (no thrash).

## The capital

`spawn_capital` places one anchored hull with `CAPITAL_PROFILE` (3000 hull /
600 subsystem) and a turret battery (`WeaponKind.TURRET`, added to the catalog as
`WEAPONS_WITH_TURRET` — the player still cycles only CANNON/SCATTER/TORPEDO).
The turret uses the exact same fire boundary, so WEAPONS impairment scales/blocks
it and repair silences it. Its hull pool means destruction requires ~40–50 s of
sustained cannon fire (deliberately long); the torpedo is the anti-capital
weapon. The capital is stationary (an interdiction frigate); its danger comes
from turret return fire (40 HP/shot at ~1.2 s cadence) rather than movement.

## Public boundary

    EnemySystem.fixed_update(dt, controls) -> list[destroyed_ids]
    EnemySystem.spawn_fighter(entity_id, position, orientation=..., weapon=...) -> entity_id
    EnemySystem.spawn_capital(entity_id, position, orientation=...) -> entity_id
    EnemySystem.snapshot(entity_id) -> ShipView
    EnemySystem.speed_of(entity_id) -> float          # for the damage speed gate
    EnemySystem.is_alive(entity_id) / is_capital(entity_id)
    EnemySystem.ai_snapshot() -> list[dict]          # trace-ready read-only state
    EnemySystem.last_enemy_events                    # (id, kind, detail) this tick

`EnemyTuning` is the configuration dataclass; `look_quat` maps a forward vector
to a Panda HPR orientation for spawns.

## Live integration

`app.py` constructs the `EnemySystem` with the player's `flight.snapshot` as its
pose provider (so the player is a hitbox enemy projectiles can hit, refreshed
every tick), wires the damage `speed_provider` to read each ship's live speed,
spawns three fighters and the capital, and runs `enemies -> weapons -> damage`
after flight in the fixed-step pipeline. Enemy meshes are driven from snapshots
and tinted by damage state (healthy -> smoking -> burning -> hidden) as a
code-level "visible weakening"; destroyed ships are removed from targeting and
hidden. The trace records `enemy_state`, `enemy_health` and `enemy_events`.

`--static-targets` restores the node-4 static hitboxes (no AI) for isolated
weapon tests; `--no-combat-targets` registers nothing.

## Verification

- `tests/test_enemies.py` (21 tests): pursuit, attack, engine/weapon impairment,
  repair entry/exit/abort/interruption, destruction, capital danger + prolonged
  attack + turret impairment + repair, and frame-interval determinism.
- `tools/enemies_sim.py`: independent headless scenarios for all of the above
  with assertion traces (no vision).
- `tools/enemies_runtime.py`: real offscreen Panda3D launches — an encounter
  (fighters maneuver + attack, capital returns fire, player fire damages the
  capital) and a repair run (crippled fighter/capital enter a vulnerable, timed
  repair and hold fire), validated from the deterministic trace plus code-level
  PNG framebuffer sampling.

Full rerun (fresh `.venv-verify`, real X11/GLX via Xvfb, llvmpipe):

    python3 tools/headless.py --venv .venv-verify --output artifacts/enemies-release

runs unit tests, the earlier flight/damage/weapons checks, `enemies_sim.py` and
`enemies_runtime.py`. All exit 0. No image perception is used.

## Balance note for the human playtest

A stationary player spawns ~100 m directly in front of the capital and is
destroyed in ~3 s by turret + fighter fire; turret arcs and initial spacing are
not yet telegraphed (that is the later mission/encounter node, per
INTEGRATION.md). Subjective feel, difficulty and visual polish are Rick's
playtest call.
