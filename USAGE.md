# Confluence → OpenWebUI Sync — Was kann ich damit tun?

Dieses Tool synchronisiert Seiten aus **Confluence** automatisch in **OpenWebUI Knowledge Bases**, damit sie als Wissensquelle für den AI-Chat zur Verfügung stehen.

## Was wird konfiguriert?

Die Steuerung erfolgt über sogenannte **Sync-Mappings** in der Datenbank (Tabelle `rag.sync_mappings`). Jedes Mapping definiert eine Regel: *«Welche Confluence-Seiten sollen in welche Knowledge Base?»*

## Möglichkeiten pro Mapping

| Einstellung | Beschreibung |
|---|---|
| **Confluence Space** | Aus welchem Space sollen Seiten synchronisiert werden (z.B. `IT`, `HR`). |
| **Labels einschliessen** | Nur Seiten mit bestimmten Labels synchronisieren (z.B. `rag`, `faq`). |
| **Labels ausschliessen** | Seiten mit bestimmten Labels ignorieren (z.B. `draft`, `intern`). |
| **Label-Modus** | Sollen *alle* angegebenen Labels vorhanden sein (`all`) oder reicht *eines* davon (`any`)? |
| **Knowledge Base Name** | Name der Ziel-Knowledge-Base in OpenWebUI. Wird automatisch erstellt, falls sie noch nicht existiert. |
| **Beschreibung** | Optionale Beschreibung der Knowledge Base. |
| **Aktiviert/Deaktiviert** | Einzelne Mappings können ein- und ausgeschaltet werden. |

## Beispiele

- **Ganzen Space synchronisieren:** Space = `IT`, keine Label-Filter → alle Seiten landen in einer KB.
- **Nur bestimmte Inhalte:** Space = `HR`, Label include = `rag` → nur Seiten mit dem Label «rag» werden übernommen.
- **Entwürfe ausschliessen:** Space = `IT`, Label exclude = `draft` → alles ausser Entwürfe wird synchronisiert.
- **Mehrere Knowledge Bases:** Mehrere Mappings anlegen, z.B. eines für HR-Inhalte und eines für IT-Inhalte — jedes mit eigener KB.

## Automatisches Verhalten

- **Neue Seiten** in Confluence werden automatisch übernommen.
- **Geänderte Seiten** werden aktualisiert.
- **Gelöschte oder nicht mehr passende Seiten** werden aus der Knowledge Base entfernt.
- **Gelöschte Knowledge Bases** in OpenWebUI werden automatisch neu erstellt (Self-Healing).

## Sync-Modi

Das Tool kann manuell mit zwei Modi gestartet werden:

- `--full` — Alle Seiten werden geprüft und Waisen (veraltete Einträge) aufgeräumt.
- `--incremental` — Nur seit dem letzten Lauf geänderte Seiten werden verarbeitet.
- `--dry-run` — Simulation ohne Änderungen, um zu sehen was passieren würde.
