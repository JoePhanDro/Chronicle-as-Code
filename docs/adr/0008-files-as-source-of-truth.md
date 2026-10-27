# ADR 0008: Dateien als Quelle der Wahrheit, lokale Arbeitskopien und Git als Austausch

- **Status:** Akzeptiert (Entscheidung des Projektinhabers, 2026-10-27, bestätigt mit ADR 0011 „Dateien sind der einzige Schreibpfad“). Punkt 6 (Anhänge) ist durch die Entscheidung vom 2026-10-27 in ADR 0020 (Vault) geändert: Anhänge liegen in Git. Die Punkte 3 und 7 sind teilweise überholt durch die Vorschläge ADR 0012, 0016 und 0020 (Vault, noch nicht bestätigt)
- **Datum:** 2026-10-22
- **Bezug:** ADR 0001 (Server, Konten), ADR 0002 (Topics und Events), ADR 0003 (Bearbeiten in der Oberfläche), ADR 0004 (Evidence), ADR 0005 (Export und Backup)

## Leitprinzip

Oberstes Ziel ist „as code“. Die Dateien bilden Chronicle vollständig ab: Topics, ihre Events und die Verknüpfungen untereinander (`triggered_by`, `refs`, Zuordnung zu Topics). Vorbild sind Terraform und Markdown. Eine lesbare, versionierbare Textdatei ist die Quelle, und Werkzeuge machen daraus eine Ansicht. Die Anwendung ist ein Werkzeug auf dieser Quelle, die Datenbank ein Index. Was sich nicht in der Notation ausdrücken lässt, gehört nicht zum Chronicle-Stand. Konten, Sitzungen und das Audit-Log sind Betrieb (ADR 0005).

## Kontext

Seit ADR 0003 ist die Datenbank die Quelle der Wahrheit. Die YAML ist nur Startdatei. Damit trägt das Projekt den Namen „Chronicle as Code“ nicht mehr: Die Timeline entsteht in der Oberfläche und ist nur über einen Export als Datei sichtbar.

Gewollt ist das Gegenteil. Die YAML-Dateien beschreiben den Stand vollständig, und Änderungen sollen nachvollziehbar, prüfbar und ohne Server lesbar sein. Gleichzeitig soll jede Person ohne Netz und ohne Abstimmung mit anderen arbeiten können, etwa während eines Vorfalls.

## Entscheidung

1. **Dateien sind die Wahrheit.** Jede Person arbeitet mit einer lokalen Arbeitskopie: einem Ordner mit YAML-Dateien und einem lokalen Server. Die Datenbank ist ein Index. Sie lässt sich jederzeit aus den Dateien neu aufbauen.

2. **Ein Schreiber.** Der lokale Server ist der einzige Schreiber seiner Arbeitskopie. Er schreibt atomar (temporäre Datei, dann umbenennen) und liest die Änderung im selben Durchlauf wieder ein. Änderungen durch Editor oder Git erkennt er per Watcher oder beim nächsten Abgleich.

3. **Git-Remote als gemeinsamer Stand.** Es gibt keinen zentralen Server. Ein privates Git-Repository dient dem Austausch. Die Oberfläche bietet die Funktion **„Abgleichen“**. Sie liest und prüft die Dateien, zeigt vorab, welche Events angelegt, geändert oder entfernt würden (wie `terraform plan`), committet lokal, holt den Remote und lädt hoch. Konflikte zeigt sie in der Oberfläche an. Git bleibt im Hintergrund, die Befehle funktionieren aber auch im Terminal.

4. **Löschen.** Wer ein Event aus seiner Datei entfernt, löscht es. Das ändert ADR 0002, wonach Entfernen nur die Zuordnung löst. Die Grabstein-Tabelle entfällt, die Historie liefert Git.

