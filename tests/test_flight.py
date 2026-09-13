import dataclasses
import math
import unittest
from panda3d.core import NodePath, Quat, Vec3
from breach.contracts import PilotInput, FIXED_DT, Subsystem
from breach.input import InputAdapter, InputSettings, BINDINGS, CONTROL_LINES
from breach.flight import FlightSystem, FlightPerformance, interpolate
from breach.timing import FixedStepper
from breach.camera import FlightCamera, EYE_OFFSET, HULL_MIN, HULL_MAX

class InputTests(unittest.TestCase):
    def test_normal_release_opposition_and_simultaneous(self):
        i = InputAdapter()
        for key in ("w", "arrow_right", "arrow_up", "d", "space", "b"):
            i.key(key, True)
        c = i.sample()
        self.assertEqual((c.throttle, c.yaw, c.pitch, c.roll), (1,1,1,1))
        self.assertTrue(c.fire and c.brake)
        for key in ("s", "arrow_left", "arrow_down", "a"):
            i.key(key, True)
        c = i.sample()
        self.assertEqual((c.throttle, c.yaw, c.pitch, c.roll), (0,0,0,0))
        for key in tuple(i.held):
            i.key(key, False)
        self.assertEqual(i.sample(), PilotInput())

    def test_each_keyboard_direction_and_action(self):
        for key, field, value in (("w","throttle",1), ("s","throttle",-1),
            ("arrow_up","pitch",1), ("arrow_down","pitch",-1),
            ("arrow_right","yaw",1), ("arrow_left","yaw",-1),
            ("d","roll",1), ("a","roll",-1), ("b","brake",True),
            ("home","level",True), ("r","repair",True), ("mouse1","fire",True)):
            i = InputAdapter()
            i.key(key, True)
            self.assertEqual(getattr(i.sample(), field), value, key)
            i.key(key, False)
            self.assertFalse(getattr(i.sample(), field))

    def test_alias_release_keeps_other_fire_key(self):
        i = InputAdapter()
        i.key("space", True); i.key("mouse1", True); i.key("space", False)
        self.assertTrue(i.sample().fire)

    def test_edge_consumed_only_once_and_repeat_suppressed(self):
        i = InputAdapter()
        i.key("tab", True)
        self.assertTrue(i.sample().target_next)
        i.key("tab", True)
        self.assertFalse(i.sample().target_next)
        i.key("tab", False); i.key("tab", True)
        self.assertTrue(i.sample().target_next)

    def test_focus_and_pause_clear_held_edges_mouse(self):
        for clear in (lambda i: i.set_focus(False), lambda i: i.set_paused(True)):
            i = InputAdapter()
            i.key("w", True); i.key("tab", True); i.mouse_delta(40,-30)
            clear(i)
            i.key("d", True); i.mouse_delta(50,50)
            self.assertEqual(i.sample(), PilotInput())
            self.assertEqual(i.held, set())
            self.assertEqual(i.aim, [0,0])
            i.set_focus(True)
            self.assertTrue(i.paused)
            i.set_paused(False)
            self.assertEqual(i.sample(), PilotInput())

    def test_mouse_deadzone_bounds_sign_invert_and_center(self):
        i = InputAdapter()
        i.mouse_delta(1,1)
        self.assertEqual((i.sample().yaw, i.sample().pitch), (0,0))
        i.mouse_delta(1000,-1000)
        self.assertEqual((i.sample().yaw, i.sample().pitch), (1,1))
        i.key("c", True)
        self.assertEqual((i.sample().yaw, i.sample().pitch), (0,0))
        i = InputAdapter(settings=InputSettings(invert_y=True))
        i.mouse_delta(0,-100)
        self.assertEqual(i.sample().pitch,-1)

    def test_mouse_displacement_partition_independence(self):
        a, b = InputAdapter(), InputAdapter()
        a.mouse_delta(40,-20)
        for _ in range(10): b.mouse_delta(4,-2)
        self.assertAlmostEqual(a.sample().yaw,b.sample().yaw)
        self.assertAlmostEqual(a.sample().pitch,b.sample().pitch)

    def test_remap_selection_and_validation(self):
        i=InputAdapter(bindings={"j":"yaw_right", "2":"weapons"})
        i.key("j", True); i.key("2", True)
        self.assertEqual(i.sample().yaw,1)
        self.assertEqual(i.sample().repair_subsystem, Subsystem.WEAPONS)
        for kw in ({"sensitivity":0}, {"deadzone":1}, {"sensitivity":float("nan")}):
            with self.assertRaises(ValueError): InputSettings(**kw)
        with self.assertRaises(ValueError): i.mouse_delta(float("nan"),0)

    def test_displayed_controls_follow_settings(self):
        from breach.input import control_lines
        self.assertEqual(control_lines(InputSettings()), CONTROL_LINES)
        self.assertIn("nose down; inverted", control_lines(InputSettings(invert_y=True))[2])
        self.assertIn("disabled", control_lines(InputSettings(), True)[2])

    def test_staging_bindings_removed(self):
        self.assertEqual(BINDINGS["space"], "fire")
        self.assertEqual(BINDINGS["escape"], "pause")
        self.assertNotIn("inspection", " ".join(CONTROL_LINES))

