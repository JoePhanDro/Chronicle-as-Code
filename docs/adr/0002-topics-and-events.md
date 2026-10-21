# ADR 0002: Events als Kern, Topics als Themen

- **Status:** Vorgeschlagen (zu bestätigen). Teilweise geändert durch ADR 0003: Import entfernt keine Verknüpfungen mehr.
- **Datum:** 2026-10-21
- **Bezug:** ADR 0001, Duplikate beim Import (Abschnitt „Bekannte Lücke“ im PR #19)

## Kontext

Bisher ist ein Incident die oberste Einheit. Ein Import legt jedes Mal einen neuen Incident mit neuen Events an, deshalb entstehen Duplikate. Außerdem passt das Modell nicht zum eigentlichen Ziel: Wir wollen Wissen zu Themen organisieren. Ein Thema kann ein Incident sein, aber auch eine Schwachstelle, ein Projekt oder ein besonderes Ereignis. Ein Event kann zu mehreren Themen gehören, etwa ein CVE-Hinweis, der einen Incident und eine Schwachstellen-Akte betrifft.

## Entscheidung

**Event ist die zentrale Einheit. Topic ist ein Thema, das Events gruppiert.**

- **Event:** hat einen stabilen, eindeutigen `key` (Pflichtfeld in der YAML). Felder wie bisher: Zeitpunkt (`t0`, UTC gespeichert), Phase, Akteur, System, Aktion, Status, Details (Markdown), Evidence, `triggered_by` (Event-Key).
- **Topic:** hat `key`, `title`, `kind` (`incident`, `vulnerability`, `project`, `special`, `other`) und `summary`.
- **Zuordnung:** Ein Event gehört zu beliebig vielen Topics (n:m).
- **Heimat einer Definition:** Jedes Event wird in genau einer Topic-Datei definiert. Andere Topics referenzieren es über `refs`, ohne es zu kopieren.
- **Import ist idempotent (Upsert):** Gleiche Datei zweimal importiert ergibt denselben Stand. Events werden über den `key` aktualisiert, nicht neu angelegt. Evidence eines Events wird beim Import ersetzt.
- **Import löscht nichts.** Entfernt man ein Event aus einer Datei, wird nur die Zuordnung zum Topic gelöst. Das Event bleibt als Wissen erhalten.
- **`triggered_by`** verweist nur auf Event-Keys. Akteure sind Attribute, keine eigenen Knoten im Wissensmodell. Der Graph im Dashboard zeigt sie weiterhin.
- **Migration:** Bestehende Incidents werden zu Topics vom `kind` `incident` mit Key `legacy-<id>`. Ihre Events bekommen Keys `legacy-<id>-<alter-event-id>`.

## Beispiel

```yaml
topic:
  key: inc-2026-042
  title: "INC-2026-042: Ausnutzung von CVE-2026-XXXX"
  kind: incident
events:
  - key: exploit-2026-10-14
    t0: 2026-10-14T08:40:00+02:00
    phase: Exploit
    actor: Angreifer (extern)
    action: "Remote Code Execution erfolgreich"
    status: in_progress
    triggered_by: cve-2026-xxxx-veroeffentlicht
refs:
  - cve-2026-xxxx-veroeffentlicht   # Heimat: topics/cve-2026-xxxx.yaml
```

## Konsequenzen

- Breaking Change für die YAML: `id` wird zu `key` (Pflicht), Top-Level `title`/`summary` wandern in den Block `topic`.
- Breaking Change für die API: `/api/v1/incidents` wird zu `/api/v1/topics` und `/api/v1/events`.
- Der statische Dashboard-Export zeigt weiter ein Topic als Timeline.
- Die Datenbank wird beim ersten Start mit diesem Stand migriert. Die Migration läuft nur einmal.

## Offene Punkte (zu bestätigen)

- Soll ein Event über `refs` nur referenziert werden können, oder darf jedes Topic es auch inhaltlich ändern? Vorschlag: nur die Heimat-Datei ändert Inhalte.
- Brauchen wir Beziehungen zwischen Topics (z. B. „Projekt X ist betroffen von CVE-…“)? Vorschlag: später, als eigener Schritt.
- Tags, Notizen und Volltextsuche gehören zur Wissensorganisation und kommen in einer späteren Phase.
