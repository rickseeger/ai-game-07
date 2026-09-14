# Node 3: accumulated damage and repair simulation

## Scope and ownership

`src/breach/damage.py` is the sole owner of hull/sub-system health, repair
progress, and ship condition. It is pure, deterministic simulation (no
NodePaths, no window, no randomness) and is injected into `FlightSystem` as the
`FlightDamageService`, so real damage-derived performance drives the live ship
rather than a disconnected demo.

Every ship has a hull HP pool and one HP pool per `Subsystem`
(ENGINE / WEAPONS / SENSORS). Snapshots normalize to [0, 1].

## Model

Hull damage drives condition through explicit thresholds:

    healthy  ->  smoking  ->  burning  ->  destroyed (hull == 0)
    hull>0.66    <=0.66       <=0.33        == 0

Subsystem capability is linear in subsystem health: a subsystem at fraction h
delivers h of its capability, and at 0 it has *failed* (capability fully lost).
ENGINE drives both thrust and turning (main + attitude thrusters); WEAPONS
drives weapons output; SENSORS drives sensor reach (the future weapons node
reads the same multiplier). Destroyed hull zeroes all capability regardless of
remaining subsystem HP.

Repair restores one selected subsystem at `repair_rate` HP/s up to
`repair_cap` (70%), and requires the ship to be at/below `repair_max_speed`
(a tactically vulnerable low-mobility/stationary state) and free of incoming
damage on the current tick. A hit interrupts the current uninterrupted repair
interval without undoing already-restored HP; sustained incoming damage
therefore prevents a repair from ever completing.

Capital ships use a much larger profile, so they withstand substantially
longer sustained attack than fighters (30x hull, 15x subsystem HP by default).

## Profiles

    FIGHTER_PROFILE: hull 100, subsystem 40, repair 4 s to 70%, max speed 2 m/s
    CAPITAL_PROFILE: hull 3000, subsystem 600, repair 30 s to 70%, max speed 0.5 m/s

All values are `DamageProfile` configuration, not constants buried in HUD
logic. `repair_rate` is derived: `subsystem_max_hp * repair_cap / repair_duration`.

## Public boundary

    DamageSystem.queue(DamageEvent | RepairIntent) -> None
    DamageSystem.fixed_update(dt, controls) -> None      # consumes queue once, in order
    DamageSystem.snapshot(entity_id) -> HealthView       # immutable read-only copy
    DamageSystem.flight_performance(entity_id) -> FlightPerformance
    DamageSystem.capability(entity_id, subsystem) -> float
    DamageSystem.subsystem_health(entity_id, subsystem) -> float
    DamageSystem.spawn(entity_id, profile) / despawn(entity_id)

`HealthView` (frozen dataclass): hull, hull_hp, state (ShipState), subsystems
(immutable mapping), repairing, repair_progress, and engine/turning/weapons/
sensors multipliers. `to_dict()` gives a JSON-ready form; the trace records it.

Fixed order per tick (matches docs/INTEGRATION.md): flight reads prior-tick
damage, then `DamageSystem.fixed_update` applies queued damage/repair and
advances repair progress. The player's R intent is edge-triggered (press/release/
switch), so held-R semantics compose with queued `RepairIntent` events.

## Live integration

`app.py` constructs `DamageSystem` (speed provider reads the live flight
velocity), spawns the player with `FIGHTER_PROFILE`, injects it as the flight's
damage service, and runs it in the `FixedStepper` system pipeline after flight
every fixed tick. The HUD shows live hull/state and repair progress; the trace
records `health` on every simulation tick and frame.

`--damage-script <json>` injects scripted `DamageEvent`/`RepairIntent` entries
keyed by tick (see `tools/damage_runtime.py` for the format). Each entry is
queued at the listed tick and applied the following fixed tick (the documented
one-tick latency). This is a debug/validation hook, not gameplay.

## Verification

Deterministic unit tests: `tests/test_damage.py` (25 tests) cover thresholds,
capability loss, repair progression, low-mobility gating, interruption /
incoming-damage-during-repair, terminal destruction, capital-vs-fighter
durability, event ordering, snapshot immutability, and live-flight integration
(FlightSystem + DamageSystem + FixedStepper lockstep).

Independent rerun (fresh `.venv-verify`, real X11/GLX via Xvfb, llvmpipe):

    python3 tools/headless.py --venv .venv-verify --output artifacts/damage-release

runs unit tests, window/offscreen smoke, the 520-frame XTest flight, the
30/144 Hz independent pilots, and `tools/damage_runtime.py` (scripted damage ->
live health/state/repair transitions: healthy -> smoking -> burning -> destroyed,
engine zeroed then repaired to 0.70 then zeroed at destruction). All exit 0.
No image perception is used: assertions read the deterministic trace.
