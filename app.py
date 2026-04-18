import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, jsonify, render_template, request
import psycopg2
import psycopg2.extras

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / 'uploads'
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024


def get_database_url() -> str:
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL non impostata')
    return database_url


@contextmanager
def get_conn():
    conn = psycopg2.connect(get_database_url())
    try:
        yield conn
    finally:
        conn.close()


def ensure_poc_tables():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''
                create schema if not exists docs;
                create table if not exists docs.upload_events (
                    id uuid primary key,
                    variant_id uuid,
                    document_code varchar(120),
                    document_name varchar(200),
                    original_filename text,
                    stored_filename text not null,
                    mime_type varchar(120),
                    file_size_bytes bigint,
                    ocr_status varchar(30) not null default 'pending',
                    ocr_payload jsonb,
                    created_at timestamptz not null default now()
                );
                '''
            )
        conn.commit()


def fake_ocr(document_code: str, filename: str) -> dict:
    base = Path(filename).stem.replace('_', ' ')
    payload = {'document_type': document_code, 'confidence': 0.74}

    if document_code == 'documento_identita':
        inferred_name = 'Mario Rossi'
        payload.update({
            'full_name': inferred_name,
            'tax_code': 'RSSMRA80A01H501Z',
            'document_number': 'CA1234567',
            'birth_date': '1980-01-01',
        })
    elif document_code == 'patente':
        payload.update({
            'full_name': 'Mario Rossi',
            'license_number': 'RM0123456X',
            'expiry_date': '2033-05-18',
            'categories': ['B'],
        })
    elif document_code == 'carta_circolazione':
        payload.update({
            'plate_number': 'AB123CD',
            'brand': 'Fiat',
            'model': 'Panda',
            'vin': 'ZFA3120000ABCDEFG',
        })
    elif document_code == 'codice_fiscale':
        payload.update({
            'tax_code': 'RSSMRA80A01H501Z'
        })
    else:
        cleaned = re.sub(r'\s+', ' ', base).strip()
        payload.update({'notes': f'Anteprima OCR simulata per file {cleaned}'})

    return payload


@app.before_request
def bootstrap():
    ensure_poc_tables()


@app.route('/')
def index():
    return render_template('index.html')


@app.get('/api/health/db')
def db_health():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('select current_database(), now()')
                row = cur.fetchone()
        return jsonify({'status': 'ok', 'database': row[0], 'time': str(row[1])})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


@app.get('/api/services')
def list_services():
    query = '''
        select
            sv.id as variant_id,
            s.id as service_id,
            sc.name as category_name,
            s.name as service_name,
            sv.name as variant_name,
            coalesce(nullif(trim(sv.description), ''), nullif(trim(s.long_description), ''), nullif(trim(s.short_description), ''), s.name) as display_description
        from catalog.service_variants sv
        join catalog.services s on s.id = sv.service_id
        join catalog.service_categories sc on sc.id = s.category_id
        where s.status = 'active' and sv.status = 'active' and s.is_public = true
        order by sc.sort_order, sc.name, s.name, sv.name
    '''
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()
    return jsonify(rows)


@app.get('/api/services/<variant_id>')
def service_detail(variant_id: str):
    service_query = '''
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
    '''
    documents_query = '''
        select
            dt.code,
            dt.name,
            vr.mode,
            vr.notes,
            vr.sort_order
        from catalog.variant_requirements vr
        join catalog.document_types dt on dt.id = vr.document_type_id
        where vr.variant_id = %s
          and vr.requirement_type = 'document'
        order by vr.sort_order, dt.name
    '''
    integrations_query = '''
        select
            p.name as portal_name,
            p.service_scope,
            sip.submission_mode,
            sip.automation_level,
            sip.requires_operator_supervision,
            sip.requires_manual_data_entry,
            sip.operational_notes
        from integration.service_integration_profiles sip
        join integration.portals p on p.id = sip.portal_id
        where sip.variant_id = %s
        order by sip.is_primary desc, p.name
    '''
    uploads_query = '''
        select id, document_code, document_name, original_filename, stored_filename, ocr_status, ocr_payload, created_at
        from docs.upload_events
        where variant_id = %s
        order by created_at desc
    '''
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(service_query, (variant_id,))
            service = cur.fetchone()
            if not service:
                return jsonify({'error': 'Servizio non trovato'}), 404
            cur.execute(documents_query, (variant_id,))
            documents = cur.fetchall()
            cur.execute(integrations_query, (variant_id,))
            integrations = cur.fetchall()
            cur.execute(uploads_query, (variant_id,))
            uploads = cur.fetchall()
    return jsonify({'service': service, 'documents': documents, 'integrations': integrations, 'uploads': uploads})


@app.post('/api/upload')
def upload_document():
    file = request.files.get('file')
    variant_id = request.form.get('variant_id')
    document_code = request.form.get('document_code')
    document_name = request.form.get('document_name')

    if not file or not variant_id or not document_code:
        return jsonify({'error': 'Parametri mancanti'}), 400

    ext = Path(file.filename).suffix
    stored_filename = f'{uuid.uuid4()}{ext}'
    file_path = UPLOAD_FOLDER / stored_filename
    file.save(file_path)

    ocr_payload = fake_ocr(document_code, file.filename)
    upload_id = str(uuid.uuid4())

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                '''
                insert into docs.upload_events (
                    id, variant_id, document_code, document_name, original_filename,
                    stored_filename, mime_type, file_size_bytes, ocr_status, ocr_payload
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                returning id, document_code, document_name, original_filename, stored_filename, ocr_status, ocr_payload, created_at
                ''',
                (
                    upload_id,
                    variant_id,
                    document_code,
                    document_name,
                    file.filename,
                    stored_filename,
                    file.mimetype,
                    file_path.stat().st_size,
                    'completed',
                    psycopg2.extras.Json(ocr_payload),
                ),
            )
            row = cur.fetchone()
        conn.commit()

    return jsonify({'status': 'ok', 'upload': row})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', '8000')))
