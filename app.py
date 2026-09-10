"""Private Railway intake for CoA's existing in-game bug-report form."""
from collections import deque
import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
import time

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from github import GitHub, GitHubError

REPORT_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def secret(name, minimum):
    value = os.environ.get(name, "")
    if (len(value) < minimum or len(value) > 512 or not value.isascii()
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise RuntimeError(f"Set {name} to a secret of {minimum}-512 ASCII characters without spaces.")
    return value


def positive_integer(name, default, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise RuntimeError(f"{name} must be an integer.") from None
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}.")
    return value


def parse_report():
    if request.mimetype != "application/json":
        return None, (jsonify(error="content_type_must_be_application_json"), 415)
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or not {"title", "body"} <= set(data)
            or not set(data) <= {"report_id", "title", "body"}):
        return None, (jsonify(error="expected_title_body_and_optional_report_id"), 400)
    report_id, title, body = data.get("report_id"), data["title"], data["body"]
    if report_id is not None and (not isinstance(report_id, str) or not REPORT_ID.fullmatch(report_id)):
        return None, (jsonify(error="invalid_report_id"), 400)
    if not isinstance(title, str) or not isinstance(body, str):
        return None, (jsonify(error="title_and_body_must_be_strings"), 400)
    title, body = title.strip(), body.replace("\r\n", "\n").strip()
    try:
        valid = (3 <= len(title.encode("utf-8")) <= 200 and 1 <= len(body.encode("utf-8")) <= 16000
                 and not any(ord(c) < 32 or ord(c) == 127 for c in title)
                 and not any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 for c in body))
    except UnicodeError:
        valid = False
    if not valid:
        return None, (jsonify(error="invalid_title_or_body"), 400)
    return (report_id, title, body), None


def create_app():
    github_token = secret("GITHUB_TOKEN", 20)
    api_key = secret("REPORT_API_KEY", 32)
    if hmac.compare_digest(github_token, api_key):
        raise RuntimeError("REPORT_API_KEY must be separate from GITHUB_TOKEN.")
    repository = os.environ.get("GITHUB_REPOSITORY", "jealous-sound/azerothcore-wotlk-coa")
    if not REPOSITORY.fullmatch(repository):
        raise RuntimeError("GITHUB_REPOSITORY must have the form owner/repository.")
    hourly_limit = positive_integer("MAX_REPORTS_PER_HOUR", 60, 10000)
    minimum_interval = positive_integer("MIN_REPORT_INTERVAL_SECONDS", 5, 3600)
    attempts = deque()
    lock = threading.Lock()
    next_allowed = [0.0]
    completed = {}
    in_flight = set()
    github = GitHub(github_token, repository)
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=128 * 1024, DEBUG=False)
    logger = logging.getLogger("gunicorn.error")

    @app.before_request
    def authenticate():
        if request.path in ("/", "/health"):
            return None
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + api_key).encode("ascii")):
            response = jsonify(error="unauthorized")
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        return None

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name.lower().replace(" ", "_")), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        # Log a fixed category only; traceback/exception strings can contain sensitive data.
        logger.error("Bug-report request failed: internal_error")
        return jsonify(status="unknown", error="internal_error"), 503

    def details(report_id, **values):
        if report_id is not None:
            values["report_id"] = report_id
        return values

    def created(report_id, number, duplicate=False):
        return jsonify(details(report_id, status="created", duplicate=duplicate,
                               issue_number=number, issue_url=github.web + str(number))), 200 if duplicate else 201

    def unknown(report_id):
        return jsonify(details(report_id, status="unknown", error="github_result_unknown")), 502

    def remember(fingerprint, state, number=None):
        # Called with lock held. Metadata only; no report text or credentials in the cache.
        if len(completed) >= 4096 and fingerprint not in completed:
            completed.pop(next(iter(completed)))
        completed[fingerprint] = (state, number, time.monotonic())

    @app.get("/")
    def index():
        return jsonify(service="coa-bug-report", version=1)

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/v1/reports")
    def submit():
        report, error = parse_report()
        if error is not None:
            return error
        report_id, title, body = report
        fingerprint = hashlib.sha256(json.dumps([title, body], ensure_ascii=False).encode("utf-8")).hexdigest()
        now = time.monotonic()
        with lock:
            for key, value in list(completed.items()):
                if value[2] <= now - 86400:
                    del completed[key]
            cached = completed.get(fingerprint)
            if cached:
                return created(report_id, cached[1], duplicate=True) if cached[0] == "created" else unknown(report_id)
            if fingerprint in in_flight:
                return jsonify(details(report_id, status="pending", retry_after=2)), 202, {"Retry-After": "2"}
            while attempts and attempts[0] <= now - 3600:
                attempts.popleft()
            delay = max(0, next_allowed[0] - now)
            if len(attempts) >= hourly_limit:
                delay = max(delay, attempts[0] + 3600 - now)
            if delay > 0:
                seconds = max(1, math.ceil(delay))
                return jsonify(error="rate_limited", retry_after=seconds), 429, {"Retry-After": str(seconds)}
            attempts.append(now)
            next_allowed[0] = now + minimum_interval
            in_flight.add(fingerprint)

        try:
            # User-entered mentions should not notify arbitrary GitHub users or teams.
            safe_title = title.replace("@", "@\u200b")
            safe_body = body.replace("@", "@\u200b")
            number = github.create(safe_title, "Submitted from the in-game bug-report form.\n\n" + safe_body)
            with lock:
                remember(fingerprint, "created", number)
        except GitHubError as failure:
            logger.warning("GitHub report delivery: %s", failure.state)
            code, error, state = {
                "queued": (429, "github_rate_limited", "failed"),
                "blocked": (503, "github_access_denied", "failed"),
                "failed": (422, "github_rejected_report", "failed"),
                "uncertain": (502, "github_result_unknown", "unknown"),
            }[failure.state]
            headers = {}
            data = {"status": state, "error": error}
            if report_id is not None:
                data["report_id"] = report_id
            if failure.state in ("queued", "blocked"):
                with lock:
                    next_allowed[0] = max(next_allowed[0], time.monotonic() + failure.retry_after)
                data["retry_after"] = failure.retry_after
                headers["Retry-After"] = str(failure.retry_after)
            elif failure.state == "uncertain":
                with lock:
                    remember(fingerprint, "unknown")
            return jsonify(data), code, headers
        except Exception:
            # Once a POST may have begun, never make a repeated click issue another one.
            with lock:
                remember(fingerprint, "unknown")
            raise
        finally:
            with lock:
                in_flight.discard(fingerprint)
        return created(report_id, number)

    return app
