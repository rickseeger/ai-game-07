"""Single-window integration: hardware -> fixed simulation -> cockpit presentation."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
from panda3d.core import loadPrcFileData
from breach.input import BINDINGS, CONTROL_LINES, InputAdapter, InputSettings, control_lines
from breach.contracts import DamageEvent, RepairIntent, Subsystem
from breach.damage import DamageSystem, FIGHTER_PROFILE, CAPITAL_PROFILE
from breach.enemies import CAPITAL_HALF, FIGHTER_HALF, EnemySystem, look_quat
from breach.weapons import WEAPONS_WITH_TURRET, WeaponsSystem
from breach.mission import PLAYER_SPAWN, MissionSystem, MissionPhase, DEFAULT_WAVES, WaveSpec
from breach.cockpit import CockpitSystem
from breach.audio import AudioEngine
from breach.effects import EffectsSystem


def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--render-hz", type=int, default=60, help="render limiter only; simulation stays 60 Hz")
    p.add_argument("--offscreen", action="store_true")
    p.add_argument("--mute", action="store_true")
    p.add_argument("--master-volume", type=float, default=1.0,
                   help="master sound volume in [0, 1] (default 1.0)")
    p.add_argument("--frames", type=int, default=0, help="0 = run until F10/window close")
    p.add_argument("--capture", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--trace-dir", type=Path, help="fresh directory for real input/tick/frame evidence")
    p.add_argument("--capture-frames", default="20,100,140,190,240,300,410,470")
    p.add_argument("--mouse-sensitivity", type=float, default=0.012)
    p.add_argument("--invert-y", action="store_true")
    p.add_argument("--keyboard-only", action="store_true")
    p.add_argument("--damage-script", type=Path,
                   help="JSON array of scripted damage/repair events keyed by tick "
                        "(see docs/DAMAGE.md); queued at the listed tick and applied "
                        "the following fixed tick (60 Hz).")
    p.add_argument("--fire-script", type=Path,
                   help="JSON array of {tick, fire} toggles that force the held "
                        "fire level for scripted/offscreen combat runs.")
    p.add_argument("--target-script", type=Path,
                   help="JSON array of {tick} entries that trigger a Tab target-lock "
                        "edge on the listed fixed tick (for HUD/radar target checks).")
    p.add_argument("--key-script", type=Path,
                   help="JSON array of {tick, key, down} injected key events "
                        "for deterministic scripted restart/pause runs.")
    p.add_argument("--radar-spawns", type=Path,
                   help="JSON array of {id, position:[x,y,z]} to spawn extra enemy "
                        "fighters (for off-screen radar validation).")
    p.add_argument("--no-combat-targets", action="store_true",
                   help="do not register any enemy/capital hitboxes")
    p.add_argument("--demo-enemies", action="store_true",
                   help="spawn the node-5 enemy-AI lab layout (3 fighters + "
                        "capital at once, no mission escalation) for isolated "
                        "enemy tests")
    p.add_argument("--static-targets", action="store_true",
                   help="use node-4 static hitboxes (no enemy AI) for isolated weapon tests")
    return p


def load_damage_schedule(path):
    """Parse a JSON list into {tick: [DamageEvent | RepairIntent]} for scripting."""
    schedule = {}
    for entry in json.loads(Path(path).read_text()):
        tick = int(entry["tick"])
        if entry.get("type", "damage") == "repair":
            event = RepairIntent(entity_id=entry.get("target_id", "player"),
                                 subsystem=Subsystem(entry["subsystem"]),
                                 active=bool(entry.get("active", True)))
        else:
            subsystem = entry.get("subsystem")
            event = DamageEvent(source_id=entry.get("source_id", "script"),
                                target_id=entry.get("target_id", "player"),
                                amount=float(entry["amount"]),
                                subsystem=Subsystem(subsystem) if subsystem else None)
        schedule.setdefault(tick, []).append(event)
    return schedule


def load_fire_schedule(path):
    """Parse a JSON list of {tick, fire} toggles into a sorted (tick, fire) list."""
    entries = [(int(e["tick"]), bool(e["fire"]))
               for e in json.loads(Path(path).read_text())]
    entries.sort()
    return entries


def load_target_schedule(path):
    """Parse a JSON list of {tick} entries into a set of target-lock ticks."""
    return {int(e["tick"]) for e in json.loads(Path(path).read_text())}


def load_key_schedule(path):
    """Parse a JSON list of {frame, key, down} into {frame: [(key, down), ...]}.

    Keys are frame-indexed (not tick-indexed) so they also fire while the fixed
    sim is frozen at a terminal end state (where the tick counter stops).
    """
    schedule = {}
    for e in json.loads(Path(path).read_text()):
        schedule.setdefault(int(e["frame"]), []).append(
            (str(e["key"]), bool(e["down"])))
    return schedule

def load_radar_spawns(path):
    """Parse a JSON list of {id, position:[x,y,z]} into a list of dicts."""
    return [dict(id=e["id"], position=tuple(float(v) for v in e["position"]))
            for e in json.loads(Path(path).read_text())]


def main():
    args = parser().parse_args()
    if args.frames < 0 or ((args.capture or args.report or args.trace_dir) and not args.frames):
        raise SystemExit("capture/report/trace require --frames > 0")
    if not 15 <= args.render_hz <= 240:
        raise SystemExit("--render-hz must be between 15 and 240")
    settings = InputSettings(sensitivity=args.mouse_sensitivity, invert_y=args.invert_y)
    displayed_controls = control_lines(settings, args.keyboard_only)
    capture_frames = {int(x) for x in args.capture_frames.split(",") if x}
    damage_schedule = load_damage_schedule(args.damage_script) if args.damage_script else {}
    fire_schedule = load_fire_schedule(args.fire_script) if args.fire_script else []
    target_ticks = load_target_schedule(args.target_script) if args.target_script else set()
    radar_spawns = load_radar_spawns(args.radar_spawns) if args.radar_spawns else []
    key_schedule = load_key_schedule(args.key_script) if args.key_script else {}
    if args.trace_dir:
        args.trace_dir.mkdir(parents=True, exist_ok=False)
    loadPrcFileData("flight", "window-title G14 - Breach Flight\nwin-size 960 540\nsync-video false\nclock-mode limited\nclock-frame-rate 60\nframebuffer-multisample false\nnotify-level info")
    loadPrcFileData("render-limiter", f"clock-frame-rate {args.render_hz}")
    if args.mute:
        loadPrcFileData("audio", "audio-library-name null")
    if args.offscreen:
        loadPrcFileData("window", "window-type offscreen")
    from direct.showbase.ShowBase import ShowBase
    from direct.gui.OnscreenText import OnscreenText
    from panda3d.core import (AmbientLight, DirectionalLight, WindowProperties,
                              ClockObject, Quat, Vec3)
    from breach.scene import box, build_scene, combat_targets
    from breach.flight import FlightSystem
    from breach.timing import FixedStepper
    from breach.camera import FlightCamera, EYE_OFFSET

    class FlightApp(ShowBase):
        def __init__(self):
            super().__init__()
            if not self.win or not self.win.isValid():
                raise RuntimeError("Renderer failed to create a valid output")
            self.disableMouse()
            self.setBackgroundColor(0.012, 0.021, 0.042, 1)
            self.camLens.setFov(78)
            self.camLens.setNearFar(0.1, 2000)
            self.controls = InputAdapter(settings=settings)
            self.flight = FlightSystem(position=PLAYER_SPAWN)
            self.damage = DamageSystem(player_entity_id=self.flight.entity_id)
            self.flight.damage = self.damage
            self.damage.spawn(self.flight.entity_id, FIGHTER_PROFILE)
            self.weapons = WeaponsSystem(player_entity_id=self.flight.entity_id,
                                         damage=self.damage,
                                         pose_provider=self.flight.snapshot,
                                         specs=WEAPONS_WITH_TURRET)
            self.enemies = EnemySystem(player_entity_id=self.flight.entity_id,
                                       damage=self.damage,
                                       weapons=self.weapons,
                                       player_pose_provider=self.flight.snapshot)
            self.damage.speed_provider = (
                lambda eid: self.flight.velocity.length()
                if eid == self.flight.entity_id else self.enemies.speed_of(eid))
            if args.demo_enemies:
                demo_waves = (WaveSpec("Demo encounter",
                                       fighters=("fighter-1", "fighter-2", "fighter-3"),
                                       capital=True),)
                demo_positions = {"fighter-1": (-23, 56, 7),
                                  "fighter-2": (21, 63, -5),
                                  "fighter-3": (27, 100, 19),
                                  "capital": (0, 85, 4)}
                self.mission = MissionSystem(
                    player_entity_id=self.flight.entity_id,
                    damage=self.damage, enemies=self.enemies, weapons=self.weapons,
                    player_pose_provider=self.flight.snapshot,
                    waves=demo_waves, spawn_positions=demo_positions)
            else:
                self.mission = MissionSystem(
                    player_entity_id=self.flight.entity_id,
                    damage=self.damage, enemies=self.enemies, weapons=self.weapons,
                    player_pose_provider=self.flight.snapshot)
            self.combat_ids = []
            self.enemy_set = set()
            if not args.no_combat_targets:
                if args.static_targets:
                    # Node-4 static hitboxes (no AI) for isolated weapon tests.
                    for target in combat_targets():
                        self.weapons.set_faction(target["id"], target["faction"])
                        self.weapons.add_target(target["id"], target["faction"],
                                                target["position"], target["half"],
                                                target["orientation"])
                        profile = (CAPITAL_PROFILE if target["id"] == "capital"
                                   else FIGHTER_PROFILE)
                        self.damage.spawn(target["id"], profile)
                        self.combat_ids.append(target["id"])
                else:
                    # Node-8 mission loop: escalating waves -> capital climax.
                    self.mission.start()
                    self.combat_ids = list(self.enemies.entities)
                    self.enemy_set.update(self.enemies.entities)
                    for entry in radar_spawns:
                        rpos = Vec3(*entry["position"])
                        aim = (self.flight.position - rpos).normalized()
                        self.enemies.spawn_fighter(entry["id"], entry["position"],
                                                   orientation=look_quat(tuple(aim)))
                        self.combat_ids.append(entry["id"])
                        self.enemy_set.add(entry["id"])
            self.audio = AudioEngine(
                loader=self.loader,
                sfx_manager=(self.sfxManagerList[0] if self.sfxManagerList else None),
                muted=args.mute,
                master_volume=args.master_volume)
            mission_entity_set = [eid for wave in DEFAULT_WAVES for eid in wave.enemy_ids()]
            self.effects = EffectsSystem(
                self.render, self.camera, self.damage, self.weapons,
                self.enemies, self.flight, audio=self.audio,
                tracked_entities=mission_entity_set + [self.flight.entity_id])
            self.stepper = FixedStepper(self.flight, systems=[self.enemies,
                                                              self.weapons,
                                                              self.damage,
                                                              self.mission,
                                                              self.effects])
            self.view = FlightCamera(self.render, self.camera)
            self.view.present(self.stepper.previous, self.stepper.current, 1)
            self.scene = build_scene(self.render)
            if not args.no_combat_targets and not args.static_targets:
                # Live enemies replace the node-2 staging combat meshes.
                for pattern in ("**/fighter-placeholder", "**/capital-hull",
                                "**/capital-deck", "**/capital-bridge",
                                "**/engine-pod", "**/engine-marker"):
                    for node in self.scene.findAllMatches(pattern):
                        node.hide()
            self._spawn_enemy_presentation()
            ambient = AmbientLight("ambient")
            ambient.setColor((0.4, 0.4, 0.45, 1))
            self.render.setLight(self.render.attachNewNode(ambient))
            key = DirectionalLight("key")
            key.setColor((0.9, 0.85, 0.75, 1))
            key_np = self.render.attachNewNode(key)
            key_np.setHpr(-30, -45, 0)
            self.render.setLight(key_np)
            # Single compact control hint; the cockpit HUD is the primary display.
            OnscreenText(text="Tab lock | Space fire | Q weapon | R repair | Esc pause",
                         pos=(0, -0.955), scale=0.038,
                         fg=(0.55, 0.66, 0.75, 1), bg=(.012, .021, .042, 0.6))
            OnscreenText(text="+", pos=(0, 0), scale=.045, fg=(.4, 1, .85, 1))
            self.pause_label = OnscreenText(text="", pos=(0,.3), scale=.06,
                                           fg=(1,.8,.4,1), mayChange=True)
            self.objective_banner = OnscreenText(text="", pos=(0, .82), scale=.052,
                                                 fg=(.9,.95,1,1), mayChange=True)
            self.objective_sub = OnscreenText(text="", pos=(0, .76), scale=.040,
                                              fg=(.6,.72,.82,1), mayChange=True)
            self.cockpit = CockpitSystem(self.render, self.camera, self.aspect2d,
                                         self.camLens, self.flight.entity_id,
                                         self.damage, self.weapons, self.enemies,
                                         self.flight)
            self.cockpit.present(1, self.stepper.current,
                                 self.damage.snapshot(self.flight.entity_id),
                                 self.weapons.aim_snapshot())
            self.frame_count = 0
            self.input_events = 0
            self.mouse_events = 0
            self.capture_active = False
            self.skip_mouse = True
            self.trace = (args.trace_dir / "trace.jsonl").open("w") if args.trace_dir else None
            self.fire_schedule = fire_schedule
            self.fire_index = 0
            self.fire_level = False
            self.target_ticks = target_ticks
            self.key_schedule = key_schedule
            for binding, action in BINDINGS.items():
                if action == "pause":
                    self.accept(binding, self.toggle_pause)
                elif action == "quit":
                    self.accept(binding, self.userExit)
                else:
                    self.accept(binding, self.input_key, [binding, True])
                    self.accept(binding+"-up", self.input_key, [binding, False])
            if not args.offscreen:
                self.accept("window-event", self.focus_event)
                self.capture_mouse(True)
            self.taskMgr.add(self.tick, "flight-tick", sort=40)
            gsg = self.win.getGsg()
            self.details = dict(engine="Panda3D", mode="offscreen" if args.offscreen else "window",
                                output_type=self.win.getType().getName(),
                                renderer=gsg.getDriverRenderer(), vendor=gsg.getDriverVendor(),
                                driver=gsg.getDriverVersion(), size=[self.win.getXSize(), self.win.getYSize()],
                                seed=14, fixed_dt=1/60, render_hz=args.render_hz, controls=list(displayed_controls),
                                settings=asdict(settings), eye_offset=EYE_OFFSET,
                                audio=("muted" if args.mute
                                       else ("openal" if self.sfxManagerList else "no-device")),
                                master_volume=args.master_volume,
                                capture_method="GraphicsOutput.saveScreenshot after graphicsEngine.renderFrame")
            if not args.offscreen:
                self.details["window_id"] = self.win.getWindowHandle().getIntHandle()
            print("RENDERER " + json.dumps(self.details), flush=True)
            print("WINDOW_READY", flush=True)

        def record(self, kind, **values):
            if self.trace:
                self.trace.write(json.dumps(dict(kind=kind, frame=self.frame_count,
                                                tick=self.stepper.tick, **values))+"\n")
                self.trace.flush()

        def input_key(self, key, down):
            action = self.controls.bindings.get(key)
            if action == "restart" and down:
                # Restart is a one-action reset on a terminal end state (or a
                # manual reset mid-run). It does not flow through the held/edge
                # input adapter, so it works while the world is frozen.
                self.restart_mission()
                self.input_events += 1
                self.record("key", key=key, down=True, restart=True,
                            paused=self.controls.paused)
                print("INPUT " + key + " down", flush=True)
                return
            self.controls.key(key, down)
            self.input_events += int(down)
            self.record("key", key=key, down=down, paused=self.controls.paused)
            print("INPUT " + key + (" down" if down else " up"), flush=True)

        def capture_mouse(self, active):
            active = active and not args.keyboard_only
            if args.offscreen:
                return
            props = WindowProperties()
            props.setCursorHidden(active)
            props.setMouseMode(WindowProperties.M_relative if active else WindowProperties.M_absolute)
            self.win.requestProperties(props)
            self.capture_active = active
            self.skip_mouse = True
            if active:
                self.win.movePointer(0, self.win.getXSize()//2, self.win.getYSize()//2)

        def toggle_pause(self):
            if not self.controls.focused:
                return
            self.controls.set_paused(not self.controls.paused)
            self.capture_mouse(not self.controls.paused)
            self.record("pause", paused=self.controls.paused)

        def focus_event(self, window):
            if window != self.win:
                return
            props = window.getProperties()
            if not props.getOpen():
                self.userExit()
                return
            focused = props.getForeground() and not props.getMinimized()
            if focused != self.controls.focused:
                self.controls.set_focus(focused)
                if not focused:
                    self.capture_mouse(False)
                self.record("focus", focused=focused, paused=self.controls.paused)

        def read_mouse(self):
            if not self.capture_active or self.controls.paused or args.offscreen:
                return
            if not self.win.getProperties().getForeground():
                return
            pointer = self.win.getPointer(0)
            cx, cy = self.win.getXSize()//2, self.win.getYSize()//2
            dx, dy = pointer.getX()-cx, pointer.getY()-cy
            if self.skip_mouse:
                # Mapping/focus is asynchronous: retry initial centering rather
                # than permanently disabling aim on a not-yet-mapped window.
                if self.win.movePointer(0, cx, cy):
                    self.skip_mouse = False
                return
            if dx or dy:
                self.controls.mouse_delta(dx, dy)
                self.mouse_events += 1
                self.record("mouse", dx=dx, dy=dy, aim=list(self.controls.aim),
                            mode=int(self.win.getProperties().getMouseMode()))
            if not self.win.movePointer(0, cx, cy):
                self.controls.aim[:] = [0., 0.]
                self.capture_mouse(False)
                self.record("mouse_unavailable", fallback="arrows")

        def health_dict(self):
            return self.damage.snapshot(self.flight.entity_id).to_dict()

        def weapons_dict(self):
            return self.weapons.aim_snapshot().to_dict()

        def enemy_health_dict(self):
            # Union of static targets (node-4 --static-targets) and live enemies
            # (mission/demo), filtered to whatever is actually spawned.
            ids = set(self.combat_ids) | set(self.enemies.entities)
            return {eid: self.damage.snapshot(eid).to_dict()
                    for eid in sorted(ids) if self.damage.is_spawned(eid)}

        def _enemy_tint(self, state):
            # Code-level "visible weakening": colour darkens as the hull degrades.
            return {"healthy": (1, 1, 1, 1), "smoking": (1, 0.55, 0.35, 1),
                    "burning": (0.55, 0.3, 0.2, 1),
                    "destroyed": (0.3, 0.15, 0.1, 1)}.get(state, (1, 1, 1, 1))

        def _spawn_enemy_presentation(self):
            for mesh in getattr(self, "enemy_meshes", {}).values():
                mesh.removeNode()
            self.enemy_meshes = {}
            self.ensure_enemy_meshes()

        def ensure_enemy_meshes(self):
            # New waves spawn dynamically; build a presentation mesh for any
            # enemy that does not have one yet (idempotent across waves/restart).
            for eid in self.enemies.entities:
                if eid in self.enemy_meshes:
                    continue
                half = CAPITAL_HALF if self.enemies.is_capital(eid) else FIGHTER_HALF
                size = tuple(2.0 * h for h in half)
                mesh = box(f"enemy-{eid}", size, (0.95, 0.32, 0.16, 1))
                mesh.reparentTo(self.render)
                self.enemy_meshes[eid] = mesh

        def restart_mission(self):
            # Rebuild the deterministic combat state from a pristine spawn,
            # reusing the same service objects (so cockpit/effects references
            # stay valid) but clearing every mutable combat registry.
            self.flight.reset(position=PLAYER_SPAWN)
            self.damage.reset()
            self.weapons.reset()
            self.enemies.reset()
            self.flight.damage = self.damage
            self.damage.spawn(self.flight.entity_id, FIGHTER_PROFILE)
            self.damage.speed_provider = (
                lambda eid: self.flight.velocity.length()
                if eid == self.flight.entity_id else self.enemies.speed_of(eid))
            # Re-register the player hitbox and a fresh mission.
            if args.demo_enemies:
                demo_waves = (WaveSpec("Demo encounter",
                                       fighters=("fighter-1", "fighter-2", "fighter-3"),
                                       capital=True),)
                demo_positions = {"fighter-1": (-23, 56, 7),
                                  "fighter-2": (21, 63, -5),
                                  "fighter-3": (27, 100, 19),
                                  "capital": (0, 85, 4)}
                self.mission = MissionSystem(
                    player_entity_id=self.flight.entity_id,
                    damage=self.damage, enemies=self.enemies, weapons=self.weapons,
                    player_pose_provider=self.flight.snapshot,
                    waves=demo_waves, spawn_positions=demo_positions)
            else:
                self.mission = MissionSystem(
                    player_entity_id=self.flight.entity_id,
                    damage=self.damage, enemies=self.enemies, weapons=self.weapons,
                    player_pose_provider=self.flight.snapshot)
            self.mission.start()
            self.combat_ids = list(self.enemies.entities)
            # Reset presentation caches and particle state; effects keeps the
            # full mission entity set so later waves are already tracked.
            self.cockpit.reset()
            self.effects.reset(tracked_entities=(
                list(self.enemies.entities) + [self.flight.entity_id]))
            self._spawn_enemy_presentation()
            # Rebuild the stepper so the fresh mission is in the fixed order.
            self.stepper = FixedStepper(self.flight, systems=[self.enemies,
                                                              self.weapons,
                                                              self.damage,
                                                              self.mission,
                                                              self.effects])
            self.fire_index = 0
            self.fire_level = False
            self.record("restart", phase=self.mission.phase,
                        wave=self.mission.snapshot().wave)
            print("MISSION_RESTART phase=combat wave=1", flush=True)

        def sample_controls(self):
            # Scripted fire toggles overlay the sampled input for deterministic
            # offscreen combat runs; keyed by the upcoming fixed tick.
            upcoming = self.stepper.tick + 1
            while (self.fire_index < len(self.fire_schedule)
                   and self.fire_schedule[self.fire_index][0] <= upcoming):
                self.fire_level = self.fire_schedule[self.fire_index][1]
                self.fire_index += 1
            controls = self.controls.sample()
            if self.fire_level:
                controls = replace(controls, fire=True)
            if upcoming in self.target_ticks:
                controls = replace(controls, target_next=True)
            return controls

        def simulation_tick(self, tick, controls, ship):
            if self.trace:
                self.record("simulation", controls=asdict(controls), ship=asdict(ship),
                            health=self.health_dict())
            for event in damage_schedule.get(tick, ()):
                self.damage.queue(event)

        def screenshot(self, path):
            self.graphicsEngine.renderFrame()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self.win.saveScreenshot(str(path.resolve())):
                raise RuntimeError("Actual framebuffer capture failed")

        def tick(self, task):
            self.frame_count += 1
            # Deterministic scripted key events (frame-indexed) for lifecycle
            # validation: pause, restart, and other keys without a live human.
            for kf in self.key_schedule.get(self.frame_count, ()):
                action = self.controls.bindings.get(kf[0])
                if action == "pause" and kf[1]:
                    self.toggle_pause()
                elif action == "quit" and kf[1]:
                    self.userExit()
                else:
                    self.input_key(kf[0], kf[1])
            self.read_mouse()
            dt = ClockObject.getGlobalClock().getDt()
            before = self.stepper.dropped_seconds
            frozen = self.controls.paused or self.mission.terminal
            steps = self.stepper.advance(dt, self.sample_controls, frozen,
                                         self.simulation_tick)
            if self.stepper.dropped_seconds > before:
                self.record("time_drop", seconds=self.stepper.dropped_seconds-before)
                print("TIME_DROP " + str(self.stepper.dropped_seconds-before), flush=True)
            pose = self.view.present(self.stepper.previous, self.stepper.current, self.stepper.alpha)
            health = self.health_dict()
            aim = self.weapons_dict()
            health_view = self.damage.snapshot(self.flight.entity_id)
            aim_state = self.weapons.aim_snapshot()
            locked = aim["locked_target"]
            target_health = self.damage.snapshot(locked) if locked is not None else None
            hud = self.cockpit.present(self.stepper.alpha, pose, health_view, aim_state,
                                       target_health)
            self.pause_label.setText("PAUSED - Esc to resume (controls cleared)" if self.controls.paused else "")
            mission = self.mission.snapshot()
            self.objective_banner.setText(mission.objective)
            self.objective_sub.setText(
                ("Wave %d/%d  |  Enemies: %d  %s"
                 % (mission.wave, mission.total_waves,
                    len(mission.remaining_enemies), mission.restart_hint))
                if not mission.terminal else
                ("Wave %d/%d  |  %s" % (mission.wave, mission.total_waves,
                                        mission.restart_hint)))
            self.ensure_enemy_meshes()
            for eid, mesh in self.enemy_meshes.items():
                if not self.enemies.is_alive(eid):
                    mesh.hide()
                    continue
                view = self.enemies.snapshot(eid)
                mesh.show()
                mesh.setPos(*view.position)
                mesh.setQuat(Quat(*view.orientation))
                mesh.setColor(*self._enemy_tint(self.damage.snapshot(eid).state.value))
            self.effects.present(self.stepper.alpha)
            capture = None
            if args.trace_dir and self.frame_count in capture_frames:
                capture = f"frame-{self.frame_count:04d}.png"
                self.screenshot(args.trace_dir / capture)
            self.record("frame", dt=dt, steps=steps, alpha=self.stepper.alpha,
                        paused=self.controls.paused, held=sorted(self.controls.held),
                        ship=asdict(self.stepper.current), pose=asdict(pose),
                        health=health, weapons=aim,
                        weapon_hits=[[h[0], h[1].value, h[2]] for h in self.weapons.hits_this_tick],
                        enemy_health=self.enemy_health_dict(),
                        enemy_state=self.enemies.ai_snapshot(),
                        enemy_events=self.enemies.last_enemy_events,
                        hud=hud.to_dict(),
                        radar=[b.to_dict() for b in self.cockpit.last_blips],
                        camera_position=tuple(self.camera.getPos(self.render)),
                        camera_orientation=tuple(self.camera.getQuat(self.render)),
                        eye_local=tuple(self.camera.getPos()),
                        effects=self.effects.counts(),
                        effects_spawns=self.effects.last_spawns,
                        effects_audio=self.effects.last_audio,
                        mission=mission.to_dict(),
                        mission_transitions=list(self.mission.transition_log),
                        capture=capture)
            if args.trace_dir:
                print("FLIGHT_FRAME " + str(self.frame_count), flush=True)
            if args.frames and self.frame_count >= args.frames:
                if args.capture:
                    self.screenshot(args.capture)
                audio_tally = {}
                for e in self.audio.events:
                    audio_tally[e["name"]] = audio_tally.get(e["name"], 0) + 1
                self.details.update(frames=self.frame_count, ticks=self.stepper.tick,
                                    input_events=self.input_events, mouse_events=self.mouse_events,
                                    ship=asdict(self.flight.snapshot()),
                                    health=self.health_dict(),
                                    weapons=self.weapons_dict(),
                                    enemy_health=self.enemy_health_dict(),
                                    enemy_state=self.enemies.ai_snapshot(),
                                    dropped_seconds=self.stepper.dropped_seconds,
                                    drop_events=self.stepper.drop_events,
                                    effects=self.effects.counts(),
                                    effects_event_tally=self.effects.event_tally,
                                    effects_destroyed_kinds=sorted(self.effects.destroyed_kinds),
                                    mission=self.mission.snapshot().to_dict(),
                                    mission_transitions=list(self.mission.transition_log),
                                    audio_played=self.audio.played,
                                    audio_events=len(self.audio.events),
                                    audio_tally=audio_tally,
                                    audio_cues=self.audio.events[-1] if self.audio.events else None,
                                    capture=str(args.capture) if args.capture else None)
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(self.details, indent=2) + "\n")
                print("RUNTIME_PASS " + json.dumps(self.details), flush=True)
                self.userExit()
            return task.cont

    app = FlightApp()
    try:
        app.run()
    finally:
        if app.trace:
            app.trace.close()
        app.destroy()

if __name__ == "__main__":
    main()
