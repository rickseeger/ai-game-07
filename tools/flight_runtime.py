"""External pilot: XTest hardware events only, never imports/mutates the live app.
Run under DISPLAY (tools/headless.py supplies a private, health-checked Xvfb).
Frame milestones schedule hardware events; actual receipt/ticks are recorded.
"""
import argparse
import ctypes as C
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from panda3d.core import PNMImage, Quat, Vec3
from breach.camera import EYE_OFFSET
from breach.flight import FlightSystem
from breach.contracts import PilotInput, FIXED_DT
from breach.input import CONTROL_LINES

class XInput:
    def __init__(self):
        self.x = C.CDLL("libX11.so.6")
        self.t = C.CDLL("libXtst.so.6")
        signatures = {
            "XOpenDisplay": ([C.c_char_p], C.c_void_p),
            "XDefaultRootWindow": ([C.c_void_p], C.c_ulong),
            "XSetInputFocus": ([C.c_void_p,C.c_ulong,C.c_int,C.c_ulong],C.c_int),
            "XStringToKeysym": ([C.c_char_p], C.c_ulong),
            "XKeysymToKeycode": ([C.c_void_p,C.c_ulong],C.c_uint),
            "XFlush": ([C.c_void_p], C.c_int),
            "XCloseDisplay": ([C.c_void_p],C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn=getattr(self.x,name); fn.argtypes=args; fn.restype=result
        self.t.XTestFakeKeyEvent.argtypes=[C.c_void_p,C.c_uint,C.c_int,C.c_ulong]
        self.t.XTestFakeRelativeMotionEvent.argtypes=[C.c_void_p,C.c_int,C.c_int,C.c_ulong]
        self.t.XTestFakeButtonEvent.argtypes=[C.c_void_p,C.c_uint,C.c_int,C.c_ulong]
        self.display=self.x.XOpenDisplay(None)
        if not self.display: raise RuntimeError("XOpenDisplay failed")
        self.root=self.x.XDefaultRootWindow(self.display)
        self.held=set()

    def focus(self, window):
        self.x.XSetInputFocus(self.display,window,1,0)
        self.x.XFlush(self.display)

    def key(self, name, down):
        if name == "mouse1":
            assert self.t.XTestFakeButtonEvent(self.display,1,int(down),0)
        else:
            symbol=self.x.XStringToKeysym(name.encode())
            code=self.x.XKeysymToKeycode(self.display,symbol)
            assert code, name
            assert self.t.XTestFakeKeyEvent(self.display,code,int(down),0)
        if down: self.held.add(name)
        else: self.held.discard(name)
        self.x.XFlush(self.display)

    def mouse(self, dx, dy):
        assert self.t.XTestFakeRelativeMotionEvent(self.display,dx,dy,0)
        self.x.XFlush(self.display)

    def close(self):
        for key in tuple(self.held): self.key(key,False)
        self.x.XCloseDisplay(self.display)

# These are X keysyms, not Panda messenger events or simulation commands.
SCHEDULE = {
    30: [("key","w",True)], 90: [("key","w",False)],
    95: [("key","s",True)], 110: [("key","s",False)],
    115: [("key",k,True) for k in ("Right","Up","d","w")],
    140: [("key",k,False) for k in ("Right","Up","d","w")],
    145: [("key","b",True)], 190: [("key","b",False)],
    195: [("key","w",True),("key","Left",True)],
    210: [("focus","away")],
    220: [("key","w",False),("key","Left",False)],
    235: [("focus","game")],
    245: [("key","Escape",True)], 247: [("key","Escape",False)],
    260: [("key","Right",True),("key","w",True)],
    270: [("key","Escape",True)], 272: [("key","Escape",False)],
    278: [("key","Right",False),("key","w",False)],
    285: [("key","Escape",True)], 287: [("key","Escape",False)],
    300: [("mouse",30,-20)], 320: [("key","c",True)],
    325: [("key","c",False),("key","Home",True),("key","b",True)],
    400: [("key","Home",False),("key","b",False)],
    410: [("key","space",True),("key","mouse1",True)],
    415: [("key","space",False),("key","mouse1",False)],
    420: [("key","w",True),("key","s",True)],
    435: [("key","w",False)], 445: [("key","s",False)],
    450: [("key",k,True) for k in ("r","w","Right")],
    465: [("key",k,False) for k in ("r","w","Right")],
    470: [("key","w",True)],
    490: [("key","w",False),("key","b",True)],
    515: [("key","b",False)],
}


def validate(out):
    events=[json.loads(line) for line in (out/"flight"/"trace.jsonl").read_text().splitlines()]
    frames={e["frame"]:e for e in events if e["kind"]=="frame"}
    ticks=[e for e in events if e["kind"]=="simulation"]
    report=json.loads((out/"report.json").read_text())
    assert len(frames)==520 and report["frames"]==520
    assert report["controls"]==list(CONTROL_LINES)
    expected_downs = sum(a[0] == "key" and a[2] and a[1] != "Escape"
                         for actions in SCHEDULE.values() for a in actions)
    assert report["input_events"] == expected_downs
    assert report["mouse_events"]>0
    assert any(e["kind"]=="focus" and not e["focused"] for e in events)
    assert any(e["kind"]=="focus" and e["focused"] for e in events)
    assert sum(e["kind"]=="pause" for e in events)==3
    assert not any(e["kind"]=="mouse_unavailable" for e in events)
    speed=lambda f: math.sqrt(sum(x*x for x in frames[f]["ship"]["velocity"]))
    assert frames[90]["ship"]["position"][1]>-10, "W must advance toward staging patrol"
    assert speed(90)>15
    assert frames[110]["ship"]["throttle"]<frames[95]["ship"]["throttle"], "S reduces throttle"
    assert frames[94]["ship"]["throttle"]==frames[92]["ship"]["throttle"], "release retains throttle"
    assert any(all(e["controls"][k]>0 for k in ("throttle","yaw","pitch","roll")) for e in ticks)
    assert speed(190)<.01 and frames[190]["ship"]["throttle"]==0
    for lo,hi in ((213,244),(273,284)):
        for f in range(lo,hi+1):
            assert frames[f]["paused"]
            assert frames[f]["held"]==[]
            assert frames[f]["ship"]==frames[lo]["ship"], "paused simulation moved"
    assert not frames[250]["paused"] and frames[250]["held"]==[]
    assert any(e["frame"] in range(302,320) and e["controls"]["yaw"]>0 and
               e["controls"]["pitch"]>0 for e in ticks), "real mouse must steer"
    assert all(e["controls"]["yaw"]==e["controls"]["pitch"]==0
               for e in ticks if e["frame"] in range(322,325)), "C must center aim"
    upright=Quat(*frames[400]["ship"]["orientation"]).xform(Vec3(0,0,1))
    assert upright.z>.999, "Home must level the ship"
    assert frames[418]["ship"]["orientation"]==frames[408]["ship"]["orientation"], "Space must not inspect"
    assert any(e["controls"]["fire"] for e in ticks if 411<=e["frame"]<=415)
    assert all(e["controls"]["throttle"]==0 for e in ticks if 423<=e["frame"]<=434)
    assert all(e["ship"]["throttle"]==0 for e in ticks if 453<=e["frame"]<=464)
    assert frames[464]["ship"]["orientation"]==frames[453]["ship"]["orientation"]
    assert frames[490]["ship"]["position"]!=frames[470]["ship"]["position"], "release repair allows escape"
    assert speed(520)<.01
    # Independent deterministic replay of received controls, not a substitute for X input.
    replay=FlightSystem()
    for expected,e in enumerate(ticks,1):
        assert e["tick"]==expected
        replay.fixed_update(FIXED_DT,PilotInput(**e["controls"]))
        assert json.loads(json.dumps(asdict(replay.snapshot())))==e["ship"]
    anchor_error=0.0
    for f in frames.values():
        pose=f["pose"]
        expected=Vec3(*pose["position"])+Quat(*pose["orientation"]).xform(Vec3(*EYE_OFFSET))
        error=(Vec3(*f["camera_position"])-expected).length()
        anchor_error=max(anchor_error,error)
        assert error<1e-4
        assert (Vec3(*f["eye_local"])-Vec3(*EYE_OFFSET)).length()<1e-6
        assert abs(Quat(*f["camera_orientation"]).dot(Quat(*pose["orientation"]))-1)<1e-5
        assert 0<=f["alpha"]<=1 and 0<=f["steps"]<=6
    captures=[]
    for f in frames.values():
        if not f["capture"]: continue
        path=out/"flight"/f["capture"]
        image=PNMImage(); assert image.read(str(path.resolve()))
        assert (image.getXSize(),image.getYSize())==(960,540)
        colors={tuple(round(v*255) for v in image.getXel(x,y))
                for x in range(160,800) for y in range(100,400)}
        assert len(colors)>=4, "capture must contain real viewport variation"
        captures.append(dict(frame=f["frame"],tick=f["tick"],path=str(path),
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                             viewport_colors=len(colors)))
    assert len(captures)==8
    assert len({c["sha256"] for c in captures})>=6
    summary=dict(status="passed",frames=len(frames),simulation_ticks=len(ticks),
        seed=14,input_events=report["input_events"],mouse_events=report["mouse_events"],
        mouse_modes=sorted({e["mode"] for e in events if e["kind"]=="mouse"}),
        peak_speed=max(speed(f) for f in frames),stop_speed=speed(520),
        camera_max_anchor_error=anchor_error,renderer=report["renderer"],
        dropped_seconds=report["dropped_seconds"],captures=captures,
        checks=["W pursuit / S reduction / release retention", "simultaneous pitch yaw roll thrust",
                "brake stop", "focus loss clears + freezes until explicit resume",
                "Escape pause/resume with held inputs", "XTest mouse steering + C center",
                "Home horizon recovery", "Space/LMB reserved, not inspection",
                "opposed throttles cancel", "repair input locks flight, release allows escape",
                "every received tick deterministically replayed", "every frame camera anchored inside ship",
                "displayed controls equal shared control strings"],
        scope="Real X11/XTest -> Panda -> InputAdapter -> fixed flight -> actual GLX framebuffer; no physical human playtest. Relative mode may use center-warp fallback; see logs.")
    (out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print("FLIGHT_RUNTIME_PASS "+json.dumps(summary))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--validate-only",action="store_true")
    args=parser.parse_args(); out=args.output
    if args.validate_only:
        validate(out); return
    out.mkdir(parents=True,exist_ok=False)
    command=[sys.executable,"-m","breach.app","--mute","--no-combat-targets","--frames","520",
             "--trace-dir",str(out/"flight"),"--report",str(out/"report.json")]
    (out/"commands.json").write_text(json.dumps(dict(command=command,seed=14,schedule=SCHEDULE),indent=2)+"\n")
    x=XInput()
    p=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
    selector=selectors.DefaultSelector(); selector.register(p.stdout,selectors.EVENT_READ)
    pending=b""; window=None; deadline=time.monotonic()+90
    try:
        with (out/"runtime.log").open("wb") as log, (out/"injected.jsonl").open("w") as injected:
            while selector.get_map():
                if time.monotonic()>deadline: raise TimeoutError("flight runtime exceeded 90s")
                for key,_ in selector.select(.5):
                    chunk=os.read(key.fd,65536)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    log.write(chunk); log.flush(); pending+=chunk
                    while b"\n" in pending:
                        line,pending=pending.split(b"\n",1)
                        text=line.decode(errors="replace")
                        if text.startswith("RENDERER "):
                            window=json.loads(text[9:])["window_id"]
                        elif text=="WINDOW_READY":
                            assert window; x.focus(window)
                        elif text.startswith("FLIGHT_FRAME "):
                            frame=int(text.split()[1])
                            for action in SCHEDULE.get(frame,[]):
                                if action[0]=="focus": x.focus(x.root if action[1]=="away" else window)
                                elif action[0]=="mouse": x.mouse(*action[1:])
                                else: x.key(*action[1:])
                                injected.write(json.dumps(dict(after_frame=frame,action=action))+"\n")
                                injected.flush()
                        elif "Traceback" in text or "Error" in text:
                            print(text,flush=True)
        assert p.wait(timeout=5)==0, "application failed; see runtime.log"
    finally:
        if p.poll() is None: p.kill(); p.wait()
        x.close(); selector.close(); p.stdout.close()
    validate(out)

if __name__=="__main__": main()
