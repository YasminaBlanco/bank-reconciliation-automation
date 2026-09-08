# Despliegue en Google Cloud

Dos formas de correr esto en producción. Elige **una**:

| | Cloud Function | Cloud Run Job |
|---|---|---|
| **Se dispara** | cuando el cliente sube un archivo | en un horario fijo |
| **Sirve para** | "subo el extracto y quiero el reporte" | "cierre automático cada mes" |
| **Costo típico** | centavos al mes | centavos al mes |
| **Recomendado** | ✅ empezar por aquí | cuando el cliente quiere un calendario |

Ambas usan el mismo `run()` de [reconcile.py](reconcile.py). La lógica no
cambia entre local y nube.

---

## 0. Preparación (una sola vez)

```bash
# Variables que se reutilizan en todos los comandos
export PROJECT_ID="mi-proyecto-gcp"
export REGION="us-central1"
export BUCKET="reconciliacion-cliente-acme"

gcloud config set project "$PROJECT_ID"

# APIs necesarias
gcloud services enable \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  eventarc.googleapis.com \
  storage.googleapis.com \
  cloudscheduler.googleapis.com \
  pubsub.googleapis.com
```

### Bucket del cliente

Un bucket por cliente mantiene los datos separados y facilita dar de baja
a uno sin tocar a los demás.

```bash
gcloud storage buckets create "gs://$BUCKET" \
  --location="$REGION" \
  --uniform-bucket-level-access

# Estructura de carpetas
#   incoming/2026-08/bank_statement.csv     <- lo sube el cliente
#   incoming/2026-08/internal_ledger.csv    <- lo sube el cliente
#   reports/2026-08/reconciliation_report.xlsx  <- lo genera el servicio
```

### Service account con permisos mínimos

```bash
export SA="reconciliacion-sa"

gcloud iam service-accounts create "$SA" \
  --display-name="Reconciliación bancaria"

export SA_EMAIL="${SA}@${PROJECT_ID}.iam.gserviceaccount.com"

# Solo el bucket de este cliente, no todo el proyecto
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.objectAdmin"
```

---

## Opción A — Cloud Function (trigger por archivo)

Se dispara con cada subida. Si solo llegó uno de los dos archivos del
período, termina sin hacer nada y espera al otro: los clientes suben el
extracto y el libro por separado casi siempre.

```bash
gcloud functions deploy reconciliacion-bancaria \
  --gen2 \
  --runtime=python312 \
  --region="$REGION" \
  --source=. \
  --entry-point=on_file_uploaded \
  --trigger-event-filters="type=google.cloud.storage.object.v1.finalized" \
  --trigger-event-filters="bucket=$BUCKET" \
  --service-account="$SA_EMAIL" \
  --set-env-vars="REPORT_LANG=es,DATE_WINDOW=3" \
  --memory=512Mi \
  --timeout=300s
```

> **Importante**: el archivo que Cloud Functions busca es `main.py` en la
> raíz del `--source`. Como aquí vive en `gcp/`, despliega así:
>
> ```bash
> cp gcp/main.py gcp/requirements.txt .
> gcloud functions deploy ... --source=.
> rm main.py requirements.txt && git checkout requirements.txt
> ```
>
> O más limpio, dejando el repo intacto:
>
> ```bash
> rm -rf build && mkdir build
> cp gcp/main.py gcp/requirements.txt build/
> cp reconcile.py build/
> cp -r reconciliation build/
> gcloud functions deploy reconciliacion-bancaria --source=build ...
> ```

### Probarla

```bash
gcloud storage cp data/bank_statement.csv "gs://$BUCKET/incoming/2026-08/"
gcloud storage cp data/internal_ledger.csv "gs://$BUCKET/incoming/2026-08/"

# Ver los logs
gcloud functions logs read reconciliacion-bancaria --region="$REGION" --limit=50

# Bajar el reporte generado
gcloud storage cp "gs://$BUCKET/reports/2026-08/reconciliation_report.xlsx" .
```

