"""Process entry point.

The FastAPI application and its lifespan wiring are added in a later step. For
now this module exposes ``run()`` (the ``hmc-backend`` console script) so the
package is importable and installable while the vertical slice is built up.
"""

from __future__ import annotations


def run() -> None:
    """Launch the ASGI app under Uvicorn with a single worker."""
    import uvicorn

    from hmc_backend.settings import load_settings

    settings = load_settings()
    uvicorn.run(
        "hmc_backend.api.app:app",
        host=settings.bind_host,
        port=settings.port,
        workers=1,
    )


if __name__ == "__main__":
    run()