5. **Doppelungen per Workflow verhindern.** Eine CI-Prüfung läuft bei jedem Push und jedem Pull Request auf dem Remote. Sie prüft über alle Dateien hinweg:
   - Event-Keys und Topic-Keys sind eindeutig,
   - Verweise (`triggered_by`, `refs`) sind auflösbar,
   - Evidence-Verweise zeigen auf vorhandene Dateien, und die `sha256`-Prüfsumme stimmt.

   Die Prüfung nutzt dieselben Regeln wie der Importer, ohne etwas zu schreiben. Sie ist nötig, weil zwei Arbeitskopien Doppelungen erst beim Zusammenführen sichtbar machen. Zwei neue Dateien mit demselben Event-Key erzeugen keinen Git-Konflikt. Die Doppelprüfung besteht bereits: innerhalb einer Datei in `models.py`, über Dateien hinweg in `import_timelines`. Der Prüfmodus fehlt noch (Issue #85).

6. **Anhänge liegen lokal.** Binärdateien (Screenshots, PDFs, Logs) liegen im Ordner `evidence/` im Datenordner, neben allen Topic-Dateien. Die Pfade sind relativ zum Datenordner (siehe `docs/notation.md`). Sie sind nicht in Git. Sie werden außerhalb von Git synchronisiert, zum Beispiel über OneDrive. Die YAML beschreibt jede Datei mit Pfad, Typ, Beschreibung und `sha256`. Fehlt eine Datei oder stimmt die Prüfsumme nicht, zeigt der Server das an. Die Regeln aus ADR 0004 gelten weiter: Endung und Inhalt müssen zusammenpassen, SVG und HTML sind verboten.

7. **Ein Nutzer am Anfang.** Konten und Rollen (ADR 0001) gelten pro Instanz. Mehrere Personen arbeiten mit je einer eigenen Instanz. Schreibrechte auf den gemeinsamen Stand regelt dann der Git-Remote.

8. **Export entfällt als Mechanismus.** Die Dateien sind die Ausgabe. Das Backup aus ADR 0005 bleibt für Konten und Audit-Log.

9. **Spezifikation der Notation.** Felder, Verknüpfungsarten, Prüfregeln und Prüfsummen werden als Spezifikation festgeschrieben, unabhängig von der Anwendung, ähnlich wie bei Markdown oder HCL. Ein JSON-Schema lässt sich daraus für Editoren und die CI ableiten.

## Konsequenzen

- Doppelungen fallen erst beim Push auf. Die lokale Prüfung ist deshalb ebenfalls nötig, zusätzlich zur CI.
- Was einmal gepusht ist, bleibt in der Git-Historie. Sensible Inhalte, die versehentlich geteilt wurden, brauchen einen Bereinigungsweg mit Umschreiben der Historie. Dieser Weg muss vor dem ersten Einsatz festgelegt sein.
- Anhänge hängen vom Sync-Dienst ab. Ein Gerät ohne synchronisierte Anhänge zeigt Events mit fehlender Evidence an. Konfliktkopien des Sync-Dienstes (z. B. „waf-block (1).png“) erkennt der Server an der Prüfsumme.
- Anhänge bei einem Cloud-Dienst zu speichern, gibt diesem Dienst die Inhalte. Das ist eine bewusste Entscheidung des Betriebs. CONTRIBUTING.md verbietet echte Evidence im Repository, dieser Weg ist damit vereinbar.
- Generatoren (etwa aus Logs per LLM) und spätere Werkzeuge wie ein Terraform-Provider schreiben Dateien und gehen über dieselbe Prüfung und denselben Abgleich.

## Offene Punkte

- **Konfliktauflösung:** zunächst Git-Merge auf Textebene. Ein Merge auf Feldebene (pro Event und Feld) ist ein späterer Schritt.
- **Prüfmodus im Importer:** Er muss prüfen können, ohne zu schreiben. Das gibt es heute nicht (Issue #85).
- **Bereinigung der Historie:** Ablauf festlegen, bevor Inhalte mit echten Daten geteilt werden.
- **Sync-Dienst für Anhänge:** festlegen, welcher Dienst genutzt wird, und wie Dateien dort gegen versehentliches Löschen geschützt sind.
