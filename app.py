import os
import uuid
import json
from contextlib import contextmanager
from functools import wraps
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from authlib.integrations.flask_client import OAuth
import psycopg2
import psycopg2.extras

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")

oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

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

def ensure_bootstrap():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                create schema if not exists docs;
                create table if not exists docs.upload_events (
                    id uuid primary key,
                    document_id uuid,
                    user_id uuid,
                    practice_id uuid,
                    filename text not null,
                    document_type_code text,
                    ocr_status text not null default 'pending',
                    ocr_preview jsonb default '{}'::jsonb,
                    created_at timestamptz not null default now()
                );
            """)
            conn.commit()

ensure_bootstrap()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Autenticazione richiesta"}), 401
        return fn(*args, **kwargs)
    return wrapper

def find_or_create_user(google_profile: dict[str, Any]) -> dict[str, Any]:
    email = google_profile.get("email")
    google_sub = google_profile.get("sub")
    first_name = google_profile.get("given_name")
    last_name = google_profile.get("family_name")
    picture = google_profile.get("picture")

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                select u.id as user_id, u.email, p.first_name, p.last_name
                from identity.auth_identities ai
                join identity.users u on u.id = ai.user_id
                left join identity.user_profiles p on p.user_id = u.id
                where ai.provider = 'google' and ai.provider_user_id = %s
            """, (google_sub,))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    update identity.auth_identities
                    set last_login_at = now(), provider_email = %s
                    where provider = 'google' and provider_user_id = %s
                """, (email, google_sub))
                cur.execute("""
                    update identity.users set last_login_at = now(), updated_at = now() where id = %s
                """, (row["user_id"],))
                conn.commit()
                return dict(row)

            cur.execute("select id, email from identity.users where email = %s", (email,))
            existing_user = cur.fetchone()

            if existing_user:
                user_id = existing_user["id"]
                cur.execute("""
                    insert into identity.auth_identities (
                        id, user_id, provider, provider_user_id, provider_email, is_primary, profile_picture_url, last_login_at, created_at
                    ) values (
                        gen_random_uuid(), %s, 'google', %s, %s, true, %s, now(), now()
                    )
                    on conflict (provider, provider_user_id) do nothing
                """, (user_id, google_sub, email, picture))
                cur.execute("""
                    update identity.users
                    set email_verified = true, last_login_at = now(), updated_at = now()
                    where id = %s
                """, (user_id,))
                cur.execute("""
                    insert into identity.user_profiles (
                        id, user_id, first_name, last_name, privacy_consent_at, created_at, updated_at
                    )
                    values (gen_random_uuid(), %s, %s, %s, now(), now(), now())
                    on conflict (user_id) do update
                    set first_name = coalesce(identity.user_profiles.first_name, excluded.first_name),
                        last_name = coalesce(identity.user_profiles.last_name, excluded.last_name),
                        updated_at = now()
                """, (user_id, first_name, last_name))
                conn.commit()
                return {"user_id": user_id, "email": email, "first_name": first_name, "last_name": last_name}

            cur.execute("""
                insert into identity.users (
                    id, email, password_hash, role, status, is_email_verified, last_login_at, created_at, updated_at
                ) values (
                    gen_random_uuid(), %s, null, 'customer', 'active', true, now(), now(), now()
                ) returning id
            """, (email,))
            user_id = cur.fetchone()["id"]

            cur.execute("""
                insert into identity.user_profiles (
                    id, user_id, first_name, last_name, marketing_consent, privacy_consent_at, created_at, updated_at
                ) values (
                    gen_random_uuid(), %s, %s, %s, false, now(), now(), now()
                )
            """, (user_id, first_name, last_name))

            cur.execute("""
                insert into identity.auth_identities (
                    id, user_id, provider, provider_user_id, provider_email, is_primary, profile_picture_url, last_login_at, created_at
                ) values (
                    gen_random_uuid(), %s, 'google', %s, %s, true, %s, now(), now()
                )
            """, (user_id, google_sub, email, picture))
            conn.commit()
            return {"user_id": user_id, "email": email, "first_name": first_name, "last_name": last_name}

def get_or_create_practice(user_id: str, variant_id: str) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select id
                from practice.practice_instances
                where requester_user_id = %s and variant_id = %s and status = 'draft'
                order by created_at desc
                limit 1
            """, (user_id, variant_id))
            row = cur.fetchone()
            if row:
                return str(row[0])
            cur.execute("""
                insert into practice.practice_instances (
                    id, variant_id, requester_user_id, acting_for_type, status, input_data, created_at, updated_at
                ) values (
                    gen_random_uuid(), %s, %s, 'user', 'draft', '{}'::jsonb, now(), now()
                ) returning id
            """, (variant_id, user_id))
            practice_id = cur.fetchone()[0]
            conn.commit()
            return str(practice_id)

