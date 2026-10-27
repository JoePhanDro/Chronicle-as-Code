# ADR 0010: Betriebsdaten getrennt vom Index

- **Status:** Akzeptiert (Entscheidung des Projektinhabers, 2026-10-23)
- **Datum:** 2026-10-23
- **Bezug:** ADR 0001 (Konten, Sitzungen), ADR 0004 (Evidence), ADR 0005 (Export und Backup), ADR 0008 (Dateien als Quelle der Wahrheit), Issue #89

## Kontext

Die Datenbank hielt Inhalte und Betrieb zusammen: Topics, Events und Anhänge, aber auch Konten mit Passwort-Hashes, Sitzungen, Login-Sperren, Einstellungen und das Audit-Log. Mit ADR 0008 sind die Topic-Dateien die Quelle der Wahrheit, der Index ist eine Ableitung. Im Dateimodus hielt der Index zusätzlich eine Kopie jedes Anhangs.

Konten und Protokoll gehören nicht in Dateien, die in Git oder in einen Sync-Ordner wandern, und sie lassen sich aus den Dateien nicht rekonstruieren.

## Entscheidung

1. **Zwei Dateien.** `chronicle.db` ist der Index: Topics, Events, Verknüpfungen, Zuordnungen zu Dateien, Suchindex. `chronicle.ops.db` neben ihr hält Konten, Sitzungen, Login-Sperren, Einstellungen und das Audit-Log.
2. **Kein doppelter Anhang im Dateimodus.** Mit Datenordner speichert der Index von Anhängen nur Pfad, Typ, Größe und Prüfsumme. Die Bytes liest die Anwendung aus der Datei und prüft sie gegen die gespeicherte Prüfsumme. Stimmt sie nicht, gibt es einen Konflikt statt einer stillen Auslieferung.
3. **Backup kopiert beide Dateien.** Die Betriebsdatei ist die einzige Kopie der Konten.
4. **Ohne Datenordner bleibt alles wie bisher.** Die Datenbank ist dann die Quelle, mit Anhangsinhalten und Grabsteinen. *(Durch ADR 0012 abgelöst: Der Server braucht künftig einen Datenordner.)*

## Konsequenzen

- Ein verlorener Index baut sich mit `chronicle apply` aus den Dateien neu auf. Konten und Audit-Log bleiben erhalten.
- Im DB-Modus ist der Index weiterhin die einzige Kopie. Er darf dort nicht gelöscht werden.
- Export und Bericht brauchen im Dateimodus den Datenordner (`--data` beim Export).
- Bestehende Datenbanken werden beim ersten Öffnen einmalig umgezogen. Die Migration ist idempotent.
- Der Verweis auf die Betriebsdatei im Backup muss mitkopiert werden, sonst fehlen die Konten.

## Offen

- ~~Ob der DB-Modus langfristig entfällt.~~ Entschieden in ADR 0012: Er entfällt.
