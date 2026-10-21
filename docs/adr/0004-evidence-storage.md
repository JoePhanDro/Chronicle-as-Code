# ADR 0004: Evidence in der Datenbank speichern

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-21
- **Bezug:** ADR 0001 (Evidence nur über autorisierten Endpunkt), ADR 0002 (Evidence-Metadaten), ADR 0003 (Bearbeiten), Issue #22

## Kontext

ADR 0001 verlangt, dass Evidence-Dateien nur über einen autorisierten Endpunkt ausgeliefert werden. Bisher hat der Import Evidence nur auf Existenz geprüft und den Dateiinhalt nicht gespeichert. Die API und die Oberfläche konnten Evidence deshalb nicht anzeigen. Das Formular nahm einen Pfad als Text entgegen, ohne zu prüfen, ob die Datei existiert.

Zu klären war, wo der Inhalt liegt: als Datei im Volume oder als Blob in der SQLite-Datenbank.

## Entscheidung

**Der Inhalt liegt als BLOB in der Tabelle `evidence`, zusammen mit MIME-Typ, Größe und SHA-256.**

- **Dateiname.** Der Dateiname ist nur eine Anzeige. Gespeichert wird der bereinigte Name (letzter Pfadbestandteil, ohne Steuerzeichen, höchstens 120 Zeichen). Er wird nie als Pfad verwendet.
- **Zugelassene Typen.** PNG, JPEG, GIF, WebP, PDF und UTF-8-Text (`.txt`, `.log`). Endung und Inhalt müssen zusammenpassen: Magic Bytes für Binärformate, gültiges UTF-8 ohne NUL-Bytes für Text. SVG und HTML sind ausgeschlossen, weil Browser darin Skripte ausführen.
- **Größe.** Höchstens 10 MiB je Datei. Der Upload wird nur bis zu diesem Limit gelesen.
- **Auslieferung.** `GET /api/v1/evidence/{id}` nur für angemeldete Nutzer, jede Rolle. Antwort mit dem gespeicherten MIME-Typ, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, Bilder inline, alles andere als Download.
- **Schreiben.** Upload und Löschen über die Oberfläche und die API, nur für Rollen `admin` und `analyst`. Jede Änderung wird im Audit-Log festgehalten, ohne Inhalt.
- **Import.** Die YAML verweist weiterhin auf Dateien im Datenordner. Der Import liest und prüft sie vor dem Schreiben und speichert den Inhalt in derselben Transaktion. Schlägt eine Datei fehl, wird nichts geschrieben.
- **Manuelle Änderung.** Upload oder Löschen von Evidence setzt das Event auf `source = manual`. Ein erneuter Import überschreibt die Evidence dann nicht (wie bei anderen Änderungen aus ADR 0003).
- **Event-Body.** Evidence wird nicht mehr im JSON-Body von Create und Update gesendet. Ein gefüllter Body wird mit 422 abgewiesen.

## Konsequenzen

- **Abweichung von ADR 0001:** Evidence liegt nicht als Datei im Dateisystem, sondern in der Datenbank. Der Schutz ist derselbe: Keine Evidence ist ohne Anmeldung erreichbar.
- **Eine Datei für Backup und Restore.** Der Inhalt wird mit der Datenbank gesichert und zurückgespielt. Es gibt keine verwaisten Dateien und keinen zweiten Speicherort, der auseinanderlaufen kann.
- **Atomarer Import.** Ein Fehler in einer Evidence-Datei rollt den ganzen Import zurück, einschließlich der Inhalte.
- **Die Datenbank wächst.** Bei vielen großen Dateien wird die Datenbank groß. Das ist für Vorfallsdaten in diesem Umfang vertretbar. Ein Umzug auf Dateispeicher bleibt möglich, weil der Zugriff in einer Funktion liegt (`get_evidence_content`).
- **Altdaten.** Evidence aus Imports vor dieser Änderung hat keinen Inhalt. Die Auslieferung antwortet dann mit 404 und dem Hinweis, den Import erneut auszuführen. Der nächste Import füllt den Inhalt nach.
- **Die statische Ausgabe ist davon nicht erfasst.** `python -m chronicle.builder` schreibt Evidence weiterhin als öffentliche Dateien in den Ausgabeordner. Das bleibt offen, siehe #24. Der Builder prüft Dateien jetzt mit derselben Regel wie der Import.

## Offene Punkte

- Löschen von Evidence beim Löschen eines Events ist über `ON DELETE CASCADE` gelöst. Eine Aufbewahrungsregel gibt es noch nicht.
- Bilder werden ohne Verkleinerung ausgeliefert. Thumbnails sind ein späterer Schritt.
- Ein Limit für die Gesamtgröße der Datenbank gibt es nicht.
- Starlette parst den Multipart-Body vollständig, bevor das Dateilimit greift. Ein Request-Limit (Body-Größe) fehlt noch und sollte im Reverse-Proxy gesetzt werden, siehe #35.
