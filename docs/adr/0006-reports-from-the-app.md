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
- **Kommandozeile bleibt.** `python -m chronicle` bleibt für Dateien außerhalb der Datenbank. Der Docker-Standard ist weiterhin die API.

## Konsequenzen

- Der Bericht ist nicht mehr anonym verfügbar. Er braucht einen Admin im Browser, und jede Erzeugung ist nachvollziehbar.
- Die Datei ist eine vollständige Kopie des Topics. Wer sie hat, sieht alles. Das bleibt das Risiko des Berichts und steht in der Oberfläche.
- Ein Bericht kann groß werden, weil Evidence eingebettet ist (bis 10 MiB je Datei). Der Download lädt die Datei komplett.
- Die Einbindung der CDN-Skripte (vis-timeline, vis-network, DOMPurify) bleibt. Der Bericht braucht dafür eine Internetverbindung beim Öffnen.

## Offene Punkte

- Ein Bericht über mehrere Topics in einer Datei gibt es nicht.
- Eine Aufbewahrung oder ein Verfallsdatum für heruntergeladene Berichte gibt es nicht.
- Das Wasserzeichen mit Benutzernamen im Bericht fehlt noch.
