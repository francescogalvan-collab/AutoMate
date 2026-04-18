# POC Service Browser v3

POC Flask collegato a PostgreSQL con:
- elenco servizi dinamico
- documenti richiesti
- canali di integrazione e grado di automazione
- upload singolo documento
- OCR simulato con anteprima dati estratti

## Avvio locale

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://user:password@host:5432/database"
python app.py
```

## Deploy su Render
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Environment variable: `DATABASE_URL` con Internal DB URL

## Note
Questa versione salva i file nella cartella `uploads/` del servizio. Per ambienti persistenti è consigliato usare object storage.