---

## Opción B — Cloud Run Job (programado)

```bash
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/servicios/reconciliacion:latest"

# Repositorio de imágenes (una sola vez)
gcloud artifacts repositories create servicios \
  --repository-format=docker --location="$REGION"

# Construir (desde la raíz del proyecto)
gcloud builds submit --tag "$IMAGE" --file gcp/Dockerfile .

# Crear el job
gcloud run jobs create reconciliacion-mensual \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --set-env-vars="BUCKET=$BUCKET,REPORT_LANG=es,DATE_WINDOW=3" \
  --max-retries=1 \
  --task-timeout=600s
```

### Ejecutarlo

```bash
# Manualmente, un período concreto
gcloud run jobs execute reconciliacion-mensual --region="$REGION" \
  --update-env-vars="PERIOD=2026-08"

# Sin PERIOD, procesa el mes cerrado anterior
gcloud run jobs execute reconciliacion-mensual --region="$REGION"
```

### Programarlo (día 3 de cada mes, 7:00)

```bash
gcloud scheduler jobs create http reconciliacion-cron \
  --location="$REGION" \
  --schedule="0 7 3 * *" \
  --time-zone="America/Argentina/Buenos_Aires" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/reconciliacion-mensual:run" \
  --http-method=POST \
  --oauth-service-account-email="$SA_EMAIL"
```

---

## Alertas cuando hay discrepancias

La función publica en Pub/Sub si encuentra ítems para revisar. Conecta ahí
lo que prefieras (email, Slack, un ticket).

```bash
gcloud pubsub topics create reconciliacion-alertas

gcloud functions deploy reconciliacion-bancaria \
  --update-env-vars="ALERT_TOPIC=reconciliacion-alertas" \
  --region="$REGION"
```

El Cloud Run Job, en cambio, devuelve **código de salida 2** cuando hay
ítems para revisar, así que también sirve una alerta sobre el estado de la
ejecución.

---

## Histórico en BigQuery (opcional)

Para mostrarle al cliente la evolución mes a mes:

```bash
bq mk --dataset "${PROJECT_ID}:reconciliacion"

bq mk --table "${PROJECT_ID}:reconciliacion.resumen_mensual" \
  periodo:STRING,cuenta:STRING,total_banco:NUMERIC,total_libro:NUMERIC,\
diferencia:NUMERIC,coincidencias:INTEGER,discrepancias:INTEGER,\
solo_banco:INTEGER,solo_libro:INTEGER,fecha_proceso:TIMESTAMP
```

La pestaña *Resumen* del reporte tiene exactamente esas columnas: cargarla
es un `insert_rows_json` sobre `outcome["summary"]`.

---

## Costos estimados

Para un cliente con 2 reconciliaciones al mes y archivos de pocos MB:

| Servicio | Uso mensual | Costo |
|---|---|---|
| Cloud Functions | ~4 invocaciones, 512 MB, <60s | dentro del nivel gratuito |
| Cloud Storage | <1 GB | ~$0.02 |
| Cloud Run Job | ~2 ejecuciones | dentro del nivel gratuito |
| Cloud Scheduler | 1 job | gratis (3 primeros) |

En la práctica, **menos de $1 al mes por cliente**. El costo real del
servicio es tu tiempo, no la infraestructura.

---

## Solución de problemas

| Síntoma | Causa habitual |
|---|---|
| La función no se dispara | El archivo no está bajo `incoming/`, o el trigger apunta a otro bucket |
| `403 Forbidden` al leer el bucket | Falta el binding `storage.objectAdmin` de la service account |
| Aparece `reports/<período>/ERROR.txt` | El CSV del cliente tiene columnas mal; el archivo dice cuáles |
| El reporte sale vacío | Revisa que ambos archivos usen la misma columna `account` |
| `ModuleNotFoundError: reconciliation` | Faltó copiar la carpeta `reconciliation/` al `--source` |