def simulate_ocr(document_type_code: str, filename: str) -> dict[str, Any]:
    mapping = {
        "documento_identita": {"nome": "Mario", "cognome": "Rossi", "documento_numero": "CA1234567"},
        "codice_fiscale": {"codice_fiscale": "RSSMRA80A01H501U"},
        "patente": {"patente_numero": "U1234567X", "scadenza": "2030-05-31"},
        "carta_circolazione": {"targa": "AB123CD", "marca": "Fiat", "modello": "Panda"},
    }
    return {
        "document_type_code": document_type_code,
        "filename": filename,
        "confidence": 0.86,
        "fields": mapping.get(document_type_code, {"preview": "estrazione generica documento"})
    }

@app.route("/")
def index():
    return render_template("index.html", user_name=session.get("user_name"), user_email=session.get("user_email"))

@app.get("/api/health/db")
def db_health():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select current_database(), now()")
                row = cur.fetchone()
        return jsonify({"status": "ok", "database": row[0], "time": str(row[1])})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.get("/api/session")
def session_info():
    return jsonify({
        "is_authenticated": bool(session.get("user_id")),
        "user_id": session.get("user_id"),
        "user_name": session.get("user_name"),
        "user_email": session.get("user_email"),
    })

@app.post("/auth/google/start")
def auth_google_start():
    payload = request.get_json(silent=True) or {}
    accepted = bool(payload.get("privacy_accepted"))
    if not accepted:
        return jsonify({"error": "Per procedere devi accettare l'informativa privacy"}), 400
    session["privacy_accepted"] = True
    redirect_uri = url_for("auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.get("/auth/google/callback")
def auth_google_callback():
    if not session.get("privacy_accepted"):
        return redirect(url_for("index"))
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = oauth.google.parse_id_token(token)
    user = find_or_create_user(userinfo)
    session["user_id"] = str(user["user_id"])
    session["user_name"] = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip() or user.get("email")
    session["user_email"] = user.get("email")
    session.pop("privacy_accepted", None)
    return redirect(url_for("index"))

@app.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"status": "ok"})

