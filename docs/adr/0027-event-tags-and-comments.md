# ADR 0027: Tags und Kommentare in Topic-Dateien

- **Status:** Akzeptiert
- **Datum:** 2026-10-29
- **Bezug:** ADR 0008, ADR 0015, Issue #227

## Entscheidung

Tags und Kommentare werden als optionale Felder im jeweiligen Event der Topic-Datei gespeichert. Tags sind eine sortierte, eindeutige Menge freier Schlagworte. Kommentare sind unveränderliche Einträge mit UTC-Zeitpunkt, Autorname und Markdown-Text; neue Kommentare werden angehängt.

Der App-Benutzername wird beim Anlegen als Autor gespeichert. Die Autorenschaft ist damit eine nachvollziehbare Angabe im Vault, aber kein kryptografischer Identitätsnachweis; dafür gelten Git-Autor, Review und Signaturen aus ADR 0015.

Tags und Kommentare werden in der Volltextsuche berücksichtigt. Tags können zusätzlich exakt gefiltert werden. Technische Berichte enthalten Tags und Kommentare, Management-Berichte enthalten sie nicht.

## Konsequenzen

- Änderungen an Tags und Kommentaren sind Git-versioniert und mergebar.
- Der SQLite-Index hält nur eine Ableitung der Topic-Dateien und kann neu aufgebaut werden.
- Kommentare werden nicht überschrieben, wenn andere Event-Felder bearbeitet werden.
