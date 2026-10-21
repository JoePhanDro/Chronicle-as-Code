# ADR 0005: YAML-Export und Backup

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-21
- **Bezug:** ADR 0002 (Topics und Events), ADR 0003 (Bearbeiten), ADR 0004 (Evidence in der Datenbank), Issue #23

## Kontext

Nach einer Bearbeitung in der Oberfläche ist die Datenbank die einzige vollständige Kopie. Es gab keinen Weg zurück in Dateien. Außerdem gab es kein Backup, das konsistent ist, während der Dienst läuft.

Der Import las bisher genau eine `timeline.yaml`. Ein Export mit mehreren Topics braucht mehrere Dateien. Events können über Topics hinweg verweisen, auch in beide Richtungen (`triggered_by` und `refs`). Ein Import Datei für Datei würde daran scheitern, sobald die Reihenfolge nicht passt oder ein Zyklus entsteht.

## Entscheidung

**Export.** `python -m chronicle.exporter --db <datei> --out <ordner>` schreibt eine YAML-Datei pro Topic (`<topic-key>.yaml`) im Format von `timeline.yaml`.

- Zu jedem Topic gehören die Events mit diesem Topic als Heimat (`events`) und die übrigen verknüpften Events (`refs`).
- Evidence-Inhalte werden unter `evidence/<event-key>/<dateiname>` geschrieben. Die YAML verweist relativ darauf.
- Events werden nach Zeitpunkt und Key sortiert, Refs nach Key. Ein Export ist damit reproduzierbar.
- Markdown in `details` und Topic-Zusammenfassungen steht als Blockscalar (`|`), damit es lesbar bleibt.
- Der Export baut alles im Speicher auf. Fehlt der Inhalt einer Evidence-Datei, bricht er ab und hinterlässt keine Teildatei.

**Import mehrerer Dateien.** `python -m chronicle.importer --data <ordner>` liest jede `*.yaml` und `*.yml` unterhalb des Ordners. Alle Dateien werden in einer Transaktion geschrieben, danach werden die Verweise (`triggered_by`, `refs`) geprüft. Dadurch funktionieren Verweise in beide Richtungen, auch zyklisch. Ein Event darf nur in einer Datei definiert sein.

**Roundtrip.** Export, Import in eine leere Datenbank, erneuter Export: Die Dateien sind byte-gleich. Das prüft ein Test.

**Backup.** `python -m chronicle.backup --db <datei> --out <datei oder ordner>` kopiert die Datenbank mit der Online-Backup-API von SQLite. Die Kopie wird mit `PRAGMA integrity_check` geprüft und mit Rechten `0600` angelegt. Ein bestehendes Ziel wird nie überschrieben.

**Nicht im Export.** Konten, Passwort-Hashes, Sitzungen, Login-Sperren, das Audit-Log und Tombstones gelöschter Events. Konten und Protokoll sind Betriebsdaten, keine Inhalte. Für sie ist das Backup da. Ohne Tombstone kann ein altes YAML einen in der Oberfläche gelöschten Event wieder anlegen. Wer das verhindern will, nimmt das Backup.

**Herkunft nach einem Roundtrip.** Events aus einem Export sind nach dem Import wieder Import-Events (`source = import`). Die YAML ist dann die Quelle, bis jemand den Event erneut in der Oberfläche bearbeitet.

## Konsequenzen

- Die Datenbank ist nicht mehr die einzige Kopie. Export und Backup laufen unabhängig voneinander: Der Export ist für Menschen und andere Werkzeuge lesbar, das Backup ist für die Wiederherstellung des Dienstes.
- Das Importformat ändert sich: Ein Ordner mit mehreren Topic-Dateien ist jetzt gültig. Eine einzelne `timeline.yaml` funktioniert weiter.
- Der statische Builder (`chronicle.builder`) liest genau eine `timeline.yaml`. Er ist seit #24 entfernt. Der Export ist damit die einzige Datei-Ausgabe, und sie ist nicht öffentlich.
- Das Backup enthält Passwort-Hashes und Sitzungs-Hashes. Es ist so zu behandeln wie die Datenbank selbst.

## Offene Punkte

- Ein Restore ist eine manuelle Handlung (Dienst stoppen, Datei ersetzen, starten). Ein Befehl dafür fehlt noch.
- Verschlüsselung der Backups fehlt. Dafür sorgt bis dahin der Speicherort.
- Eine Aufbewahrungsregel für Backups gibt es nicht.
