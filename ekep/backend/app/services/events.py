"""WebSocket-Hub: Server → Client Ereignisse pro Interview."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("ekep.ws")


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[WebSocket]] = defaultdict(set)

    async def join(self, interview_id: str, ws: WebSocket) -> None:
        self._subs[interview_id].add(ws)

    def leave(self, interview_id: str, ws: WebSocket) -> None:
        self._subs[interview_id].discard(ws)

    async def publish(self, interview_id: str, event: str, data: dict) -> None:
        msg = json.dumps({"event": event, "data": data}, default=str, ensure_ascii=False)
        dead = []
        for ws in list(self._subs.get(interview_id, ())):
            try:
                await asyncio.wait_for(ws.send_text(msg), timeout=5)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(interview_id, ws)


hub = Hub()
