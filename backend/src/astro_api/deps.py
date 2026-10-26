"""Shared application services, created once in the lifespan and read from app.state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from .call_service import CallService
from .db import Database
from .settings import Settings
from .snapshot_service import SnapshotService
from .speech.deepgram import Deepgram
from .store import Store


@dataclass
class Services:
    settings: Settings
    db: Database
    redis: Redis
    store: Store
    snapshots: SnapshotService
    calls: CallService
    speech: Deepgram


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


Svc = Annotated[Services, Depends(services)]
