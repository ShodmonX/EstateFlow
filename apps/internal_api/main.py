from __future__ import annotations

import uvicorn

from estateflow.api.internal_app import app
from estateflow.application.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