class FlightTests(unittest.TestCase):
    def fly(self, f, c, ticks=60):
        for _ in range(ticks): f.fixed_update(FIXED_DT,c)
        return f

    def test_pilot_signs_and_body_local_axes(self):
        for axis, vector, component, sign in (
            ("yaw",Vec3(0,1,0),0,1), ("pitch",Vec3(0,1,0),2,1),
            ("roll",Vec3(1,0,0),2,-1)):
            for direction in (-1,1):
                f=FlightSystem()
                self.fly(f,PilotInput(**{axis:direction}),10)
                self.assertGreater(f.orientation.xform(vector)[component]*sign*direction, .1)
        f=FlightSystem()
        f.orientation=Quat(); f.orientation.setHpr((-90,0,0))
        self.fly(f,PilotInput(pitch=1),10)
        nose=f.orientation.xform(Vec3(0,1,0))
        self.assertGreater(nose.x,.9); self.assertGreater(nose.z,.1)
        self.assertAlmostEqual(nose.y,0,places=5)

    def test_thrust_deliberate_pursuit_and_throttle_retention(self):
        f=FlightSystem()
        self.fly(f,PilotInput(throttle=1),120)
        self.assertEqual(f.throttle,1)
        self.assertGreater(f.position.y,-5)
        self.assertGreater(f.velocity.y,30)
        self.fly(f,PilotInput(yaw=1),30)
        self.fly(f,PilotInput(),120)
        self.assertGreater(f.velocity.x,15)
        self.assertEqual(f.throttle,1)
        self.fly(f,PilotInput(throttle=-1),120)
        self.assertEqual(f.throttle,0)

    def test_brake_overrides_throttle_and_stops(self):
        f=self.fly(FlightSystem(),PilotInput(throttle=1),180)
        self.fly(f,PilotInput(brake=True,throttle=1),60)
        self.assertEqual(f.throttle,0)
        self.assertEqual(tuple(f.velocity),(0,0,0))
        pos=tuple(f.position)
        self.fly(f,PilotInput(),60)
        self.assertEqual(tuple(f.position),pos)

    def test_simultaneous_rotations_normalized_and_finite(self):
        f=self.fly(FlightSystem(),PilotInput(pitch=1,yaw=-1,roll=1,throttle=1),600)
        self.assertAlmostEqual(sum(x*x for x in f.orientation),1,places=5)
        self.assertTrue(all(math.isfinite(x) for x in f.position))
        self.assertLessEqual(f.velocity.length(),45.001)

    def test_level_recovers_pitch_roll_without_chase_camera(self):
        for hpr in ((50,70,130),(-40,-80,-170),(10,0,180),(0,90,90)):
            f=FlightSystem(); f.orientation=Quat(); f.orientation.setHpr(hpr)
            self.fly(f,PilotInput(level=True),240)
            self.assertGreater(f.orientation.xform(Vec3(0,0,1)).z,.999)
            self.assertAlmostEqual(f.orientation.xform(Vec3(0,1,0)).z,0,places=4)

    def test_damage_injection_independent_thrust_and_turning(self):
        class Damage:
            def __init__(self, **kw): self.perf=FlightPerformance(**kw)
            def flight_performance(self, entity_id):
                assert entity_id == "player"
                return self.perf
        c=PilotInput(throttle=1,yaw=1)
        good=self.fly(FlightSystem(),c,30)
        engine=self.fly(FlightSystem(Damage(thrust_multiplier=.25)),c,30)
        turn=self.fly(FlightSystem(Damage(turning_multiplier=.25)),c,30)
        self.assertLess(engine.velocity.length(),good.velocity.length()*.5)
        self.assertEqual(tuple(engine.orientation),tuple(good.orientation))
        self.assertLess(turn.orientation.xform(Vec3(0,1,0)).x,
                        good.orientation.xform(Vec3(0,1,0)).x*.5)
        dead=self.fly(FlightSystem(Damage(thrust_multiplier=0)),c,60)
        self.assertEqual(dead.velocity.length(),0)
        locked=self.fly(FlightSystem(Damage(repair_locked=True)),c,60)
        self.assertEqual(locked.throttle,0)
        self.assertEqual(tuple(locked.orientation),(1,0,0,0))

    def test_repair_request_brakes_and_locks_turn_thrust_immediately(self):
        f=self.fly(FlightSystem(),PilotInput(throttle=1),120)
        q=tuple(f.orientation)
        self.fly(f,PilotInput(repair=True,throttle=1,yaw=1,level=True),60)
        self.assertEqual(tuple(f.orientation),q)
        self.assertEqual(f.velocity.length(),0)
        self.assertEqual(f.throttle,0)
        self.fly(f,PilotInput(throttle=1,yaw=1),30)
        self.assertGreater(f.velocity.length(),0)
        self.assertNotEqual(tuple(f.orientation),q)

    def test_snapshots_are_frozen_detached_and_dt_validation(self):
        f=FlightSystem(); view=f.snapshot()
        with self.assertRaises(dataclasses.FrozenInstanceError): view.throttle=1
        self.assertIsInstance(view.orientation,tuple)
        self.fly(f,PilotInput(throttle=1),60)
        self.assertEqual(view.position,(0,-15,4))
        for dt in (0,-1,float("nan"),float("inf")):
            with self.assertRaises(ValueError): f.fixed_update(dt,PilotInput())

