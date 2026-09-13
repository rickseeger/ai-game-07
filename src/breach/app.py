"""Single-window integration: hardware -> fixed simulation -> cockpit presentation."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from panda3d.core import loadPrcFileData
from breach.input import BINDINGS, CONTROL_LINES, InputAdapter, InputSettings, control_lines


def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--render-hz", type=int, default=60, help="render limiter only; simulation stays 60 Hz")
    p.add_argument("--offscreen", action="store_true")
    p.add_argument("--mute", action="store_true")
    p.add_argument("--frames", type=int, default=0, help="0 = run until F10/window close")
    p.add_argument("--capture", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--trace-dir", type=Path, help="fresh directory for real input/tick/frame evidence")
    p.add_argument("--capture-frames", default="20,100,140,190,240,300,410,470")
    p.add_argument("--mouse-sensitivity", type=float, default=0.012)
    p.add_argument("--invert-y", action="store_true")
    p.add_argument("--keyboard-only", action="store_true")
    return p


def main():
    args = parser().parse_args()
    if args.frames < 0 or ((args.capture or args.report or args.trace_dir) and not args.frames):
        raise SystemExit("capture/report/trace require --frames > 0")
    if not 15 <= args.render_hz <= 240:
        raise SystemExit("--render-hz must be between 15 and 240")
    settings = InputSettings(sensitivity=args.mouse_sensitivity, invert_y=args.invert_y)
    displayed_controls = control_lines(settings, args.keyboard_only)
    capture_frames = {int(x) for x in args.capture_frames.split(",") if x}
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
    from panda3d.core import AmbientLight, DirectionalLight, TextNode, WindowProperties, ClockObject
    from breach.scene import build_scene
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
            self.flight = FlightSystem()
            self.stepper = FixedStepper(self.flight)
            self.view = FlightCamera(self.render, self.camera)
            self.view.add_canopy()
            self.view.present(self.stepper.previous, self.stepper.current, 1)
            self.scene = build_scene(self.render)
            ambient = AmbientLight("ambient")
            ambient.setColor((0.4, 0.4, 0.45, 1))
            self.render.setLight(self.render.attachNewNode(ambient))
            key = DirectionalLight("key")
            key.setColor((0.9, 0.85, 0.75, 1))
            key_np = self.render.attachNewNode(key)
            key_np.setHpr(-30, -45, 0)
            self.render.setLight(key_np)
            OnscreenText(text="BREACH FLIGHT / FLIGHT TEST - NO COMBAT", pos=(-1.68, 0.90),
                         scale=0.046, fg=(0.3, 0.9, 0.85, 1), align=TextNode.ALeft)
            for i, line in enumerate(displayed_controls):
                OnscreenText(text=line, pos=(0, -0.55-i*.073), scale=0.052,
                             fg=(0.8, 0.85, 0.9, 1), bg=(.012, .021, .042, 1))
            OnscreenText(text="+", pos=(0, 0), scale=.045, fg=(.4, 1, .85, 1))
            self.aim_marker = OnscreenText(text="o", pos=(0, 0), scale=.03, fg=(1,.75,.3,1))
            self.hud = OnscreenText(text="", pos=(-1.68, .82), scale=.038,
                                   fg=(.9,.95,1,1), align=TextNode.ALeft, mayChange=True)
            self.pause_label = OnscreenText(text="", pos=(0,.3), scale=.06,
                                           fg=(1,.8,.4,1), mayChange=True)
            self.frame_count = 0
            self.input_events = 0
            self.mouse_events = 0
            self.capture_active = False
            self.skip_mouse = True
            self.trace = (args.trace_dir / "trace.jsonl").open("w") if args.trace_dir else None
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
                                audio="explicitly disabled" if args.mute else "backend requested; no playback test",
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

        def simulation_tick(self, tick, controls, ship):
            self.record("simulation", controls=asdict(controls), ship=asdict(ship))

        def screenshot(self, path):
            self.graphicsEngine.renderFrame()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self.win.saveScreenshot(str(path.resolve())):
                raise RuntimeError("Actual framebuffer capture failed")

        def tick(self, task):
            self.frame_count += 1
            self.read_mouse()
            dt = ClockObject.getGlobalClock().getDt()
            before = self.stepper.dropped_seconds
            steps = self.stepper.advance(dt, self.controls.sample, self.controls.paused,
                                         self.simulation_tick if self.trace else None)
            if self.stepper.dropped_seconds > before:
                self.record("time_drop", seconds=self.stepper.dropped_seconds-before)
                print("TIME_DROP " + str(self.stepper.dropped_seconds-before), flush=True)
            pose = self.view.present(self.stepper.previous, self.stepper.current, self.stepper.alpha)
            speed = self.flight.velocity.length()
            self.hud.setText(f"Throttle {self.flight.throttle:.0%} | Speed {speed:.1f} m/s | B stops | Home levels")
            self.aim_marker.setPos(self.controls.aim[0]*.25, self.controls.aim[1]*.25)
            self.pause_label.setText("PAUSED - Esc to resume (controls cleared)" if self.controls.paused else "")
            capture = None
            if args.trace_dir and self.frame_count in capture_frames:
                capture = f"frame-{self.frame_count:04d}.png"
                self.screenshot(args.trace_dir / capture)
            self.record("frame", dt=dt, steps=steps, alpha=self.stepper.alpha,
                        paused=self.controls.paused, held=sorted(self.controls.held),
                        ship=asdict(self.stepper.current), pose=asdict(pose),
                        camera_position=tuple(self.camera.getPos(self.render)),
                        camera_orientation=tuple(self.camera.getQuat(self.render)),
                        eye_local=tuple(self.camera.getPos()), capture=capture)
            if args.trace_dir:
                print("FLIGHT_FRAME " + str(self.frame_count), flush=True)
            if args.frames and self.frame_count >= args.frames:
                if args.capture:
                    self.screenshot(args.capture)
                self.details.update(frames=self.frame_count, ticks=self.stepper.tick,
                                    input_events=self.input_events, mouse_events=self.mouse_events,
                                    ship=asdict(self.flight.snapshot()),
                                    dropped_seconds=self.stepper.dropped_seconds,
                                    drop_events=self.stepper.drop_events,
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
