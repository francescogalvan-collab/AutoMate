# AutoMate POC v4

Versione con:
- login Google
- consenso privacy prima di salvare dati utente
- creazione/ripresa della prima pratica in bozza
- upload documenti associati all'utente autenticato
- OCR simulato con preview campi estratti

## Variabili ambiente richieste
DATABASE_URL=...
SECRET_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

## Render
Build command: pip install -r requirements.txt
Start command: gunicorn app:app
