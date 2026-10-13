# Confluence → OpenWebUI Sync

Synchronisiert Seiten aus Confluence Data Center nach OpenWebUI Knowledge Bases. Konfiguration über PostgreSQL-Tabellen, Deployment als Kubernetes CronJob.

## Architektur

```
Confluence DC ──REST API──▶ Sync Tool ──REST API──▶ OpenWebUI
                               │
                               ▼
                           PostgreSQL
                         (Schema: rag)
```

Das Tool liest Confluence-Seiten, konvertiert sie zu Markdown und lädt sie als Files in OpenWebUI Knowledge Bases hoch. Der Sync-State wird in PostgreSQL persistiert.

## Technologie

- **Python 3.12+** mit [uv](https://docs.astral.sh/uv/) als Package Manager
- **httpx** — HTTP-Client für Confluence & OpenWebUI REST APIs
- **markdownify** — HTML → Markdown Konvertierung
- **psycopg2** — PostgreSQL-Anbindung
- **pydantic-settings** — Konfiguration via Environment Variables
- **Docker** Multi-Stage Build, **Kubernetes** CronJob

## Setup

### 1. Abhängigkeiten installieren

```bash
uv sync
```

### 2. Environment Variables

`.env` aus `.env.example` erstellen:

```env
CONFLUENCE_BASE_URL=https://confluence.example.com
CONFLUENCE_PAT=<Personal-Access-Token>

OWUI_BASE_URL=https://openwebui.example.com
OWUI_PAT=<Bearer-Token>

DATABASE_URL=postgresql://user:password@host:5432/dbname

LOG_LEVEL=INFO
```

Die Anwendung liest ihre Konfiguration mit `pydantic-settings` aus Prozess-Umgebungsvariablen. Lokal kann sie diese Werte aus `.env` laden; im Kubernetes-CronJob kommen sie direkt aus einem Kubernetes Secret.

### 3. Datenbank vorbereiten

Das Schema (`rag`) und alle Tabellen werden automatisch beim Start erstellt. Danach muss der OpenWebUI Service User konfiguriert werden:

```sql
INSERT INTO rag.sync_config (key, value)
VALUES ('openwebui_service_user_id', '<user-id-aus-openwebui>');
```

> Die `openwebui_service_user_id` ist die UUID des OpenWebUI-Benutzers, dessen API-Key verwendet wird. Alle erstellten Knowledge Bases und Files gehören diesem User.

### 4. Sync-Mappings konfigurieren

Jedes Mapping definiert, welcher Confluence Space (optional mit Label-Filtern) in welche OpenWebUI Knowledge Base synchronisiert wird:

```sql
INSERT INTO rag.sync_mappings (
    confluence_space_key,
    confluence_labels_include,
    confluence_labels_exclude,
    confluence_label_mode,
    openwebui_knowledge_name,
    openwebui_knowledge_description
) VALUES (
    'MYSPACE',
    ARRAY['label1', 'label2'],   -- NULL = alle Seiten
    ARRAY['draft'],              -- NULL = kein Ausschluss
    'any',                       -- 'any' oder 'all'
    'Meine Knowledge Base',
    'Beschreibung'
);
```

**Label-Filter:**

| Feld | Beschreibung |
|---|---|
| `confluence_labels_include` | Nur Seiten mit diesen Labels synchronisieren (NULL = alle) |
| `confluence_labels_exclude` | Seiten mit diesen Labels ausschliessen |
| `confluence_label_mode` | `any` = mindestens ein Include-Label, `all` = alle Include-Labels |

## Ausführung

```bash
# Full Sync — alle Seiten, Orphan-Cleanup
uv run python -m src.main --full

# Inkrementeller Sync — nur geänderte Seiten seit letztem Run
uv run python -m src.main --incremental

# Dry Run — keine Änderungen, nur Logging
uv run python -m src.main --full --dry-run
```

> **Inkrementell vs. Full:** Mappings mit Label-Filtern werden automatisch als Full Sync behandelt, da Confluence das `lastModified`-Datum bei Label-Änderungen nicht aktualisiert. Reine Space-Mappings (ohne Labels) unterstützen inkrementelle Syncs.

## Sync-Ablauf

### Pro Mapping

1. **Knowledge Base sicherstellen** — existiert die KB noch? Falls extern gelöscht: neu erstellen und bestehende Files re-linken (Self-Healing)
2. **Seiten aus Confluence laden** — CQL-Query basierend auf Space, Labels und ggf. `lastModified`
3. **Pro Seite synchronisieren:**
   - HTML → Markdown konvertieren (mit Metadaten-Header)
   - Content-Hash berechnen (SHA-256)
   - Unverändert → überspringen (ausser File wurde extern gelöscht → re-upload)
   - Geändert → altes File löschen, neues hochladen
   - Neu → File hochladen und zur KB hinzufügen
4. **Orphan-Cleanup** (nur Full Sync) — Seiten, die nicht mehr im Confluence-Ergebnis sind, werden aus der KB entfernt
5. **Leere KB löschen** — hat eine KB nach dem Cleanup keine Files mehr, wird sie aus OpenWebUI gelöscht

### Nach allen Mappings

6. **Verwaiste Knowledge Bases aufräumen** — KBs, die vom Service User erstellt wurden aber keinem Mapping mehr zugeordnet sind, werden aus OpenWebUI gelöscht

### Duplicate-Content-Handling

OpenWebUI hat eine globale Duplicate-Content-Erkennung. Wenn dieselbe Confluence-Seite in mehreren Mappings vorkommt, wird das bestehende File wiederverwendet statt dupliziert.

### Shared Files

Wird ein File von mehreren Mappings referenziert, wird es beim Orphan-Cleanup nur aus der jeweiligen KB entlinkt. Das File selbst wird erst gelöscht, wenn kein Mapping mehr darauf verweist.

## Datenbank-Schema (rag)

| Tabelle | Zweck |
|---|---|
| `sync_mappings` | Konfiguration: welche Spaces/Labels → welche KB |
| `sync_state` | Tracking: welche Seite wurde mit welchem Hash/File synchronisiert |
| `sync_runs` | Historie: wann lief der Sync, wie viele Seiten |
| `sync_config` | Key-Value-Store (z.B. `openwebui_service_user_id`) |

## Deployment

### Docker

```bash
docker build -t confluence-openwebui-sync .
docker run --env-file .env confluence-openwebui-sync --full
```

### Kubernetes

Manifeste in `k8s/`:

- `namespace.yaml` — Namespace `confluence-sync`
- `secret.yaml` — älteres Secret-Template; wird von der GitLab-Pipeline nicht verwendet
- `configmap.yaml` — nicht-sensitive Konfiguration wie `LOG_LEVEL`
- `cronjob.yaml` — CronJob; Zeitplan, Namespace, Image und Secret-Namen werden beim Deploy aus CI-Variablen eingesetzt

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
```

### GitLab CI/CD

Die Pipeline in `.gitlab-ci.yml` läuft auf den Branches `stage` und `prod`:

1. `build-image` baut das Docker-Image und pusht es mit dem Commit-Tag in die GitLab Container Registry.
2. Der manuelle Job `deploy` erstellt die Kubernetes-Verbindung aus den `K8S_*`-Variablen, aktualisiert Pull- und Anwendungs-Secrets und deployed den CronJob.

#### GitLab-Variablen konfigurieren

Lege Variablen in **Settings → CI/CD → Variables** an. Verwende für Stage und Prod jeweils dieselben Variablennamen und setze den **Environment scope** auf `stage` beziehungsweise `prod`. Der Deploy-Job verwendet den Branch-Namen als GitLab-Environment-Namen. Werte dürfen nicht in `.gitlab-ci.yml`, Kubernetes-Manifeste oder Logs geschrieben werden.

| Variablen | Verwendung | Schutz |
|---|---|---|
| `K8S_SERVER`, `K8S_CLUSTER`, `K8S_NAMESPACE`, `K8S_USER`, `K8S_USER_TOKEN` | Kubernetes-Zugriff; `K8S_USER_TOKEN` als Secret-Variable | Token masked, hidden (falls verfügbar) und protected |
| `K8S_PULL_IMAGE_USER`, `K8S_PULL_IMAGE_PASSWORD` | Kubernetes Image-Pull-Secret | Passwort masked, hidden und protected |
| `CRON_SCHEDULE` | CronJob-Zeitplan, z. B. `30 0 * * *` | Nicht geheim; pro Environment scopen, falls unterschiedlich |
| `CONFLUENCE_BASE_URL`, `CONFLUENCE_PAT` | Confluence-Zugriff | PAT masked, hidden und protected |
| `OWUI_BASE_URL`, `OWUI_PAT` | OpenWebUI-Zugriff | PAT masked, hidden und protected |
| `MDM_BULK_SIZE`, `MDM_CLIENT_ID`, `MDM_CLIENT_SECRET`, `MDM_ENDPOINT_ORGANISATION`, `MDM_ENDPOINT_USER`, `MDM_ENDPOINT_USER_EGOV` | MDM-Konfiguration | Client-Secret masked, hidden und protected |
| `OWUI_DB_HOST`, `OWUI_DB_PORT`, `OWUI_DB_USER`, `OWUI_DB_PASSWORD`, `OWUI_DB_NAME_ANALYTICS`, `OWUI_DB_NAME_DOC`, `OWUI_DB_NAME_CONF` | OpenWebUI-Datenbankverbindungen | Passwort masked, hidden und protected |
| `CONVERTER_ENGINE`, `LOG_LEVEL` | Optionale Anwendungs-Einstellungen | Kein Secret; global oder pro Environment scopen |

Secrets, die je Umgebung variieren, sollten als zwei Variablen mit identischem Namen, aber den passenden Environment Scopes angelegt werden. Aktiviere `Protected` nur, wenn `stage` und `prod` in GitLab als geschützte Branches konfiguriert sind; andernfalls werden diese Variablen den Jobs nicht bereitgestellt. `Masked`/`Hidden` verhindern nicht den Zugriff eines bösartigen oder veränderten CI-Skripts auf die Werte, daher sollten geschützte Branches und Änderungen an der Pipeline kontrolliert werden.

#### Secret- und Variablenfluss

- Die Pipeline verwendet die Variablen direkt; es gibt keinen Job, der sie per `env` einsammelt, und keine `dotenv`-Reports oder Dotenv-Artefakte.
- Der Deploy-Job erstellt `aargaugpt-sync-env-$CI_COMMIT_BRANCH` mit explizit angegebenen Schlüsseln. `DATABASE_URL` wird dort aus den OWUI-Datenbankkomponenten gebildet.
- `cronjob.yaml` bindet dieses Kubernetes Secret per `envFrom` ein. Die Werte stehen dem Python-Prozess somit als Umgebungsvariablen zur Verfügung; eine gemountete `.env`-Datei ist im Cluster nicht erforderlich.
- Ein separates, branch-spezifisches Registry-Secret wird erstellt und über `imagePullSecrets` für den Pod referenziert.
- Der Namespace und die ConfigMap müssen vor dem ersten Deploy vorhanden sein. `K8S_NAMESPACE` muss zum Namespace der ConfigMap passen.

#### Vorteile

- **Begrenzte Weitergabe:** Es gelangen nur ausdrücklich im Deploy-Befehl aufgeführte Anwendungsvariablen in das Kubernetes Secret, nicht die gesamte Runner-Umgebung.
- **Weniger Secret-Kopien:** Zugangsdaten erscheinen nicht in Dotenv-Artefakten oder in einer ausgegebenen `.env`-Datei.
- **Umgebungstrennung:** Identische Variablennamen mit GitLab Environment Scopes reduzieren Präfix- und Auswahlfehler zwischen Stage und Prod.
- **Klare Laufzeitkonfiguration:** Kubernetes stellt die Werte direkt als Prozess-Umgebungsvariablen bereit; Pydantic kann sie ohne zusätzliche Datei laden.

GitLab/Kubernetes Secrets sind weiterhin keine vollständige Verschlüsselungs- oder Berechtigungsstrategie: Beschränke den Zugriff auf CI-Variablen und Kubernetes Secrets, nutze möglichst kurzlebige Tokens und rotiere Credentials regelmässig.

## Projektstruktur

```
src/
├── main.py              # CLI Entrypoint
├── config.py            # Environment-Konfiguration (pydantic-settings)
├── sync.py              # Sync-Orchestrierung
├── confluence_client.py # Confluence REST API Client
├── openwebui_client.py  # OpenWebUI REST API Client
├── converter.py         # HTML → Markdown Konvertierung
├── models.py            # Dataclasses (SyncMapping, SyncState, etc.)
└── state.py             # PostgreSQL State Management
```
