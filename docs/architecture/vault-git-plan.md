# Umsetzungsplan: Vaults in Git

- **Status:** Entwurf zur Prüfung, nichts davon ist umgesetzt
- **Stand:** 2026-10-25
- **Grundlage:** [`vault-git.md`](vault-git.md) und ADR 0011 bis 0022

## Regeln für jeden Teil

- Ein Teil ist ein Pull Request, eigener Branch von aktuellem `main`, klein und einzeln reviewbar (CONTRIBUTING). Richtwert: unter 500 geänderte Zeilen ohne Tests.
- Ein Teil lässt `main` lauffähig und die Tests grün. Kein Teil baut auf einem Zwischenzustand, der nicht gemergt ist.
- Verhalten ändert sich nur dort, wo es der Teil nennt. Reine Umbauten sind als solche gekennzeichnet.
- Tests laufen ohne Netz. Git-Tests nutzen lokale Repositories in `tmp_path` und einen lokalen „Remote“ (bare Repo), wie `tests/test_sync.py`.
- Jeder Teil passt die betroffene Dokumentation im selben PR an (README, `docs/notation.md`).
- Reihenfolge nach Risiko: zuerst Sicherheitslücken im Ist-Zustand und Vereinfachungen, dann das neue Format, dann Zusammenarbeit.

## Übersicht

| Teil | Titel | Hängt ab von | Größe | ADR | Verhalten ändert sich |
|---|---|---|---|---|---|
| 1 | Sicheres Lesen fremder Dateien | nichts | S | 0021 | ja (strenger) |
| 2 | Datenbankmodus entfernen | nichts | L | 0011 | ja (Server nur mit Datenordner) |
| 3 | Modul `git.py` | nichts | M | 0021 | nein (Umbau) |
| 4 | Manifest und Formatversion | 1 | S | 0012, 0022 | nein (neu, optional) |
| 5 | Format 1 lesen und prüfen | 1, 4 | M | 0012 | nein (neu, nur CLI) |
| 6 | Format 1 schreiben, Index je Event-Datei | 2, 5 | L (zwei PRs) | 0012, 0017 | ja (Server liest Format 1) |
| 7 | Migration `vault migrate`, Prüfsumme Pflicht | 5 | M | 0022, 0019 | nein (neuer Befehl) |
| 8 | Zustandsordner, Registry, `init`, `ops.db` umziehen | 2, 4 | M | 0013 | ja (Startparameter) |
| 9 | Mehrere Vaults: aktiver Vault, URL-Präfix | 6, 8 | M | 0018 | ja (URLs) |
| 10 | Index im Cache, Aktualisierung über Commit | 6, 8 | M | 0017 | nein (intern) |
| 11 | `vault init`, `vault clone`, Vorlage | 3, 4, 8 | M | 0011, 0021 | nein (neu) |
| 12 | Wiederverwendbarer Prüf-Workflow | 5 | S | 0021 | nein (CI) |
| 13 | Identität und Commit je Speichern, Arbeits-Branch | 3, 6, 8 | M | 0014, 0015 | ja (Schreiben committet) |
| 14 | Einreichen und Aktualisieren (Sync-Umbau) | 3, 13 | L | 0015, 0016 | ja (Abgleichen) |
| 15 | Freigabe-Status und Konfliktauflösung in der Oberfläche | 14 | M (zwei PRs) | 0015, 0016 | ja (Oberfläche) |
| 16 | Prüfung auf Geheimnisse und Bereinigungsverfahren | 5 | M | 0020 | nein (neu) |
| 17 | Dokumentation auf den neuen Stand | 6, 14 | S | alle | nein |
| 18 | Beispiel-Vault statt `data/`, alte Lesepfade entfernen | 7, 12 | S | 0022 | nein |

Größe: S bis etwa 150 Zeilen, M bis etwa 400, L darüber (dann in zwei PRs teilen).

Abhängigkeitsgraph (vereinfacht): `1 → 4 → 5 → 6`, `2 → 6`, `5 → 7`, `4 → 8 → 9`, `3 → 11`, `3, 6, 8 → 13 → 14 → 15`.

