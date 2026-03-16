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

OPENWEBUI_BASE_URL=https://openwebui.example.com
OPENWEBUI_API_KEY=<Bearer-Token>

DATABASE_URL=postgresql://user:password@host:5432/dbname

LOG_LEVEL=INFO
```

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
- `secret.yaml` — Credentials (via Sealed Secrets o.Ä. befüllen)
- `configmap.yaml` — `LOG_LEVEL` etc.
- `cronjob.yaml` — Täglicher Run um 02:00 Uhr

```bash
kubectl apply -f k8s/
```

### GitLab CI/CD

Pipeline in `.gitlab-ci.yml`: Build → Push → Deploy auf `main`-Branch.

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
