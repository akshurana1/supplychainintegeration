"""
Local runner for mock services (PIM API and WMS API).

Loads the mock services from:
- mock-services/product-api/app/main.py (port 8001)
- mock-services/warehouse-api/app/main.py (port 8002)

Does NOT modify the mock service source code on disk. Uses in-memory
loading with eval-type-backport to ensure compatibility across all
Python environments (3.9 through 3.12+).
"""

import sys
import multiprocessing
import uvicorn
import eval_type_backport


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
