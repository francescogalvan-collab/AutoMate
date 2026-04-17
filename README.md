# POC Catalogo Servizi Dinamico

POC minimale con backend Flask e frontend HTML/CSS/JS.

## Funzioni
- legge l'elenco dei servizi dal database PostgreSQL
- permette di selezionare una variante di servizio
- visualizza:
  1. descrizione del servizio
  2. documenti necessari
  3. servizi esterni e grado di automazione

## Prerequisiti
- Python 3.11+
- PostgreSQL
- schema e seed del progetto già caricati

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/pratiche_db
python app.py
```

Apri poi `http://localhost:8000`.

## Query principali
Il backend legge i dati da:
- `catalog.service_categories`
- `catalog.services`
- `catalog.service_variants`
- `catalog.variant_requirements`
- `catalog.document_types`
- `integration.service_integration_profiles`
- `integration.portals`
- `integration.integration_capabilities`

## Evoluzioni naturali
- autenticazione utenti
- caricamento documenti
- collegamento con `practice.practice_instances`
- creazione guidata della pratica dal frontend
- viste filtrate per MVP / fase 2
