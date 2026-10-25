# ADR 0016: Konfliktbehandlung und Merge von Event-Dateien

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0008 (offener Punkt Konfliktauflösung), ADR 0012, ADR 0015, `chronicle/merge.py`, `chronicle/sync.py`, Issues #94, #131

## Kontext

Der Merge-Treiber `chronicle.merge` führt Topic-Dateien auf Feldebene zusammen. Er läuft nur dort, wo `sync._use_merge_driver` ihn in `.git/config` und `.git/info/attributes` des lokalen Klons einträgt. Der Merge-Button von GitHub nutzt den Treiber nicht. Außerdem hält `synchronise` die Dateisperre während des Netzzugriffs (Issue #131).

## Entscheidung

1. **Konflikte werden vorab lokal gelöst.** Vor dem Einreichen führt die App `origin/<standard-branch>` in den Arbeits-Branch ein, mit dem Treiber (ADR 0015, Punkte 7 und 8). Der Pull Request ist dann beim Anlegen konfliktfrei. Hat sich der Standard-Branch zwischenzeitlich bewegt, wiederholt die App den Schritt. Der Button „Branch aktualisieren“ des Hosters ist **kein** Ausweg: Er führt ohne unseren Treiber zusammen und würde Konflikte erzeugen, die nur ein Mensch im Hoster löst. Auf einen Treiber auf dem Server verlässt sich nichts. Ist der Remote-Branch weitergewandert, wird er eingeführt (non-ff-Merge, nie `--force`, ADR 0015, Punkt 6).
2. **Das Layout vermeidet Konflikte** (ADR 0012): ein Event, eine Datei. Konflikt bleibt, wenn zwei Personen dasselbe Event ändern.
3. **Feldweise Zusammenführung bleibt für Event-Dateien.** Gleiche Regeln wie in `merge.py`: Ändert nur eine Seite ein Feld, gilt ihr Wert. Ändern beide Seiten gleich, gilt der Wert. Ändern beide unterschiedlich, ist es ein Konflikt. Löschen gegen Ändern ist ein Konflikt und nie ein stilles Löschen. `also_in` und `triggered_by` sind Mengen: Hinzufügen beider Seiten wird vereinigt, Entfernen durch eine Seite gilt.
4. **Der Treiber unterscheidet Konflikt und Fehler.** Exit-Code 1 ist ein Konflikt, jeder andere Fehler (Interpreter nicht gefunden, Datei nicht lesbar) bricht den Abgleich mit einer Fehlermeldung ab und markiert nichts als Konflikt. Der Treiber trägt den Pfad zu `sys.executable` ein und startet mit **`python -P -c <Start> <Paketordner>`** (ADR 0021, Punkt 18; `-I` wurde verworfen, weil es User-Site und `PYTHONPATH` bricht), damit eine `yaml.py` oder ein Ordner `chronicle/` im Vault nicht importiert wird (belegt auf `main`). Er verwendet den Vorabscan aus ADR 0021, Punkt 19. Zieht der Benutzer die virtuelle Umgebung um, schlägt der Treiber fehl. Die App prüft vor jedem Merge, dass der eingetragene Interpreter existiert, und schreibt den Eintrag sonst neu.
5. **Konflikte werden in der Oberfläche gelöst** (Issue #131): je Datei beide Seiten zeigen und wählen (eigene, fremde, von Hand je Feld). Bis dahin bricht die App den Merge ab, setzt den Arbeits-Branch zurück und nennt die Dateien. Das Terminal bleibt der Weg dafür.
6. **Verschieben der Heimat gegen Ändern.** Erkennt Git die Umbenennung nicht (kleine Dateien ähneln sich wenig), entsteht ein Konflikt „gelöscht gegen geändert“. Er wird wie Punkt 3 behandelt. Das ist selten und sichtbar.
7. **Anhänge.** Zwei Personen, die zum selben Event gleichnamige Dateien mit verschiedenem Inhalt hinzufügen, erzeugen einen Add/Add-Konflikt auf dem Pfad. Die Auflösung nummeriert eine Datei neu und passt `file` und `sha256` in der Event-Datei an (wie das Schreiben heute Nummern vergibt).
8. **Die Sperre schützt nur lokale Schritte** (Issue #131): Holen und Hochladen laufen ohne `folder_lock` und mit Zeitlimit (Standard 60 Sekunden, einstellbar). Vorher und nachher wird der Zustand unter der Sperre geprüft: Hat sich der Arbeits-Branch inzwischen bewegt, wird der Lauf wiederholt.
9. **Nach einem Merge** wird das Ergebnis geprüft (`check`, einschließlich der Modusprüfung `git ls-tree -r` auf Links und Submodule, ADR 0021, Punkt 15). Schlägt sie fehl, wird der Merge mit **`git reset --merge`** (bzw. zurück auf den Stand vor dem Merge) rückgängig gemacht, **nicht mit `reset --hard`**: `--hard` würde nicht committete Änderungen verwerfen und ist dieselbe Fehlerklasse wie Issue #126. `--merge` lässt Änderungen, die nicht Teil des Merges sind, bestehen. Die App committet vor dem Merge alles (ADR 0015, Punkt 2).

## Konsequenzen

- Der Merge-Treiber vereinfacht sich (`_merge_topic`, `_merge_event` bleiben, `_apply` entfällt zum großen Teil, Issue #128).
- Kommentare der Gegenseite gehen im Treiber verloren (dokumentiert). Bei einer Datei je Event betrifft das nur Kommentare im selben Event und ist selten.
- Die Hostprüfung `Chronicle prüfen` ist das Netz unter dem Treiber. Gelangt ein Merge ohne Treiber durch (z. B. Merge-Button bei verändertem Standard-Branch), prüft sie das Ergebnis und lässt Duplikate und kaputte Verweise nicht durch.

## Offene Punkte

- Ob die Auflösung in der Oberfläche vor dem ersten Mehrpersonen-Einsatz stehen muss. Empfehlung: ja (Plan Teil 15b).
