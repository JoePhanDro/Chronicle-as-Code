# ADR 0017: Index und Cache als abgeleiteter, nicht versionierter Zustand

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0008 Punkt 1, ADR 0010, ADR 0012, ADR 0013, Issue #125 (inkrementeller Index), `chronicle/db.py` (`source_files`, `event_files`, `topic_files`)

## Kontext

Die Datenbank `chronicle.db` ist Index: Sie lässt sich aus den Dateien neu aufbauen (ADR 0008, ADR 0010). Heute liegt sie dort, wohin `--db` zeigt, und merkt sich Dateistände in eigenen Tabellen (`source_files`). Mit Vaults in Git gibt es einen besseren Anhaltspunkt für „was hat sich geändert“: den Commit.

## Entscheidung

1. **Der Index ist ein Cache, kein Zustand.** Er liegt je Vault unter `$CHRONICLE_HOME/cache/<vault-id>/index.db` (ADR 0013), nie im Vault-Ordner, nie in Git, nie im Backup. Der Ordner hat Modus `0700`.
2. **Der Index enthält nichts, was nicht in den Dateien steht.** Auch keine Anhangs-Bytes (ADR 0010, Punkt 2). Es gibt keine Grabsteine, kein „gelöscht in der Oberfläche“ (ADR 0011).
3. **Gültigkeit.** Der Index speichert in einer Tabelle `meta`: das Indexformat (Schemaversion der Anwendung), die Vault-`id`, den Vault-Formatstand, den indizierten Commit (`HEAD`) und den Branch. Er ist **ungültig**, und wird komplett neu gebaut, wenn eine dieser Angaben nicht passt, wenn die Datei fehlt, beschädigt ist (`PRAGMA quick_check`) oder wenn die Wiederherstellung eines Teilstands fehlschlägt.
4. **Aktualisierung.** Ändert sich `HEAD` (Merge, Wechsel des Branches), liest die Anwendung `git diff --name-status -z <indizierter-commit> HEAD` und aktualisiert nur die betroffenen Event- und Topic-Dateien. Dazu kommen die Dateien mit lokalen, nicht committeten Änderungen (`git status --porcelain=v1 -z`). Nach jedem eigenen Schreibvorgang aktualisiert die App den Eintrag des Events direkt (das ist der Stand von #125). Im Zweifel wird neu gebaut: Richtigkeit geht vor Geschwindigkeit.
5. **Abgeleitet heißt auch: nie Quelle.** Keine Funktion darf aus dem Index zurück in Dateien schreiben (außer über den normalen Schreibpfad mit Prüfung). Die Anzeige „freigegeben“ (ADR 0015) berechnet sich aus Git, nicht aus dem Index.
6. **Ein Schreiber.** Nur der Serverprozess schreibt den Index (WAL-Modus). Weitere Prozesse (CLI `apply`, Tests) öffnen ihn gesperrt oder bauen auf einer Kopie.
7. **Betriebsdaten sind getrennt.** `ops.db` ist kein Cache und wird gesichert (ADR 0013).

## Konsequenzen

- Löschen von `cache/` ist immer sicher. Es kostet Zeit beim nächsten Start (Neuaufbau, Messung in `scripts/measure_index.py`).
- Wer einen Vault auf einem zweiten Rechner öffnet, baut den Index dort selbst. Es gibt nichts zu synchronisieren.
- Der Index enthält den Volltext der Inhalte. Er ist so vertraulich wie der Vault, daher die Rechte `0700`.
- Das Verfahren in Punkt 4 ersetzt die Tabellen `source_files`, `event_files` und `topic_files`, soweit der Commit ausreicht. Nicht committete Änderungen brauchen weiter einen Fingerabdruck je Datei.
- Mehrere Branches im selben Klon teilen sich einen Index. Beim Wechsel des Branches ist der Index nach Punkt 3 ungültig oder wird über den Diff angepasst.

## Offene Punkte

- Ob der Index je Branch geführt wird. Vorläufig nein.
