"""
Fault injection for the CoGNETs Swarm Arena.

Real edge networks drop requests, rate-limit, and go slow. The graded scenario
turns this on so that resilience is something a team has to earn rather than
something they get for free on a quiet laptop.

Two endpoints are never faulted:

* ``/v1/register`` - a team that cannot register cannot start, and that is a
  support call rather than a lesson.
* ``/healthz`` - the compose healthcheck depends on it.

And the three baseline bots are never faulted either. That is not a favour to
them; it is what makes the strategy score mean anything. The bots are the
market you bid against, and your strategy points are worked out by re-running
this same market with the template's strategy and with the reference agent in
your place. If the bots dropped rounds to injected faults, your run would have
faced a thinner market than those two comparisons did, and you would be paid
for the arena's dice rather than for your own ideas. Measured before this was
fixed, that alone paid an agent that had changed nothing 65% of the strategy
band. Fault injection stays exactly where it belongs: on the agents being
scored for resilience, which is you.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import asyncio
import random
from typing import AbstractSet, Callable, Iterable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

#: Paths that are never faulted, matched by prefix.
#:
#: Two groups. First, anything a team needs before it can even start - a team
#: that cannot register cannot begin, and that is a support call rather than a
#: lesson. Second, the read-only observability endpoints: they are not part of
#: the agent's required protocol, they drive the projector leaderboard, and the
#: conformance and grading harnesses poll them to judge the run. Faulting those
#: would make the dashboard flicker and the harness unreliable without testing
#: anything worth testing.
#:
#: Chaos stays exactly where resilience actually matters: /v1/round, /v1/bid,
#: /v1/heartbeat, /v1/result and /v1/me - the agent's critical path.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/healthz",
    "/v1/register",
    "/v1/leaderboard",
    "/v1/status",
    "/v1/swarm",
    "/v1/events",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static",
)


class ChaosMiddleware:
    """Pure-ASGI middleware that injects 503s, 429s and latency.

    Implemented at the ASGI layer rather than as a ``BaseHTTPMiddleware`` so it
    adds no measurable overhead when ``chaos_rate`` is zero, which is the case
    for every practice run.
    """

    def __init__(
        self,
        app,
        chaos_rate: float = 0.0,
        latency_ms: int = 0,
        seed: int = 0,
        latency_paths: Iterable[str] = ("/v1/bid",),
        exempt_prefixes: Iterable[str] = EXEMPT_PREFIXES,
        faultless_tokens: Optional[AbstractSet[str]] = None,
    ) -> None:
        self.app = app
        self.chaos_rate = max(0.0, float(chaos_rate))
        self.latency_ms = max(0, int(latency_ms))
        self.latency_paths = tuple(latency_paths)
        self.exempt_prefixes = tuple(exempt_prefixes)
        # A live reference to the arena's set, not a copy: bots register after
        # the app is built. Only the arena writes to it, and only for teams
        # named after a configured baseline.
        self.faultless_tokens = (faultless_tokens if faultless_tokens is not None
                                 else frozenset())
        # Its own RNG so injected faults never perturb the game's seeded draws.
        self._rng = random.Random(seed ^ 0x5EED)
        self.injected_503 = 0
        self.injected_429 = 0

    # ------------------------------------------------------------------ ASGI
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        if self._is_exempt(path) or self._is_faultless(scope):
            await self.app(scope, receive, send)
            return

        if self.chaos_rate > 0.0:
            roll = self._rng.random()
            if roll < self.chaos_rate:
                self.injected_503 += 1
                await self._send_json(
                    send,
                    503,
                    {
                        "detail": "arena temporarily unavailable (injected fault)",
                        "injected": True,
                    },
                    headers=[(b"retry-after", b"1")],
                )
                return
            if roll < self.chaos_rate * 1.5:
                self.injected_429 += 1
                await self._send_json(
                    send,
                    429,
                    {
                        "detail": "too many requests (injected fault)",
                        "injected": True,
                    },
                    headers=[(b"retry-after", b"1")],
                )
                return

        if self.latency_ms > 0 and path.startswith(self.latency_paths):
            await asyncio.sleep(self._rng.uniform(0.0, self.latency_ms / 1000.0))

        await self.app(scope, receive, send)

    # --------------------------------------------------------------- helpers
    def _is_exempt(self, path: str) -> bool:
        return path == "/" or path.startswith(self.exempt_prefixes)

    def _is_faultless(self, scope) -> bool:
        """True for a request carrying a baseline bot's bearer token."""
        if not self.faultless_tokens:
            return False
        for name, value in scope.get("headers", ()):
            if name == b"authorization":
                raw = value.decode("latin-1", "ignore").split(None, 1)
                if len(raw) == 2 and raw[0].lower() == "bearer":
                    return raw[1].strip() in self.faultless_tokens
                return False
        return False

    @staticmethod
    async def _send_json(send: Callable, status: int, payload: dict, headers=()) -> None:
        response = JSONResponse(status_code=status, content=payload)
        raw_headers = list(response.raw_headers) + list(headers)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": raw_headers,
            }
        )
        await send({"type": "http.response.body", "body": response.body})

    def stats(self) -> dict:
        return {
            "chaos_rate": self.chaos_rate,
            "latency_ms": self.latency_ms,
            "injected_503": self.injected_503,
            "injected_429": self.injected_429,
        }
