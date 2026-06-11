"""
Protected Trend Research server.

Uses the existing store portal users.json password hashes, allows Jamie by
default, protects report files, runs live reports, and saves report history.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = Path(os.environ.get("TREND_OUTPUT_DIR", HERE)).resolve()
STATIC_DIR = Path(os.environ.get("TREND_STATIC_DIR", OUTPUT_DIR)).resolve()
STATE_DIR = Path(os.environ.get("TREND_STATE_DIR", HERE / "data")).resolve()
HISTORY_DIR = STATE_DIR / "history"
RUN_LOCK = threading.Lock()
SESSIONS = {}
LAST_RUN_AT = 0.0
SESSION_COOKIE = "trend_research_sid"
PUBLIC_PATHS = {"/", "/index.html"}
REPORT_FILES = {"results.json", "report.html", "report.pdf", "top5_report.txt", "jamify_results.xlsx"}


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value):
    return " ".join(str(value or "").strip().split())


def normalize_username(value):
    return "".join(ch for ch in clean(value).lower() if ch.isalnum() or ch in "._-")


def normalize_email(value):
    return clean(value).lower()


def split_values(value):
    text = str(value or "").replace("\r", "\n").replace(";", "\n")
    parts = []
    for line in text.split("\n"):
        parts.extend(line.split(","))
    return [part.strip() for part in parts if part.strip()]


def read_json(path, fallback):
    try:
        with Path(path).open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def app_config():
    return read_json(HERE / "config.json", {})


def allowed_users():
    configured = os.environ.get("ALLOWED_USERS") or app_config().get("allowed_users") or ["jamie"]
    if isinstance(configured, str):
        configured = split_values(configured)
    return {normalize_username(item) for item in configured if normalize_username(item)}


def portal_user_path():
    configured = os.environ.get("PORTAL_USERS_PATH") or app_config().get("portal_users_path")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([
        HERE.parents[1] / "CUSTOMERPORTAL_NEXT" / "data" / "settings" / "users.json",
        Path("/opt/apps/customerportal/data/settings/users.json"),
        Path("/opt/apps/customerportal/app/data/settings/users.json"),
        Path("/opt/apps/contractsportal/data/settings/users.json"),
        Path("/opt/apps/installerportal/app/data/settings/users.json"),
    ])
    for path in candidates:
        if path.exists():
            return path.resolve()
    return candidates[0].resolve() if candidates else None


def b64url(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def verify_password(password, password_hash):
    parts = str(password_hash or "").split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    _kind, n, r, p, salt, expected = parts
    try:
        key = hashlib.scrypt(
            str(password).encode("utf-8"),
            salt=salt.encode("utf-8"),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
    except Exception:
        return False
    return hmac.compare_digest(b64url(key), expected)


def public_user(user):
    return {
        "id": user.get("id", ""),
        "username": user.get("username", ""),
        "name": user.get("name", user.get("username", "")),
        "role": user.get("role", ""),
    }


def authenticate(username, password):
    users_path = portal_user_path()
    if not users_path or not users_path.exists():
        return None, "The store portal user file is not connected on this server."
    store = read_json(users_path, {}) if users_path else {}
    login = normalize_username(username)
    login_email = normalize_email(username)
    user = next((item for item in store.get("staff", []) if (
        normalize_username(item.get("username")) == login
        or (login_email and normalize_email(item.get("email")) == login_email)
    )), None)
    if not user or user.get("disabled") or not verify_password(password, user.get("passwordHash")):
        return None, "Login was not accepted."
    if normalize_username(user.get("username")) not in allowed_users():
        return None, "This tool is only available for Jamie."
    return public_user(user), ""


def session_ttl():
    return int(os.environ.get("SESSION_TTL_SECONDS", str(8 * 60 * 60)))


def cookie_is_secure(handler):
    configured = os.environ.get("COOKIE_SECURE", "").lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    return handler.headers.get("X-Forwarded-Proto", "").lower() == "https"


def make_cookie(handler, token="", max_age=0):
    parts = [
        f"{SESSION_COOKIE}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if cookie_is_secure(handler):
        parts.append("Secure")
    return "; ".join(parts)


def parse_cookie(header):
    jar = cookies.SimpleCookie()
    try:
        jar.load(header or "")
    except Exception:
        return ""
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value if morsel else ""


def prune_sessions():
    now = time.time()
    for token, session in list(SESSIONS.items()):
        if session.get("expires", 0) <= now:
            SESSIONS.pop(token, None)


def get_session(handler):
    prune_sessions()
    token = parse_cookie(handler.headers.get("Cookie"))
    session = SESSIONS.get(token)
    if not session:
        return None
    session["expires"] = time.time() + session_ttl()
    return session


def max_searches():
    return int(os.environ.get("MAX_SEARCHES", str(app_config().get("max_searches", 3))))


def cooldown_seconds():
    return int(os.environ.get("RUN_COOLDOWN_SECONDS", "300"))


def custom_keywords(text):
    terms = split_values(text)
    if terms:
        return "\n".join(terms[:8])
    return clean(text)[:180]


def history_index_path():
    return STATE_DIR / "history.json"


def load_history():
    return read_json(history_index_path(), {"reports": []})


def report_summary(results, request_payload=None):
    meta = results.get("meta", {})
    usage = meta.get("search_usage", {})
    rows = results.get("rows", [])
    return {
        "id": meta.get("report_id", ""),
        "generated_on": meta.get("generated_on", ""),
        "generated_at": meta.get("generated_at", ""),
        "mode": meta.get("search_label") or meta.get("search_mode", ""),
        "area": ", ".join(meta.get("markets", [])[:6]),
        "request": clean((request_payload or {}).get("request", "")),
        "result_count": len(rows),
        "top_product": rows[0].get("keyword", "") if rows else "",
        "top_score": rows[0].get("score", "") if rows else "",
        "live_calls": usage.get("live_calls", 0),
        "cache_hits": usage.get("cache_hits", 0),
        "max_searches": usage.get("max_searches", max_searches()),
    }


def save_history(results, request_payload):
    report_id = utc_now().strftime("%Y%m%d-%H%M%S")
    results.setdefault("meta", {})["report_id"] = report_id
    results["meta"]["generated_at"] = iso_now()
    write_json(OUTPUT_DIR / "results.json", results)

    report_dir = HISTORY_DIR / report_id
    report_dir.mkdir(parents=True, exist_ok=True)
    for name in REPORT_FILES:
        src = OUTPUT_DIR / name
        if src.exists():
            shutil.copy2(src, report_dir / name)

    summary = report_summary(results, request_payload)
    summary["id"] = report_id
    history = load_history()
    reports = [item for item in history.get("reports", []) if item.get("id") != report_id]
    reports.insert(0, summary)
    write_json(history_index_path(), {"reports": reports[:30]})
    write_json(STATE_DIR / "last_run.json", summary)
    return summary


def status_payload():
    history = load_history().get("reports", [])
    results = read_json(OUTPUT_DIR / "results.json", {})
    meta = results.get("meta", {})
    usage = meta.get("search_usage", {})
    users_path = portal_user_path()
    return {
        "ok": True,
        "lastRun": history[0] if history else report_summary(results),
        "history": history[:10],
        "usage": {
            "maxSearchesPerRun": max_searches(),
            "cooldownSeconds": cooldown_seconds(),
            "lastLiveCalls": usage.get("live_calls", 0),
            "lastCacheHits": usage.get("cache_hits", 0),
            "lastSearchedTerms": usage.get("searched_terms", len(meta.get("searched_keywords", []))),
        },
        "sources": [
            "Google Shopping prices, sellers, ratings, and sample stores",
            "Google Trends, Reddit, Etsy, and Amazon check links for each product",
            "Local area terms from Volusia County / Edgewater / New Smyrna / Oak Hill / Titusville",
        ],
        "auth": {
            "allowedUsers": sorted(allowed_users()),
            "usersPathConfigured": bool(users_path and users_path.exists()),
        },
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 20000:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def current_session(self):
        return get_session(self)

    def require_auth(self):
        session = self.current_session()
        if session:
            return session
        if self.path.startswith("/api/"):
            self.send_json(401, {"ok": False, "message": "Jamie login required.", "loginRequired": True})
        else:
            self.send_error(403, "Jamie login required.")
        return None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/session":
            session = self.current_session()
            payload = {"authenticated": bool(session), "user": session.get("user") if session else None}
            self.send_json(200, payload)
            return
        if path == "/api/status":
            if not self.require_auth():
                return
            self.send_json(200, status_payload())
            return
        if path == "/api/history":
            if not self.require_auth():
                return
            self.send_json(200, {"ok": True, "history": load_history().get("reports", [])[:30]})
            return
        if path.startswith("/api/history/"):
            if not self.require_auth():
                return
            return self.serve_history_file(path)
        if path not in PUBLIC_PATHS and not self.require_auth():
            return
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/login":
            return self.handle_login()
        if path == "/api/logout":
            return self.handle_logout()
        if path == "/api/run-report":
            if not self.require_auth():
                return
            return self.handle_run_report()
        self.send_error(404)

    def handle_login(self):
        try:
            payload = self.read_json()
        except Exception:
            self.send_json(400, {"ok": False, "message": "Could not read the login."})
            return
        user, message = authenticate(payload.get("username", ""), payload.get("password", ""))
        if not user:
            self.send_json(401, {"ok": False, "message": message})
            return
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = {"user": user, "expires": time.time() + session_ttl()}
        self.send_response(200)
        body = json.dumps({"ok": True, "user": user}).encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", make_cookie(self, token, session_ttl()))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_logout(self):
        token = parse_cookie(self.headers.get("Cookie"))
        SESSIONS.pop(token, None)
        self.send_response(200)
        body = b'{"ok": true}'
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", make_cookie(self, "", 0))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_run_report(self):
        try:
            payload = self.read_json()
        except Exception:
            self.send_json(400, {"ok": False, "message": "Could not read the report request."})
            return

        mode = clean(payload.get("mode") or "top_trends")
        if mode not in {"top_trends", "most_potential", "custom"}:
            mode = "top_trends"

        request_text = clean(payload.get("request", ""))
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
        now = time.time()
        cooldown = cooldown_seconds()
        if now - LAST_RUN_AT < cooldown:
            wait = max(1, int(cooldown - (now - LAST_RUN_AT)))
            self.send_json(429, {"ok": False, "message": f"Please wait {wait} seconds before running another report."})
            return

        if not RUN_LOCK.acquire(blocking=False):
            self.send_json(409, {"ok": False, "message": "A report is already running. Please wait a minute."})
            return

        try:
            env = os.environ.copy()
            env.setdefault("MAX_SEARCHES", str(max_searches()))
            env["SEARCH_MODE"] = mode
            env["TREND_OUTPUT_DIR"] = str(OUTPUT_DIR)

            area = clean(payload.get("area", ""))
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
            results = read_json(OUTPUT_DIR / "results.json", {})
            summary = save_history(results, payload)
            results = read_json(OUTPUT_DIR / "results.json", {})
            self.send_json(200, {
                "ok": True,
                "message": "New report generated.",
                "summary": summary,
                "results": results,
                "status": status_payload(),
            })
        except subprocess.TimeoutExpired:
            self.send_json(504, {"ok": False, "message": "The report took too long and was stopped."})
        except Exception as exc:
            self.send_json(500, {"ok": False, "message": f"Report failed: {exc}"})
        finally:
            RUN_LOCK.release()

    def serve_history_file(self, path):
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 4 or parts[:2] != ["api", "history"]:
            self.send_error(404)
            return
        report_id, filename = parts[2], parts[3]
        if not report_id.replace("-", "").isdigit() or filename not in REPORT_FILES:
            self.send_error(404)
            return
        file_path = (HISTORY_DIR / report_id / filename).resolve()
        if HISTORY_DIR.resolve() not in file_path.parents or not file_path.exists():
            self.send_error(404)
            return
        self.send_response(200)
        if filename.endswith(".pdf"):
            self.send_header("Content-Type", "application/pdf")
        elif filename.endswith(".json"):
            self.send_header("Content-Type", "application/json; charset=utf-8")
        elif filename.endswith(".xlsx"):
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        elif filename.endswith(".txt"):
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        else:
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.end_headers()
        with file_path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)


def config_has_serpapi_key():
    key = clean(os.environ.get("SERPAPI_KEY") or app_config().get("serpapi_key", ""))
    return bool(key and key != "PASTE_SERPAPI_KEY_HERE")


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8099"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Trend Research server running on http://{host}:{port}")
    print(f"Static files: {STATIC_DIR}")
    print(f"Report output: {OUTPUT_DIR}")
    print(f"State/history: {STATE_DIR}")
    print(f"Portal users: {portal_user_path()}")
    print(f"Allowed users: {', '.join(sorted(allowed_users()))}")
    server.serve_forever()


if __name__ == "__main__":
    main()
