"""An event loop with a virtual clock, on which waits, such as arXiv's rate limit, take no real time."""

import asyncio
import selectors


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """
    An event loop whose clock stands still while a task can run, and jumps to the next timer once every task waits.
    Sleeps take no real time, yet concurrent sleeps end in the order, and at the times, they would.
    """

    def __init__(self) -> None:
        self.now = 0.0
        super().__init__(ClockJumpingSelector(self))

    def time(self) -> float:
        return self.now


class ClockJumpingSelector(selectors.DefaultSelector):
    """Instead of waiting for its loop's next timer, moves the loop's clock forward to it."""

    def __init__(self, loop: VirtualTimeLoop) -> None:
        super().__init__()
        self.loop = loop

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        if timeout:
            self.loop.now += timeout
            timeout = 0
        return super().select(timeout)


class VirtualTimePolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        return VirtualTimeLoop()
