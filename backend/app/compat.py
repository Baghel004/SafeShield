"""Platform compatibility shims."""

import sys

# uvicorn's `loop` setting accepts either a known name ("auto", "asyncio",
# "uvloop") or a "module:attr" import string resolving to a zero-argument
# callable that returns an event loop.
_SELECTOR_LOOP_FACTORY = "asyncio:SelectorEventLoop"


def uvicorn_loop_setting() -> str:
    """Return the event loop uvicorn should build.

    On Windows, uvicorn hardcodes ProactorEventLoop (see
    `uvicorn.loops.asyncio.asyncio_loop_factory`). psycopg3 cannot run on that
    loop in async mode -- every database connection fails with
    `InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`.

    This cannot be fixed from application code. Since uvicorn 0.36 the loop is
    created by passing an explicit `loop_factory` to `asyncio.run`, so uvicorn
    never consults the asyncio event loop policy; setting that policy at import
    time has no effect. Supplying the factory through this setting is the
    supported override.

    Off Windows this returns "auto", letting uvicorn choose uvloop when present.
    """
    return _SELECTOR_LOOP_FACTORY if sys.platform == "win32" else "auto"
