"""Bounded 60 Hz accumulator; sample edges once per simulation tick."""
import math
from breach.contracts import FIXED_DT, MAX_FRAME_DT, MAX_STEPS
from breach.flight import interpolate

class FixedStepper:
    def __init__(self, flight):
        self.flight = flight
        self.previous = self.current = flight.snapshot()
        self.accumulator = 0.0
        self.tick = 0
        self.dropped_seconds = 0.0
        self.drop_events = 0

    def advance(self, real_dt, sample, paused=False, on_tick=None):
        if not math.isfinite(real_dt) or real_dt < 0:
            raise ValueError("frame dt must be finite and nonnegative")
        if paused:
            self.accumulator = 0.0
            self.previous = self.current
            return 0
        accepted = min(real_dt, MAX_FRAME_DT)
        dropped = real_dt - accepted
        self.accumulator += accepted
        steps = 0
        while self.accumulator + 1e-12 >= FIXED_DT and steps < MAX_STEPS:
            controls = sample()
            self.previous = self.current
            self.flight.fixed_update(FIXED_DT, controls)
            self.current = self.flight.snapshot()
            self.accumulator = max(0, self.accumulator - FIXED_DT)
            steps += 1
            self.tick += 1
            if on_tick:
                on_tick(self.tick, controls, self.current)
        if self.accumulator >= FIXED_DT:
            backlog = math.floor(self.accumulator / FIXED_DT)*FIXED_DT
            self.accumulator -= backlog
            dropped += backlog
        if dropped > 1e-12:
            self.dropped_seconds += dropped
            self.drop_events += 1
        return steps

    @property
    def alpha(self):
        return max(0, min(1, self.accumulator/FIXED_DT))

    def pose(self):
        return interpolate(self.previous, self.current, self.alpha)
