# ADR 0011: Dateien sind der einzige Schreibpfad

- **Status:** Akzeptiert (Entscheidung des Projektinhabers, 2026-10-27)
- **Datum:** 2026-10-27
- **Bezug:** ADR 0003 (Bearbeiten in der Oberfläche), ADR 0008 (Dateien als Quelle der Wahrheit), ADR 0010 (Betriebsdaten getrennt vom Index), Issues #123, #124

## Kontext

ADR 0008 macht die Topic-Dateien zur Wahrheit und die Datenbank zum Index. Der Code hat daneben einen zweiten, vollständigen Schreibpfad: Zehn Stellen in `repository.py` verzweigen auf `data_dir is not None`. Ohne Datenordner schreibt Chronicle direkt in die Datenbank, und die Datei wird nie berührt (ADR 0003, ADR 0010 Punkt 4).

Das hat Folgen gehabt. Jede Änderung existiert zweimal, und die Pfade sind auseinandergelaufen: Verknüpfungen waren im Dateimodus nur in der Datenbank, bis #119 das behob. Die Tests prüfen überwiegend den Datenbankpfad. Die Docker-Anleitung startet den Server ohne `--data`. Wer ihr folgt, arbeitet also im Datenbankmodus, und seine Änderungen landen nicht in Git. Das widerspricht dem Leitprinzip „as code“.

## Entscheidung

1. **Es gibt einen Schreibpfad: die Dateien.** Jede Änderung geht zuerst in die Topic-Datei. Danach wird der Index aus den Dateien neu aufgebaut. ADR 0008 gilt damit ohne Einschränkung und wird bestätigt.
2. **Der Server braucht einen Datenordner.** Ohne `--data` bzw. `CHRONICLE_DATA` startet er nicht. Einen schreibgeschützten Betrieb ohne Dateien gibt es nicht.
3. **Grabsteine entfallen.** Ein gelöschtes Event ist aus der Datei entfernt, die Historie liefert Git (ADR 0008, Punkt 4). Damit entfallen die Tabelle `deleted_events`, das Wiederherstellen und `/admin/grabsteine`.
4. **Evidence-Inhalte liegen nur als Dateien vor.** Der Index hält Pfad, Typ, Größe und Prüfsumme (ADR 0010, Punkt 2), keine Bytes.
5. **Die Docker-Anleitung bindet den Datenordner schreibbar ein** und startet den Server mit `--data`. Der Import-Dienst entfällt oder wird zum Neuaufbau des Index (`chronicle apply`).

## Vorgehen in Schritten

Jeder Schritt ist ein eigener Pull Request, die Tests bleiben nach jedem grün.

1. **Dieses ADR** und die Bestätigung von ADR 0008. Keine Codeänderung. *(erledigt)*
2. **Vorbereitung:** Die Docker-Anleitung und `docker-compose.yml` auf `--data` umstellen. Die Tests auf eine gemeinsame Dateimodus-Fixture bringen. Der Datenbankmodus bleibt bis hierher lauffähig, damit sich der Schritt zurücknehmen lässt. Beim Start ohne Datenordner erscheint eine Warnung. *(erledigt)*
3. **Entfernen:** Datenbankpfad, Grabsteine und der Start ohne Datenordner fallen weg. *(erledigt: Grabsteine in 3a, Datenbankpfad und Start ohne Datenordner in 3b; Schritt 3c: Import und Index ohne Reste des Datenbankmodus. Der Importer baut denselben Index wie „Neu laden“, die Marke „manuell“ und gespeicherte Evidence-Bytes entfallen)* Datenbanken mit vorhandenen Grabsteinen verlieren die Tabelle bei der Migration, die Anleitung nennt vorher den Weg, sie zu sichern.
4. **Aufteilen:** Den Schreibteil von `repository.py` in Module teilen (#124, Schritt 1 ist mit `queries.py` und `errors.py` getan).

## Konsequenzen

- Es gibt nur noch eine Fehlerbehandlung und eine Regel pro Änderung. Verknüpfungen, Löschen und Evidence lassen sich nicht mehr auseinanderentwickeln.
- Wer Chronicle bisher im Datenbankmodus betreibt, muss vor Schritt 3 seinen Stand exportieren (`python -m chronicle.exporter`, ADR 0005) und mit `--data` neu starten. Gelöschte Events, die nur als Grabstein vorliegen, gehen verloren, wenn sie vorher nicht wiederhergestellt oder gesichert werden.
- Wer ohne Git arbeitet, kann gelöschte Events nicht mehr aus der Oberfläche zurückholen. Der Datenordner sollte unter Versionskontrolle stehen oder gesichert werden.
- Der Schreibvorgang ist langsamer als ein einzelnes SQL-Update (#125). Das bleibt ein eigenes Thema.
- ADR 0003 gilt nicht mehr, ADR 0010 Punkt 4 entfällt.

## Offen

- Ob der Import-Dienst in `docker-compose.yml` bleibt (als Neuaufbau des Index) oder entfällt.
- Ob ein ausdrücklicher Entwicklungsmodus mit temporärem Datenordner nötig ist, etwa für `pytest` und Demos. Voraussichtlich genügt ein temporärer Ordner.
