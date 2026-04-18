import os
from contextlib import contextmanager
from typing import Any

from flask import Flask, jsonify, render_template
import psycopg2
from psycopg2.rows import dict_row


app = Flask(__name__)


def get_database_url() -> str:
    database_url = os.getenv("postgresql://automate_postgres_user:WOERDl99wyAm9PvPRIbzHOxBRitbvXLe@dpg-d7hc913bc2fs73detoeg-a.frankfurt-postgres.render.com/automate_postgres")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL non impostata. Esempio: postgresql://user:password@localhost:5432/pratiche_db"
        )
    return database_url


@contextmanager
def get_conn():
    conn = psycopg.connect(get_database_url(), row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


@app.route("/")
def index():
    return render_template("index.html")


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
            coalesce(nullif(trim(sv.description), ''), nullif(trim(s.long_description), ''), nullif(trim(s.short_description), ''), s.name) as display_description
        from catalog.service_variants sv
        join catalog.services s on s.id = sv.service_id
        join catalog.service_categories sc on sc.id = s.category_id
        where s.status = 'active' and sv.status = 'active' and s.is_public = true
        order by sc.sort_order, sc.name, s.name, sv.name
    """
    with get_conn() as conn:
        rows = conn.execute(query).fetchall()
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
            coalesce(nullif(trim(sv.description), ''), nullif(trim(s.long_description), ''), nullif(trim(s.short_description), ''), s.name) as description
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
        service = conn.execute(service_query, (variant_id,)).fetchone()
        if not service:
            return jsonify({"error": "Servizio non trovato"}), 404
        documents = conn.execute(documents_query, (variant_id,)).fetchall()
        integrations = conn.execute(integrations_query, (variant_id,)).fetchall()

    payload: dict[str, Any] = {
        "service": service,
        "documents": documents,
        "integrations": integrations,
    }
    return jsonify(payload)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
