import os
import uuid
from contextlib import contextmanager
from typing import Any

from flask import Flask, jsonify, render_template, request
import psycopg2
import psycopg2.extras

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL non impostata")
    return database_url


@contextmanager
def get_conn():
    conn = psycopg2.connect(get_database_url())
    try:
        yield conn
    finally:
        conn.close()


@app.route("/")
def index():
    return render_template("index.html")


# =========================
# SERVICES LIST
# =========================
@app.get("/api/services")
def list_services():
    query = """
        select
            sv.id as variant_id,
            s.name as service_name,
            sv.name as variant_name,
            coalesce(sv.description, s.short_description) as description
        from catalog.service_variants sv
        join catalog.services s on s.id = sv.service_id
        where s.status = 'active' and sv.status = 'active'
        order by s.name, sv.name
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
    return jsonify(rows)


# =========================
# SERVICE DETAIL
# =========================
@app.get("/api/services/<variant_id>")
def service_detail(variant_id: str):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute("""
                select sv.id, sv.name, sv.description
                from catalog.service_variants sv
                where sv.id = %s
            """, (variant_id,))
            service = cur.fetchone()

            cur.execute("""
                select dt.code, dt.name
                from catalog.variant_requirements vr
                join catalog.document_types dt on dt.id = vr.document_type_id
                where vr.variant_id = %s
            """, (variant_id,))
            documents = cur.fetchall()

    return jsonify({
        "service": service,
        "documents": documents
    })


# =========================
# UPLOAD DOCUMENT
# =========================
@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    document_type = request.form.get("document_type")

    if not file:
        return {"error": "file mancante"}, 400

    filename = f"{uuid.uuid4()}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                insert into docs.documents (
                    id, file_name, document_type, status, created_at
                )
                values (gen_random_uuid(), %s, %s, 'pending', now())
            """, (filename, document_type))
            conn.commit()

    return {"status": "ok", "file": filename}


# =========================
# DEBUG
# =========================
@app.get("/api/health/db")
def health():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select now()")
            return {"status": "ok"}
