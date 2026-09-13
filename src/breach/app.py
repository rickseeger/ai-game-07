"""Real Panda3D application with renderer-owned capture."""
import argparse
import json
from pathlib import Path
from panda3d.core import loadPrcFileData

def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--offscreen", action="store_true")
    p.add_argument("--mute", action="store_true")
    p.add_argument("--frames", type=int, default=0, help="0 = run until Escape")
    p.add_argument("--capture", type=Path)
    p.add_argument("--report", type=Path)
    return p

def main():
    args = parser().parse_args()
    if args.frames < 0 or ((args.capture or args.report) and not args.frames):
        raise SystemExit("capture/report require --frames > 0; frames cannot be negative")
    loadPrcFileData("foundation", "window-title G14 - Breach Flight Foundation\nwin-size 960 540\nsync-video false\nclock-mode limited\nclock-frame-rate 60\nframebuffer-multisample false\nnotify-level info")
    if args.mute:
        loadPrcFileData("audio", "audio-library-name null")
    if args.offscreen:
        loadPrcFileData("window", "window-type offscreen")
    from direct.showbase.ShowBase import ShowBase
    from direct.gui.OnscreenText import OnscreenText
    from panda3d.core import AmbientLight, DirectionalLight, TextNode
    from breach.scene import build_scene

    class Foundation(ShowBase):
        def __init__(self):
            super().__init__()
            if not self.win or not self.win.isValid():
                raise RuntimeError("Renderer failed to create a valid output")
            self.disableMouse()
            self.setBackgroundColor(0.012, 0.021, 0.042, 1)
            self.camLens.setFov(78)
            self.camLens.setNearFar(0.1, 2000)
            self.camera.setPos(0, -15, 4)
            self.camera.lookAt(0, 78, 5)
            self.scene = build_scene(self.render)
            ambient = AmbientLight("ambient")
            ambient.setColor((0.4, 0.4, 0.45, 1))
            self.render.setLight(self.render.attachNewNode(ambient))
            key = DirectionalLight("key")
            key.setColor((0.9, 0.85, 0.75, 1))
            key_np = self.render.attachNewNode(key)
            key_np.setHpr(-30, -45, 0)
            self.render.setLight(key_np)
            OnscreenText(text="BREACH FLIGHT / G14 FOUNDATION", pos=(-1.68, 0.88),
                         scale=0.055, fg=(0.3, 0.9, 0.85, 1), align=TextNode.ALeft)
            OnscreenText(text="3D staging scene - not gameplay | Space: inspection angle | Esc: exit",
                         pos=(0, -0.91), scale=0.038, fg=(0.8, 0.85, 0.9, 1))
            self.frame_count = 0
            self.input_events = 0
            self.accept("escape", self.userExit)
            self.accept("space", self.inspect_angle)
            self.taskMgr.add(self.tick, "foundation-tick", sort=60)
            gsg = self.win.getGsg()
            self.details = dict(engine="Panda3D", mode="offscreen" if args.offscreen else "window",
                                output_type=self.win.getType().getName(),
                                renderer=gsg.getDriverRenderer(), vendor=gsg.getDriverVendor(),
                                driver=gsg.getDriverVersion(), size=[self.win.getXSize(), self.win.getYSize()],
                                audio="explicitly disabled" if args.mute else "backend requested; no playback test",
                                capture_method="GraphicsOutput.saveScreenshot after graphicsEngine.renderFrame")
            if not args.offscreen:
                self.details["window_id"] = self.win.getWindowHandle().getIntHandle()
            print("RENDERER " + json.dumps(self.details), flush=True)
            print("WINDOW_READY", flush=True)

        def inspect_angle(self):
            self.input_events += 1
            self.camera.setX(5 if self.input_events % 2 else 0)
            self.camera.lookAt(0, 78, 5)
            print("INPUT space " + str(self.input_events), flush=True)

        def tick(self, task):
            self.frame_count += 1
            if args.frames and self.frame_count >= args.frames:
                self.graphicsEngine.renderFrame()
                if args.capture:
                    args.capture.parent.mkdir(parents=True, exist_ok=True)
                    if not self.win.saveScreenshot(str(args.capture.resolve())):
                        raise RuntimeError("Actual framebuffer capture failed")
                self.details.update(frames=self.frame_count, input_events=self.input_events,
                                    capture=str(args.capture) if args.capture else None)
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(self.details, indent=2) + "\n")
                print("RUNTIME_PASS " + json.dumps(self.details), flush=True)
                self.userExit()
            return task.cont

    app = Foundation()
    try:
        app.run()
    finally:
        app.destroy()

if __name__ == "__main__":
    main()
