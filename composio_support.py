from __future__ import annotations

import os
from pathlib import Path


def prepare_composio_imports(cache_dir: Path | None = None) -> None:
    if cache_dir is not None:
        resolved_cache_dir = str(cache_dir)
        os.environ["COMPOSIO_CACHE_DIR"] = resolved_cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        import composio.client as composio_client_mod

        if not hasattr(composio_client_mod, "DEFAULT_MAX_RETRIES"):
            try:
                import composio_client as composio_client_pkg

                composio_client_mod.DEFAULT_MAX_RETRIES = getattr(
                    composio_client_pkg,
                    "DEFAULT_MAX_RETRIES",
                    2,
                )
            except Exception:
                composio_client_mod.DEFAULT_MAX_RETRIES = 2
    except Exception:
        return


def load_composio_class(cache_dir: Path | None = None):
    prepare_composio_imports(cache_dir)
    from composio.sdk import Composio

    return Composio
