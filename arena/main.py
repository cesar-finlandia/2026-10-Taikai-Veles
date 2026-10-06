"""
FastAPI application for the CoGNETs Swarm Arena.

Routes are documented in docs/api.md; Swagger UI is served at /docs.

Copyright 2026 The CoGNETs Consortium
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chaos import ChaosMiddleware
from .config import ScenarioConfig
from .state import Arena, ArenaError, NodeState

LOG = logging.getLogger("arena")
STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    team: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Your team name. Your device profile is derived from it, "
        "so it is stable across every run you do.",
        examples=["team-kappa"],
    )


class BidRequest(BaseModel):
    round: Optional[int] = Field(
        None,
        description="The round you believe is open. Sending it lets the arena "
        "reject a stale bid with 422 instead of silently accepting it.",
        examples=[12],
    )
    bid: Dict[str, float] = Field(
        default_factory=dict,
        description="Your bid per resource. Every value >= 0, and the sum must "
        "not exceed your budget for this round.",
        examples=[{"compute": 0.51, "energy": 0.32, "security": 0.24}],
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app(config: Optional[ScenarioConfig] = None) -> FastAPI:
    config = config or ScenarioConfig.load()
    arena = Arena(config)

    app = FastAPI(
        title="CoGNETs Swarm Arena",
        version="1.0.0",
        description=(
            "Register an edge node, then bid each round for shares of three "
            "scarce resource pools under a budget. Allocation is a Kelly "
            "proportional share; payoff is a CES utility with minimum service "
            "floors.\n\n"
            "See `docs/api.md` and `docs/strategy-primer.md` in the repository."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.state.arena = arena
    app.state.config = config

    chaos = ChaosMiddleware(
        app,
        chaos_rate=config.chaos_rate,
        latency_ms=config.latency_ms,
        seed=config.seed,
        faultless_tokens=arena.faultless_tokens,
    )
    app.state.chaos = chaos
    # Wrap the app itself so the middleware sits outermost.
    app.add_middleware(
        _PassthroughChaos,
        chaos=chaos,
    )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    _register_routes(app, arena)
    _register_lifecycle(app, arena)
    return app


class _PassthroughChaos:
    """Adapter so ChaosMiddleware can be installed via add_middleware()."""

    def __init__(self, app, chaos: ChaosMiddleware) -> None:
        chaos.app = app
        self._chaos = chaos

    async def __call__(self, scope, receive, send):
        await self._chaos(scope, receive, send)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def _bearer(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="missing Authorization header; send 'Bearer <token>'",
        )
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401, detail="Authorization header must be 'Bearer <token>'"
        )
    return parts[1].strip()


async def _current_node(request: Request, authorization: str = Header(None)) -> NodeState:
    arena: Arena = request.app.state.arena
    token = _bearer(authorization)
    try:
        return arena.authenticate(token)
    except ArenaError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def _register_routes(app: FastAPI, arena: Arena) -> None:

    @app.exception_handler(ArenaError)
    async def _arena_error_handler(_request: Request, exc: ArenaError):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    # ------------------------------------------------------------- health
    @app.get("/healthz", tags=["meta"], summary="Liveness probe (never faulted)")
    async def healthz() -> Dict[str, Any]:
        return {"ok": True, "scenario": arena.config.name}

    @app.get("/", include_in_schema=False)
    async def index():
        page = STATIC_DIR / "index.html"
        if page.is_file():
            return FileResponse(str(page))
        return JSONResponse({"detail": "leaderboard page not installed"}, status_code=404)

    # ----------------------------------------------------------- registry
    @app.post("/v1/register", tags=["registry"], summary="Join the swarm")
    async def register(body: RegisterRequest) -> Dict[str, Any]:
        node = arena.register(body.team)
        return {
            "node_id": node.node_id,
            "token": node.token,
            "profile": node.profile(),
            "arena": arena.config.public(),
        }

    @app.post("/v1/heartbeat", tags=["registry"], summary="Renew your lease")
    async def heartbeat(node: NodeState = Depends(_current_node)) -> Dict[str, Any]:
        return arena.heartbeat(node)

    # ------------------------------------------------------------ auction
    @app.get("/v1/round", tags=["auction"], summary="The round currently open")
    async def get_round(
        request: Request, authorization: Optional[str] = Header(None)
    ) -> Dict[str, Any]:
        state = arena.current
        if state is None:
            raise HTTPException(
                status_code=404,
                detail="no round is open yet; the arena is still starting",
            )
        payload = state.public()
        # Sending no Authorization header is fine and returns the public view.
        # Sending one the arena does not accept is answered with 401 rather
        # than quietly served that same view: a client cannot tell the two
        # apart, and an agent that read a round with no `budget` in it would
        # bid zero for the round instead of re-registering.
        if authorization:
            try:
                node = arena.authenticate(_bearer(authorization))
            except ArenaError as exc:
                raise HTTPException(
                    status_code=exc.status, detail=str(exc)
                ) from exc
            payload["budget"] = round(node.budget, 6)
            payload["you"] = {
                "node_id": node.node_id,
                "already_submitted": node.node_id in state.bids,
                "admissible": node.admissible(
                    arena.config.battery_cutoff, arena.config.kappa_bar
                ),
                "battery": round(node.battery, 4),
                "compromise": round(node.compromise, 4),
            }
        return payload

    @app.post("/v1/bid", tags=["auction"], summary="Submit this round's bid")
    async def post_bid(
        body: BidRequest, node: NodeState = Depends(_current_node)
    ) -> Dict[str, Any]:
        return arena.submit(node, body.round, body.bid)

    @app.get(
        "/v1/result/{round_index}",
        tags=["auction"],
        summary="What you were allocated and scored in a settled round",
    )
    async def get_result(
        round_index: int, node: NodeState = Depends(_current_node)
    ) -> Dict[str, Any]:
        return arena.result_for(node, round_index)

    # --------------------------------------------------------------- views
    @app.get("/v1/me", tags=["views"], summary="Your full node state")
    async def me(node: NodeState = Depends(_current_node)) -> Dict[str, Any]:
        return arena.me(node)

    @app.get("/v1/leaderboard", tags=["views"], summary="Ranked standings")
    async def leaderboard() -> Dict[str, Any]:
        return {"leaderboard": arena.leaderboard(), "status": arena.status()}

    @app.get("/v1/swarm", tags=["views"], summary="Every node's public profile")
    async def swarm() -> Dict[str, Any]:
        return {"nodes": arena.swarm()}

    @app.get("/v1/status", tags=["views"], summary="Run status and LSW series")
    async def status(request: Request) -> Dict[str, Any]:
        payload = arena.status()
        payload["faults"] = request.app.state.chaos.stats()
        return payload

    @app.get("/v1/events", tags=["views"], summary="Recent arena events (debugging)")
    async def events(limit: int = 100) -> Dict[str, Any]:
        limit = max(1, min(limit, 500))
        return {"events": arena.event_log[-limit:]}


# ---------------------------------------------------------------------------
# Round scheduler
# ---------------------------------------------------------------------------
def _register_lifecycle(app: FastAPI, arena: Arena) -> None:
    config = arena.config

    async def scheduler() -> None:
        """Open, wait, settle, repeat - until the run is finished."""
        LOG.info(
            "scenario=%s rounds=%d round_seconds=%.1f chaos=%.2f seed=%d",
            config.name,
            config.total_rounds,
            config.round_seconds,
            config.chaos_rate,
            config.seed,
        )
        LOG.info("waiting %.1fs for agents to register", config.start_delay_seconds)
        await asyncio.sleep(config.start_delay_seconds)

        while not arena.finished:
            state = arena.open_round()
            deadline = state.closes_at
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.25, remaining))
                arena.expire_leases()
            arena.settle()
            LOG.info(
                "round %d/%d settled  participants=%d  lsw=%.3f",
                state.index,
                config.total_rounds,
                len(state.results),
                state.lsw,
            )

        LOG.info("run finished after %d rounds", len(arena.rounds))
        for row in arena.leaderboard()[:10]:
            LOG.info(
                "  #%d %-24s %8.3f  (%d rounds, %d missed)",
                row["rank"],
                row["team"],
                row["score"],
                row["rounds_participated"],
                row["rounds_missed"],
            )

    async def lease_sweeper() -> None:
        while True:
            await asyncio.sleep(1.0)
            arena.expire_leases()

    @app.on_event("startup")
    async def _startup() -> None:
        app.state.tasks = []
        if config.autostart:
            app.state.tasks.append(asyncio.create_task(scheduler()))
        app.state.tasks.append(asyncio.create_task(lease_sweeper()))

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        for task in getattr(app.state, "tasks", []):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


# ---------------------------------------------------------------------------
# Module-level app for `uvicorn arena.main:app`
# ---------------------------------------------------------------------------
def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


_configure_logging()
app = create_app()
