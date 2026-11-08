# ADR 0031: Aufgaben (Checklisten) pro Topic und Vorlagen pro Incident-Typ

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-11-08
- **Bezug:** ADR 0008 (Dateien als Quelle der Wahrheit), ADR 0010 (Konten nicht in den Dateien), ADR 0016 (Teilen), ADR 0029/0030 (Entities/Export), `docs/notation.md`, Issue #226 (Vergleich TheHive/DFIR-IRIS)

## Kontext

In TheHive/DFIR-IRIS hat ein Case eine Vorlage mit vordefinierten Aufgaben, Zuständigen und Status. In Chronicle war „erledigt?“ bisher höchstens Freitext. Playbooks als Automation gibt es bewusst nicht — die Vorlage ist der Ersatz dafür.

## Entscheidung

1. **Aufgaben leben in der Topic-Datei** in einer Liste `tasks` — damit in Git und im Merge-Treiber (by key, wie Events). Keine separate Datei pro Topic.
2. **Task-Felder:** `key` (Muster wie Event-Keys, eindeutig pro Topic), `title` (Pflicht, ≤200), `status` (`open`/`in_progress`/`done`, Standard `open`), `assignee` (**Freitext**, ≤100 — App-Konten leben nicht in den Dateien, ADR 0010), `deadline` (optional, ISO 8601 mit Zeitzone, UTC), `note` (optional, ≤2000). Reihenfolge in der Datei bleibt erhalten (Checklisten-Reihenfolge); Keys dedupliziert.
3. **Vorlagen:** eine Datei pro Incident-Typ in `checklists/` im Datenordner (z. B. `checklists/ransomware.yaml`), gelesen von `checklists.py` mit denselben Scan-Grenzen wie Presets; der Loader überspringt den Ordner wie `presets/`. Format: `label` + `tasks` mit `title`, `note`, `assignee`, `deadline_hours` (relativ zum Übernehmen). „Aus Vorlage erstellen" kopiert die Aufgaben mit frischen Keys (`task-<hex>`), `deadline_hours` wird zur absoluten Frist ab jetzt; bestehende Aufgaben bleiben unangetastet. Audit-Eintrag `template_applied`.
4. **Deadlines sind reine Anzeige** (Badge „überfällig“ auf der Topic-Seite). Erinnerungen/Benachrichtigungen sind **nicht** Teil dieser Fassung — sie können später über die Notification-Kanäle (#258) laufen.
5. **Rollen:** anlegen/ändern/abhaken/löschen dürfen Writer (analyst, admin); Viewer liest. Audit: `task_created` / `task_updated` / `task_deleted` / `template_applied`.
6. **Anzeige:** Fortschrittsbalken und Aufgabenliste auf der Topic-Seite (Status per Checkbox umschaltbar), Badge „n offene Aufgaben" in der Topics-Übersicht, Abschnitt „Aufgaben“ im PDF (alle Varianten) und Aufgabenliste im HTML-Bericht (Snapshot).
7. **Index:** Tabelle `tasks` (topic_pk, key, title, status, assignee, deadline, note, UNIQUE(topic_pk,key)), beim Reindex aus den Dateien neu aufgebaut. `get_topic` liefert `tasks` mit, `list_topics` die Zahl offener Aufgaben.
8. **Merge:** Aufgaben mergen by key wie Events (feldweise three-way; Löschen+Ändern derselben Aufgabe ist ein Konflikt; beide Seiten haken verschiedene Aufgaben ab → konfliktfrei).
9. **Alte Dateien bleiben gültig und byte-gleich:** ohne `tasks` wird der Schlüssel nicht geschrieben.

## Wirkung auf die Teile

| Teil | Änderung |
|---|---|
| Modell, JSON-Schema, Notation | `Task`, `TaskStatus`, `Timeline.tasks`; Schema neu erzeugen |
| Dateien schreiben (`store.py`), Export | `tasks`-Liste nur schreiben, wenn gesetzt; `set_tasks_in_file` ersetzt die Liste atomar mit Versions-Check |
| Index | Tabelle `tasks` in `db.py`, befüllt in `index.py` |
| Merge-Treiber | `_merge_tasks` by key, `_apply_tasks` |
| Vorlagen | `chronicle/checklists.py`, Ordner `checklists/` im Datenordner |
| API | `tasks` im Topic-Payload; `POST /api/v1/topics/{key}/tasks`, `PUT …/tasks/{task_key}`, `DELETE …/tasks/{task_key}`, `POST …/tasks/from-template` |
| UI | Aufgaben-Sektion auf der Topic-Seite (Checkbox, Edit, Löschen, Neu, Vorlage), Badge in der Topics-Übersicht |
| Berichte | PDF: Abschnitt „Aufgaben“; HTML-Bericht: Aufgabenliste im Snapshot |
