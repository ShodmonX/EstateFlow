from __future__ import annotations

import uvicorn

from estateflow.api.app import create_app
from estateflow.application.core.config import get_settings

app = create_app(profile="internal")


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "estateflow.api.internal_app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
