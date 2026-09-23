"""Phusion Passenger / LiteSpeed LSAPI (cPanel "Setup Python App") entry point.

cPanel runs WSGI applications; FastAPI is ASGI, so it is wrapped with a2wsgi.

Two things have to happen on opposite sides of the server's fork, and getting either wrong looks
like the site hanging:

* **Before the fork** - import the geometry stack and build the FastAPI application. numpy, scipy,
  shapely and pyproj take seconds to import; doing that inside the first request lets the web server
  time the request out and kill the worker mid-import.
* **After the fork** - build the a2wsgi wrapper. It runs the ASGI application on an event loop in a
  background thread, and a thread does not survive `fork()`. A wrapper made in the parent leaves
  every worker holding a dead loop: the worker accepts the request and never answers, and the server
  eventually returns "Request Timeout".

So the application is created at import and the wrapper per process, rebuilt whenever the pid
changes.

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

#: built in the parent: no event loop and no thread yet, just the routes and the stores
asgi_app = create_app()

_worker: dict[str, object] = {"pid": None, "wsgi": None}


def application(environ, start_response):
    """WSGI entry point. The a2wsgi wrapper belongs to the worker process that answers."""
    pid = os.getpid()
    if _worker["pid"] != pid:
        _worker["wsgi"] = ASGIMiddleware(asgi_app)
        _worker["pid"] = pid
    return _worker["wsgi"](environ, start_response)  # type: ignore[operator]