class TimingCameraTests(unittest.TestCase):
    def test_frame_intervals_identical_per_tick_controls(self):
        sequences=([1/30]*180,[1/60]*360,[1/144]*864,[.005,.025,.01,.06]*60)
        final=[]
        for intervals in sequences:
            s=FixedStepper(FlightSystem())
            def sample():
                return PilotInput(throttle=1 if s.tick<120 else 0,
                                  yaw=.5 if 120<=s.tick<180 else 0,
                                  brake=s.tick>=300)
            for dt in intervals: s.advance(dt,sample)
            self.assertEqual(s.tick,360)
            self.assertEqual(s.drop_events,0)
            final.append(s.current)
        self.assertTrue(all(v==final[0] for v in final))

    def test_edges_preserved_until_tick_then_consumed_in_catchup(self):
        i=InputAdapter(); s=FixedStepper(FlightSystem()); results=[]
        i.key("tab",True)
        self.assertEqual(s.advance(.001,i.sample),0)
        s.advance(.1,i.sample,on_tick=lambda tick,c,ship:results.append(c.target_next))
        self.assertEqual(results,[True,False,False,False,False,False])

    def test_drop_budget_alpha_pause_and_invalid_intervals(self):
        s=FixedStepper(FlightSystem())
        self.assertEqual(s.advance(.5,lambda:PilotInput(throttle=1)),6)
        self.assertAlmostEqual(s.dropped_seconds,.4)
        self.assertEqual(s.drop_events,1)
        s.advance(.008,lambda:PilotInput())
        self.assertGreater(s.alpha,0); self.assertLess(s.alpha,1)
        before=s.current
        s.advance(10,lambda:self.fail("sampled paused input"),paused=True)
        self.assertEqual(s.current,before)
        self.assertEqual(s.previous,s.current)
        self.assertEqual(s.alpha,0)
        self.assertEqual(s.advance(FIXED_DT,lambda:PilotInput()),1)
        for dt in (-1,float("nan"),float("inf")):
            with self.assertRaises(ValueError): s.advance(dt,lambda:PilotInput())

    def test_interpolation_endpoints_short_arc_and_camera_anchor(self):
        f=FlightSystem(); a=f.snapshot()
        for _ in range(20): f.fixed_update(FIXED_DT,PilotInput(throttle=1,pitch=1,roll=1,yaw=1))
        b=f.snapshot()
        self.assertEqual(interpolate(a,b,0),a)
        self.assertEqual(interpolate(a,b,1).position,b.position)
        negative=dataclasses.replace(b,orientation=tuple(-v for v in b.orientation))
        self.assertAlmostEqual(abs(sum(x*y for x,y in zip(interpolate(b,negative,.5).orientation,b.orientation))),1,places=5)
        world=NodePath("world"); camera=NodePath("camera"); view=FlightCamera(world,camera)
        for alpha in (0,.25,.5,1):
            pose=view.present(a,b,alpha)
            expected=Vec3(*pose.position)+Quat(*pose.orientation).xform(Vec3(*EYE_OFFSET))
            self.assertLess((camera.getPos(world)-expected).length(),1e-5)
            self.assertLess((camera.getPos()-Vec3(*EYE_OFFSET)).length(),1e-6)
            self.assertAlmostEqual(abs(camera.getQuat(world).dot(Quat(*pose.orientation))),1,places=5)
            self.assertEqual(f.snapshot(),b)
        for low, eye, high in zip(HULL_MIN,EYE_OFFSET,HULL_MAX):
            self.assertLess(low,eye); self.assertLess(eye,high)

if __name__ == "__main__": unittest.main()
