"""
Small local server for Trend Research.

It serves the static files when run directly and exposes POST /api/run-report
so the Generate Report button can run trendscout.py for real.
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.environ.get("TREND_OUTPUT_DIR", HERE)).resolve()
STATIC_DIR = Path(os.environ.get("TREND_STATIC_DIR", OUTPUT_DIR)).resolve()
RUN_LOCK = threading.Lock()
LAST_RUN_AT = 0.0


def split_values(value):
    text = str(value or "").replace("\r", "\n").replace(";", "\n")
    parts = []
    for line in text.split("\n"):
        parts.extend(line.split(","))
    return [part.strip() for part in parts if part.strip()]


def custom_keywords(text):
    terms = split_values(text)
    if terms:
        return "\n".join(terms[:8])
    text = " ".join(str(text or "").split())
    return text[:180]


def config_has_serpapi_key():
    config_path = HERE / "config.json"
    if not config_path.exists():
        return False
    try:
        with config_path.open(encoding="utf-8") as f:
            key = str(json.load(f).get("serpapi_key", "")).strip()
    except Exception:
        return False
    return bool(key and key != "PASTE_SERPAPI_KEY_HERE")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 20000:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/run-report":
            self.send_error(404)
            return

        try:
            payload = self.read_json()
        except Exception:
            self.send_json(400, {"ok": False, "message": "Could not read the report request."})
            return

        mode = str(payload.get("mode") or "top_trends").strip()
        if mode not in {"top_trends", "most_potential", "custom"}:
            mode = "top_trends"

        request_text = str(payload.get("request") or "").strip()
        if mode == "custom" and not request_text:
            self.send_json(400, {"ok": False, "message": "Enter keywords or choose Top Trends or Most Potential."})
            return

        if not os.environ.get("SERPAPI_KEY") and not config_has_serpapi_key():
            self.send_json(400, {
                "ok": False,
                "message": "The live search key is missing on the server, so a new report cannot run yet."
            })
            return

        global LAST_RUN_AT
        cooldown = int(os.environ.get("RUN_COOLDOWN_SECONDS", "300"))
        now = time.time()
        if now - LAST_RUN_AT < cooldown:
            wait = max(1, int(cooldown - (now - LAST_RUN_AT)))
            self.send_json(429, {"ok": False, "message": f"Please wait {wait} seconds before running another report."})
            return

        if not RUN_LOCK.acquire(blocking=False):
            self.send_json(409, {"ok": False, "message": "A report is already running. Please wait a minute."})
            return

        try:
            env = os.environ.copy()
            env.setdefault("MAX_SEARCHES", "3")
            env["SEARCH_MODE"] = mode
            env["TREND_OUTPUT_DIR"] = str(OUTPUT_DIR)

            area = str(payload.get("area") or "").strip()
            if area:
                env["MARKET_LOCATIONS"] = "\n".join(split_values(area))

            if mode == "custom":
                env["CUSTOM_KEYWORDS"] = custom_keywords(request_text)

            proc = subprocess.run(
                [sys.executable, str(HERE / "trendscout.py"), "live", mode],
                cwd=str(HERE),
                env=env,
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("REPORT_TIMEOUT_SECONDS", "240")),
            )
            if proc.returncode != 0:
                lines = (proc.stderr or proc.stdout or "").strip().splitlines()
                message = lines[-1] if lines else "The report did not finish."
                self.send_json(500, {"ok": False, "message": message})
                return

            LAST_RUN_AT = time.time()
            results_path = OUTPUT_DIR / "results.json"
            with results_path.open(encoding="utf-8") as f:
                results = json.load(f)
            self.send_json(200, {"ok": True, "message": "New report generated.", "results": results})
        except subprocess.TimeoutExpired:
            self.send_json(504, {"ok": False, "message": "The report took too long and was stopped."})
        except Exception as exc:
            self.send_json(500, {"ok": False, "message": f"Report failed: {exc}"})
        finally:
            RUN_LOCK.release()


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8099"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Trend Research server running on http://{host}:{port}")
    print(f"Static files: {STATIC_DIR}")
    print(f"Report output: {OUTPUT_DIR}")
    server.serve_forever()


if __name__ == "__main__":
    main()
