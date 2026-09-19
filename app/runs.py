from __future__ import annotations

import asyncio
import threading
import uuid
from typing import AsyncIterator


class Run:
    """A single search run.

    Events are produced by a background thread driving :func:`app.orchestrator.run_search`
    and consumed by one or more SSE subscribers, each of which typically lives on a
    different event loop (the ASGI server may hand requests to any worker).  State is
    therefore guarded by a plain :class:`threading.Condition` rather than loop-bound
    asyncio primitives, so ``subscribe`` can replay existing events and then follow new
    ones from any loop, including across client reconnects.
    """

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.events: list[dict] = []
        self.done = False
        self._condition = threading.Condition()

    def publish(self, event: dict) -> None:
        with self._condition:
            self.events.append(event)
            if event.get("type") == "done":
                self.done = True
            self._condition.notify_all()

    def _wait_for_more(self, index: int) -> None:
        with self._condition:
            while index >= len(self.events) and not self.done:
                self._condition.wait()

    async def subscribe(self) -> AsyncIterator[dict]:
        index = 0
        while True:
            with self._condition:
                batch = self.events[index:]
                index += len(batch)
                finished = self.done
            for event in batch:
                yield event
            if finished:
                return
            await asyncio.to_thread(self._wait_for_more, index)


class RunRegistry:
    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._threads: set[threading.Thread] = set()

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def create(self, cards, adapters, db, force_refresh: bool = False) -> Run:
        from app.orchestrator import run_search

        run = Run()
        self._runs[run.id] = run

        def _drive() -> None:
            async def _consume() -> None:
                async for event in run_search(cards, adapters, db, force_refresh=force_refresh):
                    run.publish(event)

            asyncio.run(_consume())

        thread = threading.Thread(target=_drive, name=f"run-{run.id}", daemon=True)
        self._threads.add(thread)
        thread.start()
        return run
