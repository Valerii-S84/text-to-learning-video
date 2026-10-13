from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

_active_log: ContextVar[logging.Logger | None] = ContextVar("execution_log", default=None)


@contextmanager
def operation(name: str):
    logger = _active_log.get()
    started = perf_counter()
    if logger:
        logger.info("START %s", name)
    try:
        yield
    except BaseException:
        if logger:
            logger.exception("FAIL %s elapsed=%.3fs", name, perf_counter() - started)
        raise
    else:
        if logger:
            logger.info("OK %s elapsed=%.3fs", name, perf_counter() - started)


def traced(function):
    if iscoroutinefunction(function):
        @wraps(function)
        async def async_wrapper(*args, **kwargs):
            with operation(function.__qualname__):
                return await function(*args, **kwargs)
        return async_wrapper

    @wraps(function)
    def wrapper(*args, **kwargs):
        with operation(function.__qualname__):
            return function(*args, **kwargs)
    return wrapper


@contextmanager
def execution_session(log_dir: Path):
    existing = _active_log.get()
    if existing is not None:
        yield Path(existing.handlers[0].baseFilename)
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(prefix="generation-", suffix=".log", dir=log_dir, delete=False) as file:
        path = Path(file.name)
    logger = logging.Logger("anna.execution", level=logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    token = _active_log.set(logger)
    try:
        yield path
    finally:
        _active_log.reset(token)
        handler.close()
        logger.removeHandler(handler)


def logged_run(function):
    @wraps(function)
    async def wrapper(self, *args, **kwargs):
        log_dir = Path(kwargs["tmp_dir"]).expanduser().resolve().parent / "logs"
        with execution_session(log_dir) as path:
            self.last_log_path = path
            with operation(function.__qualname__):
                return await function(self, *args, **kwargs)
    return wrapper
