from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator


class Run:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.events: list[dict] = []
        self.done = False
        self._condition = asyncio.Condition()

    async def publish(self, event: dict) -> None:
        async with self._condition:
            self.events.append(event)
            if event.get("type") == "done":
                self.done = True
            self._condition.notify_all()

    async def subscribe(self) -> AsyncIterator[dict]:
        index = 0
        while True:
            async with self._condition:
                while index >= len(self.events) and not self.done:
                    await self._condition.wait()
                batch = self.events[index:]
                index = len(self.events)
                finished = self.done and index >= len(self.events)
            for event in batch:
                yield event
            if finished:
                return


class RunRegistry:
    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def create(self, cards, adapters, db, force_refresh: bool = False) -> Run:
        from app.orchestrator import run_search

        run = Run()
        self._runs[run.id] = run

        async def _drive():
            async for event in run_search(cards, adapters, db, force_refresh=force_refresh):
                await run.publish(event)

        task = asyncio.create_task(_drive())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run
