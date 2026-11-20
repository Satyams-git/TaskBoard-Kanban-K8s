"""
TaskBoard backend API (Flask + MySQL).

Endpoints
  GET    /api/health        liveness probe: the process is alive
  GET    /api/ready         readiness probe: the database is reachable
  GET    /api/info          which pod / node / version served this request
  GET    /api/tasks         list all tasks
  POST   /api/tasks         create a task   {"title", "description", "priority"}
  PUT    /api/tasks/<id>    update a task   {"status"} and/or other fields
  DELETE /api/tasks/<id>    delete a task
  GET    /api/stress        burn CPU for N seconds (for the HPA demo)

All settings come from environment variables, so the same image runs in
Docker Compose, Kind and the kubeadm cluster without changes.
"""

import os
import socket
import time
from datetime import datetime

import pymysql
from flask import Flask, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------
# Settings (injected by ConfigMap / Secret in Kubernetes)
# ---------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "taskboard")
DB_USER = os.getenv("DB_USER", "taskboard")
DB_PASSWORD = os.getenv("DB_PASSWORD", "taskboard")
APP_VERSION = os.getenv("APP_VERSION", "v1")
APP_ENV = os.getenv("APP_ENV", "local")
MAX_STRESS_SECONDS = int(os.getenv("MAX_STRESS_SECONDS", "30"))

VALID_PRIORITIES = ("low", "medium", "high")
VALID_STATUSES = ("todo", "in_progress", "done")

_schema_ready = False


# ---------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------
def get_connection():
    """Open a new MySQL connection (one per request keeps the code simple)."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        connect_timeout=3,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def ensure_schema():
    """Create the tasks table the first time the database is reachable."""
    global _schema_ready
    if _schema_ready:
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                title       VARCHAR(200) NOT NULL,
                description VARCHAR(1000) NOT NULL DEFAULT '',
                priority    ENUM('low','medium','high') NOT NULL DEFAULT 'medium',
                status      ENUM('todo','in_progress','done') NOT NULL DEFAULT 'todo',
                created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    _schema_ready = True


def to_json(row):
    """Convert a DB row into JSON-friendly values."""
    out = dict(row)
    if isinstance(out.get("created_at"), datetime):
        out["created_at"] = out["created_at"].isoformat()
    return out


def db_error(exc):
    app.logger.error("database error: %s", exc)
    return jsonify(error="database unavailable", detail=str(exc)), 503


# ---------------------------------------------------------------------
# Probes and info
# ---------------------------------------------------------------------
@app.get("/api/health")
def health():
    # Liveness: only says the process is running. Never touch the DB here,
    # otherwise a DB outage would make Kubernetes restart healthy pods.
    return jsonify(status="ok")


@app.get("/api/ready")
def ready():
    # Readiness: send traffic to this pod only when the DB answers.
    try:
        ensure_schema()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return jsonify(status="ready")
    except Exception as exc:  # noqa: BLE001
        return jsonify(status="not ready", detail=str(exc)), 503


@app.get("/api/info")
def info():
    return jsonify(
        pod=os.getenv("POD_NAME", socket.gethostname()),
        node=os.getenv("NODE_NAME", "local"),
        namespace=os.getenv("POD_NAMESPACE", "local"),
        version=APP_VERSION,
        environment=APP_ENV,
    )


# ---------------------------------------------------------------------
# Tasks CRUD
# ---------------------------------------------------------------------
@app.get("/api/tasks")
def list_tasks():
    try:
        ensure_schema()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM tasks ORDER BY created_at DESC, id DESC")
            rows = cur.fetchall()
        return jsonify([to_json(r) for r in rows])
    except pymysql.MySQLError as exc:
        return db_error(exc)


@app.post("/api/tasks")
def create_task():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    priority = data.get("priority", "medium")

    if not title:
        return jsonify(error="title is required"), 400
    if len(title) > 200:
        return jsonify(error="title must be 200 characters or less"), 400
    if priority not in VALID_PRIORITIES:
        return jsonify(error=f"priority must be one of {VALID_PRIORITIES}"), 400

    try:
        ensure_schema()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (title, description, priority) VALUES (%s, %s, %s)",
                (title, description[:1000], priority),
            )
            cur.execute("SELECT * FROM tasks WHERE id = %s", (cur.lastrowid,))
            row = cur.fetchone()
        return jsonify(to_json(row)), 201
    except pymysql.MySQLError as exc:
        return db_error(exc)


@app.put("/api/tasks/<int:task_id>")
def update_task(task_id):
    data = request.get_json(silent=True) or {}
    fields, values = [], []

    if "status" in data:
        if data["status"] not in VALID_STATUSES:
            return jsonify(error=f"status must be one of {VALID_STATUSES}"), 400
        fields.append("status = %s")
        values.append(data["status"])
    if "priority" in data:
        if data["priority"] not in VALID_PRIORITIES:
            return jsonify(error=f"priority must be one of {VALID_PRIORITIES}"), 400
        fields.append("priority = %s")
        values.append(data["priority"])
    if "title" in data:
        title = str(data["title"]).strip()
        if not title:
            return jsonify(error="title cannot be empty"), 400
        fields.append("title = %s")
        values.append(title[:200])
    if "description" in data:
        fields.append("description = %s")
        values.append(str(data["description"])[:1000])

    if not fields:
        return jsonify(error="nothing to update"), 400

    try:
        ensure_schema()
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE tasks SET {', '.join(fields)} WHERE id = %s",
                (*values, task_id),
            )
            cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
        if row is None:
            return jsonify(error="task not found"), 404
        return jsonify(to_json(row))
    except pymysql.MySQLError as exc:
        return db_error(exc)


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id):
    try:
        ensure_schema()
        with get_connection() as conn, conn.cursor() as cur:
            deleted = cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
        if not deleted:
            return jsonify(error="task not found"), 404
        return "", 204
    except pymysql.MySQLError as exc:
        return db_error(exc)


# ---------------------------------------------------------------------
# Load generator endpoint (HPA demo)
# ---------------------------------------------------------------------
@app.get("/api/stress")
def stress():
    seconds = min(max(int(request.args.get("seconds", 10)), 1), MAX_STRESS_SECONDS)
    end = time.time() + seconds
    count = 0
    while time.time() < end:
        count += sum(i * i for i in range(1000))
    return jsonify(
        pod=os.getenv("POD_NAME", socket.gethostname()),
        burned_seconds=seconds,
    )


if __name__ == "__main__":
    # Local development only. In containers, gunicorn runs the app.
    app.run(host="0.0.0.0", port=5000, debug=False)
