"""Phusion Passenger (cPanel "Setup Python App") entry point.

cPanel runs WSGI applications; FastAPI is ASGI, so it is wrapped with a2wsgi.
Environment variables (set them in the cPanel Python App screen):
    PLM_DATA_DIR=/home/<user>/plm_data      (outside public_html!)
    PLM_AUTH_ENABLED=1
    PLM_WEB_DIST=/home/<user>/plm/web/dist  (built front end)
    PLM_CORS_ORIGINS=https://your-domain.example
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PLM_DATA_DIR", str(HERE.parent / "plm_data"))

from a2wsgi import ASGIMiddleware  # noqa: E402

from plm.api.main import create_app  # noqa: E402

application = ASGIMiddleware(create_app())
