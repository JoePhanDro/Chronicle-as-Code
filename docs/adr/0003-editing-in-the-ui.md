# ADR 0003: Bearbeiten von Events in der Oberfläche

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-21
- **Bezug:** ADR 0001 (Rollen, Audit), ADR 0002 (Topics und Events)

## Kontext

Events sollen in der Oberfläche angelegt, geändert und gelöscht werden. Bisher ist die YAML die Quelle und der Import der einzige Schreibweg. Damit gibt es zwei Quellen für dieselben Daten. Ein erneuter Import würde manuelle Änderungen still überschreiben, und gelöschte Events kämen zurück.

## Entscheidung

**Die Datenbank ist nach dem Import die Quelle der Wahrheit. Der Import liefert Startdaten und darf keine manuelle Arbeit überschreiben.**

- **Herkunft pro Event:** Jedes Event trägt `source` (`import` oder `manual`). Ein Import aktualisiert nur `import`-Events. `manual`-Events werden übersprungen und im Ergebnis gemeldet.
- **Verknüpfungen:** Ein Import fügt Topic-Verknüpfungen nur hinzu und entfernt keine mehr. Verknüpfungen ändert man in der Oberfläche.
- **Löschen mit Grabstein:** Ein gelöschtes Event hinterlässt einen Eintrag in `deleted_events`. Ein Import mit diesem Key überspringt das Event und meldet es.
- **Versionierung:** Jedes Event hat eine `version`. Ein Update muss die aktuelle Version mitschicken. Passt sie nicht, gibt es 409. So überschreiben sich zwei Bearbeiter nicht still.
- **Löschen nur ohne Abhängigkeit:** Ein Event, das ein anderes auslöst (`triggered_by`), lässt sich nicht löschen, solange der Verweis besteht. Das verhindert hängende Verweise.
- **Heimat bleibt fest:** Das Topic, in dem ein Event angelegt wurde, ändert sich nicht. Weitere Topics werden per Verknüpfung ergänzt oder entfernt, das Heimat-Topic bleibt immer verknüpft.
- **Rollen:** `viewer` liest. `analyst` und `admin` legen an, ändern und löschen. Jede Änderung landet im Audit-Log mit den geänderten Feldern, aber ohne Inhalte.
- **Eingabe:** Dieselben Pydantic-Regeln wie beim Import. Die Oberfläche macht keine eigene Validierung.

## Konsequenzen

- ADR 0002 wird an einer Stelle geändert: Import entfernt keine Topic-Verknüpfungen mehr. Die Entfernung läuft über die Oberfläche.
- Die YAML ist danach Startdatei. Wer Änderungen aus der Oberfläche zurück in Git haben will, braucht einen Export. Der ist nicht Teil dieses Schritts.
- Ein Event, das in der Oberfläche gelöscht wurde, kommt beim Import nicht zurück. Ein Admin stellt es auf der Seite „Gelöschte Events“ wieder her; sie legt es mit seiner letzten Fassung, den Verknüpfungen und den Belegen neu an (Issue #34). Ein Grabstein lässt sich dort auch endgültig entfernen. Danach nimmt der Import das Event wieder auf, falls es noch in der YAML steht.

## Offene Punkte

- YAML-Export der aktuellen Datenbank, damit Änderungen in Git landen können
- Evidence-Upload statt Pfadangabe
- Topics anlegen und bearbeiten in der Oberfläche (dieser Schritt beschränkt sich auf Events)
