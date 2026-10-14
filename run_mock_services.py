"""
Local runner for mock services (PIM API and WMS API).

Loads the mock services from:
- mock-services/product-api/app/main.py (port 8001)
- mock-services/warehouse-api/app/main.py (port 8002)

Does NOT modify the mock service source code on disk. Uses in-memory
loading with eval-type-backport to ensure compatibility across all
Python environments (3.9 through 3.12+).

Also fixes the Starlette JSONResponse signature compatibility issue
(where the mock service passes status_code as first positional argument
instead of content) so that rate limits (429) and transient errors (503)
are returned as their real HTTP status codes with Retry-After headers
instead of crashing into spurious 500 Internal Server Errors.
"""

import sys
import multiprocessing
import uvicorn
import eval_type_backport
import fastapi.responses
import starlette.responses

# Compatibility patch for Starlette/FastAPI JSONResponse signature
_orig_json_init = starlette.responses.JSONResponse.__init__


def _patched_json_init(
    self,
    content=None,
    status_code=200,
    headers=None,
    media_type=None,
    background=None,
):
    if isinstance(content, int) and isinstance(status_code, (dict, list, str)):
        # Correct inverted arguments from mock service:
        # JSONResponse(429, {"detail": ...}) -> JSONResponse(content={"detail": ...}, status_code=429)
        _orig_json_init(
            self,
            content=status_code,
            status_code=content,
            headers=headers,
            media_type=media_type,
            background=background,
        )
    else:
        _orig_json_init(
            self,
            content=content,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )


starlette.responses.JSONResponse.__init__ = _patched_json_init
fastapi.responses.JSONResponse.__init__ = _patched_json_init


def load_fastapi_app(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        code = "from __future__ import annotations\n" + f.read()
    compiled = compile(code, filepath, "exec")
    mod = {"__file__": filepath, "__name__": "__main__"}
    exec(compiled, mod)
    return mod["app"]


def run_product_api():
    app = load_fastapi_app("mock-services/product-api/app/main.py")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")


def run_warehouse_api():
    app = load_fastapi_app("mock-services/warehouse-api/app/main.py")
    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="warning")


def main():
    p1 = multiprocessing.Process(target=run_product_api, daemon=True)
    p2 = multiprocessing.Process(target=run_warehouse_api, daemon=True)

    p1.start()
    p2.start()

    print("Mock Product API running on http://127.0.0.1:8001")
    print("Mock Warehouse API running on http://127.0.0.1:8002")

    try:
        p1.join()
        p2.join()
    except KeyboardInterrupt:
        print("\nStopping mock services...")
        p1.terminate()
        p2.terminate()


if __name__ == "__main__":
    main()
