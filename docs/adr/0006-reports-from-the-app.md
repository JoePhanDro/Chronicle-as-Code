# ADR 0006: Berichte aus der Anwendung

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-21
- **Bezug:** ADR 0001 (Berichts-Export), ADR 0005 (Export), Issue #24

## Kontext

ADR 0001 sieht einen eigenständigen HTML-Bericht für Post-Mortems vor, ohne Server und ohne Login. Der bisherige Weg war `python -m chronicle` auf einer `timeline.yaml`. Damit entsteht der Bericht nur aus einer Datei auf der Festplatte. Änderungen aus der Oberfläche fehlen dabei, und die Erzeugung braucht Shell-Zugang. Issue #24 verlangt, dass der Export nur auf Anforderung eines angemeldeten Admins entsteht.

## Entscheidung

- **Quelle ist die Datenbank.** Der Bericht entsteht aus dem Stand der Datenbank, also inklusive Änderungen aus der Oberfläche. Er nutzt denselben Exportpfad wie ADR 0005.
- **Wer darf.** Nur `admin`, über `GET /api/v1/topics/{key}/report` und die Seite `/reports`. Andere Rollen bekommen 403.
- **Eine Datei.** Stylesheet und alle Evidence-Dateien sind eingebettet (Stylesheet inline, Bilder als Data-URI). Die Datei lässt sich ohne Server öffnen und verschicken.
- **Verknüpfte Events.** Events aus anderen Topics, die mit dem Topic verknüpft sind, gehören zum Bericht und stehen in der Timeline.
- **Kennzeichnung.** Die Seite trägt „Berichts-Snapshot · vertraulich · ohne Anmeldung“ und den Erstellungszeitpunkt.
- **Protokoll.** Jeder Bericht wird im Audit-Log festgehalten (`report_created`, Topic und Anzahl Evidence, ohne Inhalt).
- **Kommandozeile entfällt.** `python -m chronicle` ist entfernt (Issue #24). Es gibt keinen Weg mehr, außerhalb der Anwendung eine ungeschützte Kopie zu erzeugen. Dateien außerhalb der Datenbank kommen über den Import in die Datenbank.

## Konsequenzen

- Der Bericht ist nicht mehr anonym verfügbar. Er braucht einen Admin im Browser, und jede Erzeugung ist nachvollziehbar.
- Die Datei ist eine vollständige Kopie des Topics. Wer sie hat, sieht alles. Das bleibt das Risiko des Berichts und steht in der Oberfläche.
- Ein Bericht kann groß werden, weil Evidence eingebettet ist (bis 10 MiB je Datei). Der Download lädt die Datei komplett.
- Die Einbindung der CDN-Skripte (vis-timeline, vis-network, DOMPurify) bleibt. Der Bericht braucht dafür eine Internetverbindung beim Öffnen.
  - Aktualisierung: vis-timeline und vis-network liegen unter `static/vendor/` (Herkunft und Hashes in der README dort). Die App liefert sie selbst aus, Bericht und statische Seiten betten sie ein, die Datei wächst dadurch um etwa 1,3 MB. Inter und JetBrains Mono liegen unter `static/vendor/fonts/` und werden ebenso ausgeliefert oder eingebettet (etwa 120 KB). Offen bleibt Mermaid, das nur bei Diagrammen im Text nachgeladen wird.

## Offene Punkte

- Ein Bericht über mehrere Topics in einer Datei gibt es nicht.
- Eine Aufbewahrung oder ein Verfallsdatum für heruntergeladene Berichte gibt es nicht.
- Das Wasserzeichen mit Benutzernamen im Bericht fehlt noch.
