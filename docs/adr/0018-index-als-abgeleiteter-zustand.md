# ADR 0018: Index und Cache als abgeleiteter, nicht versionierter Zustand

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0008 Punkt 1, ADR 0010, ADR 0013, ADR 0014, Issue #125 (inkrementeller Index), `chronicle/db.py` (`source_files`, `event_files`, `topic_files`)

## Kontext

Die Datenbank `chronicle.db` ist Index: Sie lässt sich aus den Dateien neu aufbauen (ADR 0008, ADR 0010). Heute liegt sie dort, wohin `--db` zeigt, und merkt sich Dateistände in eigenen Tabellen (`source_files`). Mit Vaults in Git gibt es einen besseren Anhaltspunkt für „was hat sich geändert“: den Commit.

## Entscheidung

1. **Der Index ist ein Cache, kein Zustand.** Er liegt je Vault unter `$CHRONICLE_HOME/cache/<local-id>/index.db` (ADR 0014; der Schlüssel ist die lokale ID der Installation und nicht die Manifest-ID, die der Vault kontrolliert), nie im Vault-Ordner, nie in Git, nie im Backup. Der Ordner hat Modus `0700`.
2. **Der Index enthält nichts, was nicht in den Dateien steht.** Auch keine Anhangs-Bytes (ADR 0010, Punkt 2). Es gibt keine Grabsteine, kein „gelöscht in der Oberfläche“ (ADR 0012). **Auf `main` bereits so umgesetzt** (ADR 0011, Schritte 3a und 3c): Der Index entsteht in einer Funktion (`build_index`) aus den Dateien, auch beim Importer.
3. **Gültigkeit.** Der Index speichert in einer Tabelle `meta`: das Indexformat (Schemaversion der Anwendung), die lokale ID, den Vault-Formatstand, und zur Information den indizierten Commit (`HEAD`). Er ist **ungültig**, und wird komplett neu gebaut, wenn Indexformat, lokale ID oder Vault-Format nicht passen, wenn die Datei fehlt oder beschädigt ist (`PRAGMA quick_check`) oder wenn eine Teilaktualisierung fehlschlägt.
4. **Aktualisierung: ein Mechanismus.** Maßgeblich ist der **Fingerabdruck je Datei** (Größe, Änderungszeit, bei Abweichung SHA-256), wie ihn `source_files` heute schon führt. Git dient nur als **Beschleuniger**, nicht als zweite Wahrheit: `git diff --name-status -z --no-renames <indizierter-commit> HEAD` und `git status --porcelain=v1 -z` liefern die Kandidatenmenge, die der Fingerabdruck dann bestätigt. `--no-renames` ist Pflicht (sonst erscheint eine verschobene Datei nur als eine Zeile mit zwei Pfaden). Im Zweifel wird neu gebaut. Die Teilschritte aus #125 (nur das geänderte Event schreiben, Feldänderung aktualisiert nur den Event-Eintrag) sind **bereits auf `main`** (`c7bb327`, `5c017a6`, PR #136) und bleiben. Messwert aus dem Review: 10.000 Dateien, Vollaufbau 0,63 s. Ein zweiter, an Commits gebundener Mechanismus lohnt den Aufwand nicht.
5. **Abgeleitet heißt auch: nie Quelle.** Keine Funktion darf aus dem Index zurück in Dateien schreiben (außer über den normalen Schreibpfad mit Prüfung). Die Anzeige „freigegeben“ (ADR 0016) berechnet sich aus Git, nicht aus dem Index.
6. **Ein Schreiber.** Nur der Serverprozess schreibt den Index (WAL-Modus). Weitere Prozesse (CLI `apply`, Tests) öffnen ihn gesperrt oder bauen auf einer Kopie.
7. **Betriebsdaten sind getrennt.** `ops.db` ist kein Cache und wird gesichert (ADR 0014). Sie bleibt die Datenbank für Konten, Sitzungen, Audit-Log und Schlüssel-Metadaten (Entscheidung des Projektinhabers); die Abgrenzung zum Vault und zum Index steht in der Tabelle in ADR 0014, Punkt 5.
8. **Verschlüsselung at rest ist eine Frage für ADR 0024.** Der Index enthält den Volltext der Inhalte. Ob er (und `ops.db`) zusätzlich verschlüsselt wird, ist dort als Option (c) beschrieben. Der Index bleibt in jedem Fall verwerfbar.

## Konsequenzen

- Löschen von `cache/` ist immer sicher. Es kostet Zeit beim nächsten Start (Neuaufbau, Messung in `scripts/measure_index.py`).
- Wer einen Vault auf einem zweiten Rechner öffnet, baut den Index dort selbst. Es gibt nichts zu synchronisieren.
- Der Index enthält den Volltext der Inhalte. Er ist so vertraulich wie der Vault, daher die Rechte `0700`.
- Die Tabellen `source_files`, `event_files` und `topic_files` bleiben, werden aber je Event-Datei geführt (ADR 0013). Der Umfang dieses Teils schrumpft damit auf: Cache-Ordner, `meta`-Kopf, Gültigkeitsregeln.
- Mehrere Branches im selben Klon teilen sich einen Index. Beim Wechsel des Branches ist der Index nach Punkt 3 ungültig oder wird über den Diff angepasst.

## Offene Punkte

- Ob der Index je Branch geführt wird. Vorläufig nein.