---

## Teil 1: Sicheres Lesen fremder Dateien

- **Inhalt:** Im Lader und in `check`: Symlinks abweisen (`lstat`, `find_topic_files`, `collect_evidence`), Größe vor dem Lesen begrenzen, YAML ohne Aliase, Tags und doppelte Schlüssel lesen, Steuer- und Bidi-Zeichen abweisen (ADR 0021, Punkte 11 bis 15). Gilt für Format 0, wie es heute ist.
- **Akzeptanz:** Eine Datei `x.yaml`, die ein Link auf eine Datei außerhalb ist, wird mit Dateiname abgelehnt, ihr Inhalt erscheint in keiner Meldung. Doppelter Schlüssel, Alias, 1-MiB-Datei, U+202E im Feld `action` werden mit Zeile und Datei gemeldet. Die vorhandenen Beispieldaten bestehen unverändert.
- **Test:** Neue Tests in `tests/test_check.py`, `tests/test_timeline.py` je Fall. Regressionstest, dass `data/` besteht.
- **Risiko:** Bestehende Dateien mit Aliasen oder doppelten Schlüsseln werden unbrauchbar. Gering, aber im PR-Text nennen und mit `chronicle check` an `data/` belegen.

## Teil 2: Datenbankmodus entfernen (Issue #123)

