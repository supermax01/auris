"""Server-clock timing; browser reports focus, visibility and recent human activity."""

from auris.annotation.models import Timing

IDLE_SECONDS = 60.0


class EncounterClock:
    def __init__(self, previous: Timing, now: float) -> None:
        self.previous = previous
        self.started = self.last = now
        self.active = 0.0
        self.focused = False
        self.activity_at = now - IDLE_SECONDS

    def event(self, now: float, *, focused: bool, idle_seconds: float) -> Timing:
        if now < self.last or not 0 <= idle_seconds:
            raise ValueError("invalid timing event")
        # Credit only intervals after a foreground report, capped at the last
        # reported interaction + 60s. A sleeping tab cannot credit its entire gap.
        if self.focused:
            self.active += max(0.0, min(now, self.activity_at + IDLE_SECONDS) - self.last)
        self.last = now
        self.focused = focused
        self.activity_at = now - min(idle_seconds, IDLE_SECONDS)
        return Timing(
            active_seconds=self.previous.active_seconds + self.active,
            wall_seconds=self.previous.wall_seconds + now - self.started,
        )