@app.get("/api/services")
def list_services():
    query = """
        select
            sv.id as variant_id,
            s.id as service_id,
            sc.name as category_name,
            s.code as service_code,
            s.name as service_name,
            sv.code as variant_code,
            sv.name as variant_name,
            coalesce(
                nullif(trim(sv.description), ''),
                nullif(trim(s.long_description), ''),
                nullif(trim(s.short_description), ''),
                s.name
            ) as display_description
        from catalog.service_variants sv
        join catalog.services s on s.id = sv.service_id
        join catalog.service_categories sc on sc.id = s.category_id
        where s.status = 'active'
          and sv.status = 'active'
          and s.is_public = true
        order by sc.sort_order, sc.name, s.name, sv.name
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
    return jsonify(rows)

@app.get("/api/services/<variant_id>")
def service_detail(variant_id: str):
    service_query = """
        select
            sv.id as variant_id,
            s.id as service_id,
            sc.code as category_code,
            sc.name as category_name,
            s.code as service_code,
            s.name as service_name,
            sv.code as variant_code,
            sv.name as variant_name,
            coalesce(
                nullif(trim(sv.description), ''),
                nullif(trim(s.long_description), ''),
                nullif(trim(s.short_description), ''),
                s.name
            ) as description
        from catalog.service_variants sv
        join catalog.services s on s.id = sv.service_id
        join catalog.service_categories sc on sc.id = s.category_id
        where sv.id = %s
    """

    documents_query = """
        select
            dt.code,
            dt.name,
            vr.mode,
            vr.notes,
            vr.sort_order,
            vr.condition_expression
        from catalog.variant_requirements vr
        join catalog.document_types dt on dt.id = vr.document_type_id
        where vr.variant_id = %s
          and vr.requirement_type = 'document'
        order by vr.sort_order, dt.name
    """

    integrations_query = """
        select
            p.code as portal_code,
            p.name as portal_name,
            p.service_scope,
            sip.submission_mode,
            sip.automation_level,
            sip.supports_submission,
            sip.supports_status_check,
            sip.supports_document_exchange,
            sip.requires_operator_supervision,
            sip.requires_manual_data_entry,
            sip.operational_notes,
            sip.sla_notes,
            ic.name as capability_name,
            ic.description as capability_description
        from integration.service_integration_profiles sip
        join integration.portals p on p.id = sip.portal_id
        left join integration.integration_capabilities ic on ic.id = sip.capability_id
        where sip.variant_id = %s
        order by sip.is_primary desc, p.name
    """

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(service_query, (variant_id,))
            service = cur.fetchone()
            if not service:
                return jsonify({"error": "Servizio non trovato"}), 404

            cur.execute(documents_query, (variant_id,))
            documents = cur.fetchall()

            cur.execute(integrations_query, (variant_id,))
            integrations = cur.fetchall()

    return jsonify({"service": service, "documents": documents, "integrations": integrations})

@app.post("/api/practices")
@login_required
def create_practice():
    payload = request.get_json(silent=True) or {}
    variant_id = payload.get("variant_id")
    if not variant_id:
        return jsonify({"error": "variant_id mancante"}), 400
    practice_id = get_or_create_practice(session["user_id"], variant_id)
    return jsonify({"status": "ok", "practice_id": practice_id})

@app.post("/api/upload")
@login_required
def upload_document():
    file = request.files.get("file")
    document_type_code = request.form.get("document_type_code")
    variant_id = request.form.get("variant_id")

    if not file or not document_type_code or not variant_id:
        return jsonify({"error": "Parametri mancanti"}), 400

    practice_id = get_or_create_practice(session["user_id"], variant_id)
    ext = os.path.splitext(file.filename)[1]
    stored_filename = f"{uuid.uuid4()}{ext}"
    storage_path = os.path.join(UPLOAD_FOLDER, stored_filename)
    file.save(storage_path)

    ocr_preview = simulate_ocr(document_type_code, stored_filename)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select id from catalog.document_types where code = %s", (document_type_code,))
            doc_type = cur.fetchone()
            if not doc_type:
                return jsonify({"error": "Tipo documento non valido"}), 400

            cur.execute("""
                insert into docs.documents (
                    id, document_type_id, owner_type, owner_user_id, uploaded_by_user_id,
                    source, original_filename, storage_key, mime_type, file_size_bytes,
                    validation_status, extracted_metadata, created_at, updated_at
                ) values (
                    gen_random_uuid(), %s, 'user', %s, %s,
                    'user_upload', %s, %s, %s, %s,
                    'pending', %s::jsonb, now(), now()
                ) returning id
            """, (
                doc_type["id"],
                session["user_id"],
                session["user_id"],
                file.filename,
                storage_path,
                file.mimetype or "application/octet-stream",
                file.content_length or 0,
                json.dumps(ocr_preview),
            ))
            document_id = cur.fetchone()["id"]

            cur.execute("""
                insert into practice.practice_documents (
                    id, practice_id, document_id, requirement_id, is_primary, linked_at
                ) values (
                    gen_random_uuid(), %s, %s, null, false, now()
                )
                on conflict do nothing
            """, (practice_id, document_id))

            cur.execute("""
                insert into docs.upload_events (
                    id, document_id, user_id, practice_id, filename, document_type_code, ocr_status, ocr_preview, created_at
                ) values (
                    gen_random_uuid(), %s, %s, %s, %s, %s, 'completed', %s::jsonb, now()
                )
            """, (
                document_id, session["user_id"], practice_id, file.filename, document_type_code, json.dumps(ocr_preview)
            ))
            conn.commit()

    return jsonify({
        "status": "ok",
        "practice_id": practice_id,
        "document_type_code": document_type_code,
        "ocr_status": "completed",
        "ocr_preview": ocr_preview,
        "message": "Documento caricato e associato all'utente autenticato"
    })

@app.get("/api/my/uploads")
@login_required
def my_uploads():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                select
                    ue.id,
                    ue.filename,
                    ue.document_type_code,
                    ue.ocr_status,
                    ue.ocr_preview,
                    ue.created_at
                from docs.upload_events ue
                where ue.user_id = %s
                order by ue.created_at desc
                limit 20
            """, (session["user_id"],))
            rows = cur.fetchall()
    return jsonify(rows)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