- **Inhalt:** Alle Zweige `if data_dir is not None` in `repository.py` entfernen, Grabsteine (`deleted_events`, `deleted_evidence`) und gespeicherte Anhangs-Bytes streichen, Server startet nur mit Datenordner, Tests auf den Dateimodus umstellen. Reiner Umbau, soweit möglich.
- **Akzeptanz:** `repository.py` hat einen Schreibpfad. `serve` ohne `--data` endet mit klarer Meldung. Alle Tests laufen im Dateimodus. Ein Migrationsschritt in `db.py` löscht die toten Tabellen idempotent.
- **Test:** Bestehende Tests umbauen (Fixtures in `conftest.py`, #129). Test, dass eine alte Datenbank mit Tabellen geöffnet werden kann.
- **Risiko:** Größter Umbau am Anfang. In zwei bis drei PRs teilen (Schreibpfade für Events, Topics, Anhänge). Der Server startet ohne Datenordner nicht mehr: Betreiber mit reinem Datenbankbetrieb müssen vorher exportieren (ADR 0022, Punkt 8). Entscheidung dazu in offener Frage 6.

## Teil 3: Modul `git.py`

- **Inhalt:** Gehärteter Aufruf (ADR 0021, Punkte 1 bis 10). `sync.py` ruft nur noch `git.py`. Prüfung von URL und Branch-Namen als reine Funktionen. Mindestversion.
- **Akzeptanz:** Es gibt kein `subprocess` außerhalb von `chronicle/git.py` (Test). Zeitlimit beendet einen hängenden Prozess. URLs `ext::…`, `file:…`, `https://u:p@h/…`, `-oProxyCommand=…` werden abgelehnt. Fehlermeldungen enthalten keine Zugangsdaten. Alle Tests in `tests/test_sync.py` bleiben grün.
- **Test:** Einheitstests der Prüffunktionen (Tabellen mit guten und bösen Eingaben). Test mit einem Skript als „git“ im `PATH`, das schläft (Zeitlimit). Test, dass `core.hooksPath` Hooks im Testrepo nicht ausführt (ein Hook schreibt eine Datei).
- **Risiko:** Umgebung wird eingeschränkt: Manche Credential Helper brauchen weitere Variablen (z. B. `DISPLAY`, `XDG_*`, `SSH_ASKPASS`). Auf macOS und Linux manuell prüfen (offene Frage 7).

## Teil 4: Manifest und Formatversion

- **Inhalt:** Modell `VaultManifest` (`format`, `id`, `name`), Erkennen von Format 0 (ohne Manifest) und 1, Ablehnen unbekannter Formate. Befehl `chronicle vault info <ordner>`.
- **Akzeptanz:** `format: 2` führt zu einer Meldung zum Aktualisieren. Fehlende `id` ist ein Fehler. Format 0 bleibt unverändert nutzbar.
- **Test:** Tabelle von Manifesten. `vault.yaml` über 16 KiB wird abgewiesen (Teil 1).
- **Risiko:** gering.

## Teil 5: Format 1 lesen und prüfen

- **Inhalt:** Lader und Prüfregeln für das Layout aus ADR 0012: `topics/<key>/topic.yaml`, `events/<key>.yaml`, `also_in`, Pfad gleich Key, Eindeutigkeit ohne Beachtung der Schreibweise, verwaiste und fehlende Anhänge (Prüfsumme), JSON-Schemas für Topic- und Event-Datei. `chronicle check`, `plan`, `build`, `apply` arbeiten mit beiden Formaten. Intern dieselbe `Timeline`-Darstellung (Zusammenführen in Speicher), damit Index und Rendering unverändert bleiben.
- **Akzeptanz:** Ein von Hand gebauter Beispiel-Vault in `tests/fixtures/vault/` besteht `check`. Jede Prüfregel hat einen Fehlertest. `chronicle schema` gibt die Schemas aus, `schema/` im Repo ist aktuell (bestehender Test).
- **Test:** Parametrisierte Fehlerfälle (Pfad ungleich Key, Duplikat in zwei Topics, Key nur in Groß-/Kleinschreibung verschieden, `also_in` unbekannt, Kreis).
- **Risiko:** Gleichwertigkeit der inneren Darstellung (Heimat als Pfad statt als Feld). Genau diese Stelle sorgfältig reviewen.

## Teil 6: Format 1 schreiben, Index je Event-Datei (zwei PRs)

- **6a:** `store.py` schreibt Events als eigene Dateien: anlegen, ändern (Feldweise, Kommentare bleiben), löschen, Heimat wechseln (`git mv`-gleich), `also_in` ändern. Atomar, mit Versionsvergleich je Datei, Sperre wie heute. Topics: anlegen, ändern, löschen (Ordner).
- **6b:** `repository.py` und Index auf Event-Dateien (`source_files` je Datei), Anhänge nach `evidence/<key>/…` mit `sha256`. Server liest Format 1. Format 0 wird vom Server nicht mehr bedient (Hinweis auf `vault migrate`).
- **Akzeptanz:** Alle bisherigen Schreibtests laufen auf Format 1. „Alles oder nichts“ (Rollback) und Versionskonflikt (409) verhalten sich wie heute. Eine Änderung schreibt genau eine Datei (Test über Dateizeitstempel). 10.000 Events: Änderung unter 1 Sekunde (`scripts/measure_index.py` erweitern).
- **Test:** Portierte Dateien `tests/test_file_*.py`. Gleichzeitigkeit mit zwei Threads (wie `test_file_rollback.py`).
- **Risiko:** Größter fachlicher Teil. Umbenennen/Heimat wechseln ist die kniffligste Operation (Anhänge und Verweise anpassen). Index und `topic_files`-Tabellen ändern sich. Wenn Teil 2 nicht vorher fertig ist, wächst dieser Teil auf das Doppelte: **Reihenfolge einhalten**.

## Teil 7: Migration `vault migrate`

- **Inhalt:** Befehl nach ADR 0022, Punkte 4 bis 7, mit Gleichwertigkeitsprüfung, deterministischer Ausgabe, Warnung bei Kommentaren. `sha256` wird in Format 1 Pflicht.
- **Akzeptanz:** `data/` migriert, `check` besteht, Gleichwertigkeitsprüfung grün, zweiter Lauf ergibt identische Bytes. Absichtlich verfälschte Ausgabe (Test) lässt den Befehl scheitern und den Ausgabeordner entfernen.
- **Test:** Round-Trip-Test auf `data/` und auf einem synthetischen Bestand (500 Events, Verweise in beide Richtungen, Zyklusfreiheit).
- **Risiko:** Sinnverlust bei `refs` zu `also_in` (Richtung umgedreht). Mit Gleichwertigkeitsprüfung auf Mengenebene absichern.

## Teil 8: Zustandsordner, Registry, `init`, `ops.db`

- **Inhalt:** `CHRONICLE_HOME`, `config.toml`, Prüfung der Rechte, `chronicle init`, `vault add`, `vault list`, `vault remove`, `serve` liest die Registry, `--db`/`--data` bleiben als veraltet. `ops.db` zieht in den Zustandsordner um (`init --adopt-ops`). Spalten `git_name`, `git_email`, Audit `vault_id`.
- **Akzeptanz:** Start ohne Parameter öffnet den registrierten Vault. Falsche `id`, fehlendes `vault.yaml`, Ordner mit Rechten für andere führen zu klaren Fehlern. Bootstrap-Wege (Terminal, Umgebung, `/setup`) funktionieren unverändert. `remove` löscht nie den Vault-Ordner.
- **Test:** `tmp_path` als `CHRONICLE_HOME`. `tests/test_admin_cli.py`, `tests/test_setup.py` anpassen. Migration einer alten `ops.db` (Idempotenz).
- **Risiko:** Docker-Compose-Aufruf und Dokumentation ändern sich (Volume `/state`). `docker-compose.yml`, `Dockerfile`, CI-Smoke-Test (`docker` Job) mit anpassen.

## Teil 9: Mehrere Vaults

- **Inhalt:** Aktiver Vault in der Sitzung, URL-Präfix `/v/<id>/…`, Vault-Umschalter, Schreibvorgänge tragen die `id`, Audit mit `vault_id`.
- **Akzeptanz:** Ein Schreibaufruf mit falscher `id` scheitert. Zwei Vaults in einer Testinstallation zeigen nie Daten des anderen (Test über alle Routen).
- **Test:** Routen-weite Verwechslungstests mit zwei Fixtures-Vaults. Browser-Test für den Umschalter.
- **Risiko:** Viele Routen und Vorlagen betroffen. `api.py`/`pages.py` aufteilen (Issue #127) macht diesen Teil leichter und sollte vorher stehen.

## Teil 10: Index im Cache, Aktualisierung über Commit

- **Inhalt:** `cache/<vault-id>/index.db`, Tabelle `meta`, Gültigkeitsregeln, Aktualisierung über `git diff --name-status` und `status --porcelain`, Neuaufbau im Zweifel.
- **Akzeptanz:** Löschen von `cache/` ändert nichts außer Startzeit. Branch-Wechsel und Merge führen zu richtigem Index (Vergleich mit Neuaufbau, Test). Beschädigte Datei führt zu Neuaufbau.
- **Test:** Property-artig: nach zufälliger Folge von Änderungen ist der inkrementelle Index gleich dem frisch gebauten.
- **Risiko:** Stille Abweichungen. Der Vergleichstest ist Pflicht.

## Teil 11: `vault init`, `vault clone`, Vorlage

- **Inhalt:** `chronicle vault init <ordner> --name …` (Manifest, `.gitignore`, `.github/CODEOWNERS`, Workflow-Aufruf (Teil 12), PR-Vorlage, README mit Branch-Protection-Checkliste), `vault init --adopt` für einen vorhandenen Ordner, `vault clone <url>` (ADR 0021, Punkte 5 und 6). Optional ein Skript `scripts/vault-protect.sh` mit `gh api`-Aufrufen als Vorlage (wird **nicht** automatisch ausgeführt).
- **Akzeptanz:** Neuer Vault besteht `check` und `git status` ist sauber nach dem ersten Commit. `clone` lehnt böse URLs ab, setzt `core.symlinks=false`, trägt in die Registry ein.
- **Test:** lokale bare Repositories als Remote. Kein Netz.
- **Risiko:** Die Branch-Protection-Einstellungen lassen sich nicht im Repository ablegen. Die Checkliste kann veralten (offene Frage 4).

## Teil 12: Wiederverwendbarer Prüf-Workflow

- **Inhalt:** `.github/workflows/vault-check.yml` mit `workflow_call`, `chronicle check --secrets` (sobald Teil 16 steht, vorher ohne), `pull_request`, `contents: read`, Aktionen per SHA. Der Vault ruft ihn per SHA des Code-Repositorys auf. `topic-files.yml` prüft vorerst weiter `data/`.
- **Akzeptanz:** Der Workflow läuft in einem Testrepository grün und lehnt einen Pull Request mit kaputtem Event ab.
- **Test:** In einem eigenen Testrepository auf GitHub, dokumentiert (kein automatischer Test möglich).
- **Risiko:** Kopplung von Vault-CI und Code-Version. Die SHA muss beim Formatwechsel angehoben werden (ADR 0022). Zugriff auf das Code-Repository aus privaten Vaults: wenn es privat ist, braucht der Vault die Erlaubnis zur Nutzung wiederverwendbarer Workflows.

## Teil 13: Identität, Commit je Speichern, Arbeits-Branch

- **Inhalt:** Benutzerfelder `git_name` und `git_email` in Oberfläche und CLI, Pflicht vor dem ersten Schreiben. Jede erfolgreiche Schreiboperation wird committet (Autor, Trailer) auf `chronicle/<benutzer>/<datum>-<name>`, Branch wird bei Bedarf von `origin/<standard>` angelegt. Modus `workflow = "direct"` für Einzelpersonen.
- **Akzeptanz:** Nach dem Speichern zeigt `git log` einen Commit mit Autor und Trailer. Die Konfiguration des Benutzers bleibt unverändert (Test). Schlägt ein Commit fehl, wird die Dateiänderung zurückgenommen (alles oder nichts).
- **Test:** Temporäres Repo. Zwei Benutzer nacheinander im selben Klon (verhalten wie ADR 0015).
- **Risiko:** Mehrere Benutzer der gleichen Installation im selben Klon: Branch-Wechsel unter dem Arbeitsstand eines anderen. Vorläufig: ein Arbeits-Branch je Klon, ein Benutzer gleichzeitig. Die Oberfläche warnt, wenn ein anderer Benutzer den Branch hält (offene Frage 8).

## Teil 14: Einreichen und Aktualisieren

- **Inhalt:** `sync.synchronise` wird zu „Einreichen“ und „Aktualisieren“ (ADR 0015, Punkte 4 und 5; ADR 0016, Punkte 1, 8, 9): Netzwerkaufrufe außerhalb der Sperre und mit Zeitlimit (#131, Punkt 1), `--ff-only` für den Standard-Branch, Merge mit Treiber in den Arbeits-Branch, Push nur des Arbeits-Branches ohne `--force`, Link zum Pull Request. Merge-Treiber auf Event-Dateien vereinfacht und mit Unterscheidung Konflikt/Fehler.
- **Akzeptanz:** Push auf den Standard-Branch ist im Code nicht mehr möglich (Test). Zwei Klone ändern verschiedene Felder desselben Events, ein Klon reicht ein, der andere aktualisiert: Feldweise zusammengeführt. Gleiches Feld: Abbruch mit Dateiliste, Zustand unverändert.
- **Test:** Zwei Klone mit bare Remote, wie `tests/test_sync.py`. Hängender „Remote“ (Skript) beweist, dass die Sperre frei bleibt.
- **Risiko:** Größter Eingriff in `sync.py`. `reset --hard` nach fehlgeschlagener Prüfung nur nach Commit aller Änderungen. Hier gezielt reviewen.

## Teil 15: Freigabe-Status und Konfliktauflösung (zwei PRs)

- **15a:** Merkmal „freigegeben / wartet auf Freigabe“ je Event (ADR 0015, Punkt 3), Kopfzeile mit Anzahl und Link zum Vergleich.
- **15b:** Auflösung von Konflikten in der Oberfläche (#131, Punkt 2): je Datei beide Seiten, Wahl eigene/fremde/je Feld. Anhangs-Konflikte (ADR 0016, Punkt 7).
- **Akzeptanz:** Der Status stimmt mit `git` überein (Test gegen berechneten Diff). Ein Konflikt lässt sich ohne Terminal auflösen und das Ergebnis besteht `check`.
- **Test:** Seiten-Tests wie `tests/test_topics_ui.py`, ein Browser-Test.
- **Risiko:** Auflösung ist Fehler-anfällig in der Oberfläche (CSRF, Parallelität). Eigene Sicherheitsprüfung des PRs.

## Teil 16: Geheimnisse und Bereinigung

- **Inhalt:** `chronicle check --secrets` (ADR 0020, Punkt 3) mit Allowlist, Prüfung vor jedem Speichern. Skript `scripts/vault-purge.sh` mit Trockenlauf und Beschreibung (Issue #98), im Testrepository durchgespielt.
- **Akzeptanz:** Bekannte Muster in `action`, `details` und `.log`-Anhang werden gefunden, kein Treffer in `data/`. Das Skript entfernt in einem Testrepository eine Datei aus der gesamten Historie, ein frischer Klon enthält sie nicht (`git log --all -S`).
- **Test:** Mustertabelle (Treffer/kein Treffer), Skript-Test mit lokalem Repo.
- **Risiko:** Fehlalarme und Lücken. Das Muster-Set bewusst klein halten und erweiterbar.

## Teil 17: Dokumentation

- **Inhalt:** README (Datenhaltung, Konfiguration, Start), `docs/notation.md` auf Format 1 (neues Dokument `docs/notation-v1.md`, altes bleibt bis Teil 18), CONTRIBUTING (ADR-Hinweis „0003 ist die letzte“ ist längst veraltet), Statuszeilen von ADR 0008 und 0009.
- **Akzeptanz:** Jede Aussage der README ist am Code gegengeprüft. Links funktionieren.

## Teil 18: Beispiel-Vault, alte Lesepfade

- **Inhalt:** `data/` wird per `vault migrate` zu `examples/vault/`, `topic-files.yml` und `ci.yml` angepasst, Format-0-Lader nur noch für `vault migrate`.
- **Akzeptanz:** CI grün, `data/` entfernt, Tests verwenden `examples/vault/`.
- **Risiko:** Viele Verweise (Docker, README, Tests, `scripts/`). Suchen mit `grep data/`.

---

## Vorschläge für Issues (nicht angelegt)

Das Projekt nutzt GitHub-Issues (siehe #89, #90, #123). Die Texte unten sind Vorschläge. Ohne ausdrückliche Rückfrage lege ich keine an.

| Vorschlag | Titel | Inhalt in einem Satz |
|---|---|---|
| Epic | Vaults in Git: Code und Inhalte trennen, Freigabe per Pull Request | Verweist auf ADR 0011 bis 0022 und diesen Plan, Checkliste der Teile 1 bis 18 |
| Teil 1 | Sicherheit: symbolische Links, Aliase und doppelte Schlüssel in Topic-Dateien abweisen | Heute folgt der Lader Dateilinks und nimmt bei doppelten Schlüsseln den letzten Wert (ADR 0021, 11 bis 14) |
| Teil 3 | Ein Modul für alle Git-Aufrufe mit Zeitlimit, Umgebung und URL-Prüfung | ADR 0021 Punkte 1 bis 10 |
| Teil 14 | „Abgleichen“ durch „Einreichen“ ersetzen: Arbeits-Branch, kein Push auf `main` | Ersetzt Teile von #90 und #131 |
| Teil 16 | Prüfung auf Geheimnisse und Bereinigungsverfahren | Zusammenführung mit #98 |
| bestehend | #123 entscheiden: Variante A | ADR 0011 schlägt Variante A vor |
| bestehend | #125 | Erledigt sich im Kern mit Teil 6 (Format 1) |
| bestehend | #131 | Wird in Teil 14 und 15 aufgenommen |
| bestehend | #128 | Der Merge-Treiber wird mit Teil 14 einfacher |

---

## Offene Fragen an den Projektinhaber

Nur echte Entscheidungen. Für alles andere steht in den ADR eine begründete Vorgabe.

1. **Anhänge in Git (ADR 0019) und Hoster.** Ändert die bestätigte Entscheidung aus ADR 0008/0009 (Anhänge außerhalb von Git). Sind Sie einverstanden, dass Anhänge im Vault liegen, und liegen die echten Vaults bei GitHub (US-Anbieter, Auftragsverarbeitung, Datenschutzbeauftragte(r)) oder bei einem selbst betriebenen Git-Server? Beides trägt die Architektur, die Antwort bestimmt Vertragslage und Löschbarkeit.
2. **Signierte Commits** als Pflicht der Vorlage oder nur Empfehlung? Vorgabe: Empfehlung.
3. **Vaults mit nur einer Person:** Freigabeanzahl 0 mit Pull Request (kein echtes Vier-Augen-Prinzip) oder `workflow = "direct"`? Vorgabe: `direct` ist erlaubt, wird in der Oberfläche als „ohne Freigabe“ gekennzeichnet.
4. **Wer richtet Branch Protection ein?** Es gibt keinen Weg, sie im Repository abzulegen. Reicht eine Checkliste mit optionalem `gh`-Skript, oder soll es ein Prüfwerkzeug geben, das die Einstellungen über die API abfragt (braucht einen Token)? Vorgabe: Checkliste und Skript.
5. **Pull Request aus der App anlegen** (über `gh` oder API, braucht Token) oder Link? Vorgabe: Link, bis es ein Bedürfnis gibt.
6. **Entfällt der Datenbankmodus ganz (ADR 0011, #123 Variante A)?** Betrifft vorhandene Installationen ohne Datenordner. Vorgabe: ja, mit Export und Migration.
7. **Plattformen:** nur macOS und Linux? Vorgabe: ja, Windows nicht.
8. **Mehrere Benutzer an derselben Installation:** Reicht für den ersten Ausbau „ein Arbeitsplatz, eine Person“ (ADR 0014)? Vorgabe: ja. Ein gemeinsamer Teamserver ist ein eigenes, späteres Thema.
9. **Notfall bei Vorfällen:** Genügt es, dass die App sofort den eigenen Stand zeigt und die Freigabe nachträglich erfolgt (ADR 0015, Punkte 3 und 8)? Oder soll es für aktive Incidents einen Weg ohne Pull Request geben?
10. **Aufbewahrungsfristen** und Verfahren zur Löschung personenbezogener Daten (ADR 0020): Gibt es Vorgaben, die vor dem ersten echten Vault bekannt sein müssen?

## Bereiche mit der größten Unsicherheit (zum gezielten Review)

1. ADR 0012: Heimat im Pfad, `also_in` am Event, Verschieben bei Wechsel der Heimat (Anhangs-Pfade und Rename-Erkennung von Git).
2. ADR 0021, Punkt 3 und 4: Umgebung und `-c`-Schalter. Zu streng bricht Credential Helper und SSH, zu locker lässt Angriffsfläche. Mindestversion 2.39 ist geschätzt.
3. ADR 0021, Punkt 13: ein eigener strenger YAML-Parser (Ereignisstrom) ist neuer Code im sicherheitskritischen Pfad. Alternative: nur Aliase und Duplikate über `yaml.compose` prüfen.
4. ADR 0015, Punkt 3: der Status „freigegeben“ aus dem Vergleich mit `origin/<standard>` bei Squash-Merges. Nach einem Squash ist der Arbeits-Branch nicht im Standard-Branch enthalten, nur der Dateiinhalt gleich. Der Vergleich geht über den Inhalt und nicht über Commits, ob das in allen Fällen trägt (Änderung nach dem Merge), ist nicht belegt.
5. ADR 0015, Punkt 1: ein Arbeits-Branch je Klon und die Folgen bei mehreren Benutzern (Teil 13).
6. ADR 0019: Anhänge in Git ohne LFS. Größenschwellen (200 MiB, 1 GiB) sind geschätzt.
7. ADR 0020, Punkt 10: der Bereinigungsablauf ist nicht erprobt; die Aussage zu `refs/pull/*` und zum Hoster-Support ist Annahme und mit GitHub zu klären.
8. ADR 0017, Punkt 4: Aktualisierung des Index über `git diff` bei nicht committeten Änderungen. Ein Fehler hier zeigt falsche Daten, deshalb der Vergleichstest in Teil 10.
9. Merge auf GitHub ruft keinen eigenen Treiber auf. Das ist hier als Tatsache angenommen und sollte vor der Umsetzung in einem Testrepository bestätigt werden.
