# ADR 0022: Migration vom heutigen Speicherformat und Formatversionen

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0010 (Betriebsdatei), ADR 0011, ADR 0012, ADR 0013, `docs/notation.md` Abschnitt 10 (Versionsfeld), `chronicle/exporter.py`

## Kontext

Es gibt zwei heutige Speicherarten:

- **Dateimodus (Format 0):** ein Ordner mit Topic-Dateien, `refs:` am Topic, Anhänge ohne Prüfsumme, Index und `ops.db` neben der Datenbank-Option `--db`.
- **Datenbankmodus:** Inhalte nur in `chronicle.db` (mit Grabsteinen und Anhangs-Bytes), `ops.db` daneben.

Beides soll in Vaults (Format 1) übergehen. Vorhandene Daten dürfen weder verloren gehen noch beim Umzug unbemerkt ihren Sinn ändern.

## Entscheidung

### Formatversionen

1. **`format` im Manifest** (`vault.yaml`, ADR 0012). Ohne `vault.yaml` gilt ein Ordner mit YAML-Dateien als Format 0.
2. **Die Anwendung liest Format N und schreibt nur Format N.** Ein Vault mit höherem Format als der App bekannt wird abgelehnt mit dem Hinweis, die Anwendung zu aktualisieren. Format 0 wird nur von `check`, `apply`, `build` und `vault migrate` gelesen, nicht vom Server (er verweigert den Start mit Hinweis auf `vault migrate`).
3. **Spätere Formatwechsel** sind Befehle der CLI (`chronicle vault migrate-format`), die auf einem Branch laufen, einen Commit erzeugen und als Pull Request freigegeben werden. Ein Formatwechsel ist damit prüfbar. Die Vault-CI ist per SHA an eine App-Version gebunden (ADR 0021, Punkt 18). Der Wechsel hebt diese SHA im selben Pull Request an.

### Migration Format 0 zu Format 1

4. **Befehl:** `chronicle vault migrate <alter-ordner> --out <neuer-ordner> [--name <anzeigename>]`. Der alte Ordner wird nie verändert. Der neue Ordner darf nicht existieren oder muss leer sein.
5. **Ablauf:**
   1. Alter Stand wird mit den heutigen Regeln geprüft (`check`, mit Anhängen). Fehler brechen ab, nichts wird geschrieben.
   2. Für jedes Topic entsteht `topics/<key>/topic.yaml`, für jedes Event `topics/<home>/events/<key>.yaml`. `refs:` wird zu `also_in:` am Event umgedreht.
   3. Anhänge werden nach `evidence/<event-key>/<name>` kopiert (Pfade, die schon so liegen, bleiben), `sha256` wird eingetragen, die Dateien werden geprüft (ADR 0004).
   4. `vault.yaml` bekommt `format: 1`, eine neue `id` (UUID) und den Namen.
   5. **Gleichwertigkeitsprüfung:** Der neue Ordner wird mit den Regeln von Format 1 geladen und mit dem alten Stand verglichen: dieselbe Menge Topics (Felder), dieselbe Menge Events (alle Felder), dieselben Zuordnungen (Heimat und weitere Topics), dieselben Anhänge (Pfade und Prüfsummen). Weicht etwas ab, wird der neue Ordner gelöscht und der Befehl endet mit Fehler.
   6. Die Ausgabe ist deterministisch (sortiert, feste Formatierung). Ein zweiter Lauf ergibt dieselben Bytes.
6. **Git:** Der Befehl führt weder `git init` noch Commits aus. Der Benutzer richtet das Repository ein (oder `chronicle vault init --adopt`, Plan Teil 11), prüft, committet und veröffentlicht. Der Umzug ist ein einzelner, prüfbarer Commit.
7. **Kommentare und Layout** der alten Dateien gehen beim Umzug verloren, weil Events auf neue Dateien verteilt werden. Der Befehl warnt, wenn die alte Datei Kommentare enthält (`#` außerhalb von Zeichenketten, geprüft mit `ruamel.yaml`).

### Von der Datenbank

8. **Datenbankmodus:** zuerst `python -m chronicle.exporter` (schreibt Format 0), dann Punkt 4. Grabsteine (`deleted_events`) werden nicht übertragen. Der Export enthält nur die vorhandenen Events, gelöschte bleiben also gelöscht. Im Vault gibt es keinen Import und damit keine Wiederbelebung gelöschter Events.
9. **Konten:** `chronicle init --adopt-ops <pfad-zu-chronicle.ops.db>` kopiert die Betriebsdatei in den Zustandsordner (ADR 0013), fügt Spalten `git_name`, `git_email`, `vault_id` im Audit-Log hinzu und lässt die alten Einträge ohne `vault_id`. Der alte Index wird nicht übernommen.
10. **Rückweg:** Der alte Ordner bleibt unverändert, bis der Benutzer ihn löscht. Wird eine Migration verworfen, genügt es, den neuen Ordner zu löschen.

### Das Beispiel im Code-Repository

11. `data/` wird mit `vault migrate` zu `examples/vault/` umgezogen (Plan Teil 18). Die Workflows `topic-files.yml` und `ci.yml` prüfen es. Die Bilder `data/evidence/*.png` sind Platzhalter und bleiben.

## Konsequenzen

- Der Server kann Format 0 nicht mehr bedienen, sobald Teil 6b des Plans umgesetzt ist. **Die Migration (Teil 7) steht deshalb vor Teil 6b oder wird mit ihm zusammen ausgeliefert.** Sonst gäbe es einen Stand, in dem Format 0 abgeschaltet ist und kein Weg nach Format 1 existiert (Review H8). Das Lesen von Format 1 kommt vorher in Teil 5, `data/` wird direkt danach umgezogen (Teil 18).
- Ein falscher Umzug fällt durch die Gleichwertigkeitsprüfung auf, nicht erst im Betrieb.
- Zwei Lesepfade (Format 0 und Format 1) existieren eine Zeit lang. Der Lesepfad für Format 0 wird mit Teil 18 auf `vault migrate` beschränkt.

## Offene Punkte

- Wie lange Format 0 lesbar bleibt (Vorschlag: bis nach dem Umzug von `data/`).
