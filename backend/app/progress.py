from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock
from time import time


@dataclass
class TaskState:
    active: bool = False
    name: str = ""
    stage: str = "idle"
    current: int = 0
    total: int = 0
    message: str = "Idle"
    started_at: float | None = None
    updated_at: float | None = None


_LOCK = Lock()
_STATE = TaskState()


def start_task(name: str, total: int = 0, message: str | None = None) -> None:
    now = time()
    with _LOCK:
        _STATE.active = True
        _STATE.name = name
        _STATE.stage = "starting"
        _STATE.current = 0
        _STATE.total = total
        _STATE.message = message or f"Starting {name}"
        _STATE.started_at = now
        _STATE.updated_at = now


def update_task(stage: str, current: int | None = None, total: int | None = None, message: str | None = None) -> None:
    with _LOCK:
        _STATE.stage = stage
        if current is not None:
            _STATE.current = current
        if total is not None:
            _STATE.total = total
        if message is not None:
            _STATE.message = message
        _STATE.updated_at = time()


def finish_task(message: str = "Done") -> None:
    with _LOCK:
        _STATE.active = False
        _STATE.stage = "done"
        if _STATE.total:
            _STATE.current = _STATE.total
        _STATE.message = message
        _STATE.updated_at = time()


def fail_task(message: str) -> None:
    with _LOCK:
        _STATE.active = False
        _STATE.stage = "error"
        _STATE.message = message
        _STATE.updated_at = time()


def get_task() -> dict[str, object]:
    with _LOCK:
        data = asdict(_STATE)
    if data["total"]:
        data["percent"] = round(min(100.0, float(data["current"]) / float(data["total"]) * 100), 1)
    else:
        data["percent"] = 0.0
    return data
