# Umsetzungsplan: Vaults in Git

- **Status:** Entwurf, überarbeitet nach Review (Sicherheit und Architektur). Nichts davon ist umgesetzt.
- **Stand:** 2026-10-25
- **Grundlage:** [`vault-git.md`](vault-git.md) und ADR 0011 bis 0023

## Regeln für jeden Teil

- Ein Teil ist ein Pull Request, eigener Branch von aktuellem `main`, klein und einzeln reviewbar (CONTRIBUTING). Richtwert: unter 500 geänderte Zeilen ohne Tests.
- Ein Teil lässt `main` lauffähig und die Tests grün. Kein Teil setzt einen Zwischenzustand voraus, der nicht gemergt ist.
- Verhalten ändert sich nur dort, wo es der Teil nennt.
- Tests laufen ohne Netz. Git-Tests nutzen lokale Repositories in `tmp_path` und einen lokalen „Remote“ (bare Repo).
- Jeder Teil passt die betroffene Dokumentation im selben PR an. Das Gesamtkapitel (Teil 17) kommt zum Schluss.

## Reihenfolge der Ausführung

Die Nummern sind Namen, nicht die Reihenfolge. Ausgeführt wird so:

1. **Sofort, wirkt auf `main`:** 0a, 0b, dann 1. Sie beheben im Review **belegte** Lücken des Ist-Zustands und hängen an nichts.
2. **Vereinfachen:** 2a, 2b, 2c (Datenbankmodus entfernen). Parallel dazu 3 (`git.py`).
3. **Format:** 4, 5, danach **7 (Migration) vor oder zusammen mit 6b**, 6a, 6b, **18 direkt nach 6b**.
4. **Lokale Konfiguration:** 8, 8b, 10.
5. **Vault anlegen und prüfen:** 11, 12, 12b, **12a (Annahmen zu GitHub am Testrepository prüfen) vor 14**.
6. **Zusammenarbeit:** 13, 14a, 14b (zusammen ausgeliefert, bis dahin Funktionsschalter aus), 15a, 15b, 20.
7. **Bereinigung und Doku:** 16a, 16b, 17 (am Ende).
8. **Nach Nutzerentscheidung:** 19 (Incident-Branch). **Zurückgestellt:** 9 (mehrere Vaults).

Abhängigkeiten (vereinfacht): `0a,0b → 1 → 4 → 5 → 7 → 6b → 18`; `2 → 6b`; `4 → 8 → 8b, 10`; `3 → 11, 14a`; `12a → 14b`; `6b, 8, 13 → 14a → 14b → 15a, 15b, 20, 21`.

## Übersicht

| Teil | Titel | Hängt ab von | Größe | ADR | Behebt | Verhalten ändert sich |
|---|---|---|---|---|---|---|
| **0a** | Sofort: Treiber isolieren, sicher schreiben, Links abweisen | nichts | S | 0021 | K1, H2 | ja (strenger) |
| **0b** | Sofort: YAML-Vorabscan, Fehlermeldungen, `fullmatch` | nichts | S/M | 0021 | H1, M1, M2 | ja (strenger) |
| 1 | Sicheres Lesen, Rest: Größen, Zeichen, strenge Typen, Host-Prüfung | 0a, 0b | S | 0021 | N1, N2, N3, N5 | ja |
| 2a | Datenbankmodus entfernen: Events | nichts | M | 0011 | | ja |
| 2b | Datenbankmodus entfernen: Topics, Verknüpfungen | 2a | M | 0011 | | ja |
| 2c | Datenbankmodus entfernen: Anhänge, Grabsteine, Start nur mit Datenordner | 2b | M | 0011 | | ja |
| 3 | Modul `git.py` mit Härtung | nichts (Matrix vorab) | M/L | 0021 | H3, H4, M8, M9 | nein (Umbau) |
| 4 | Manifest und Formatversion | 1 | S | 0012, 0022 | M4 | nein |
| 5 | Format 1 lesen und prüfen | 1, 4 | M | 0012 | M5, N5 | nein (nur CLI) |
| 7 | Migration `vault migrate`, Prüfsumme Pflicht | 5 | M | 0022, 0019 | H8 | nein (neuer Befehl) |
| 6a | Format 1 schreiben: Events, Topics, Verknüpfungen | 2c, 5 | L | 0012 | | ja |
| 6b | Server liest und schreibt Format 1, Anhänge, Index je Datei | 6a, **7** | L | 0012, 0017 | H8 | ja |
| 18 | Beispiel-Vault statt `data/`, alte Lesepfade entfernen | 6b, 12 | S | 0022 | H8 | nein |
| 8 | Zustandsordner, Registry, `init`, `ops.db` umziehen, Backup/Restore | 2c, 4 | M | 0013 | H7 | ja |
| 8b | Container-Betrieb (Git, SSH, Volumes, nicht read-only für Vault) | 3, 8 | M | 0013 | M10 | ja |
| 10 | Cache-Ordner und `meta`-Kopf des Index | 6b, 8 | S | 0017 | M7 | nein |
| 11 | `vault init`, `vault clone`, Vorlage (inkl. Ruleset-JSON) | 3, 4, 8 | M | 0011, 0021 | H9 | nein |
| 12 | Wiederverwendbarer Prüf-Workflow (erweitert) | 5 | M | 0021 | M13 | nein |
| 12a | Annahmen zu GitHub im Testrepository prüfen | 11, 12 | S | 0015, 0016 | | nein (Bericht) |
| 12b | `chronicle vault doctor` | 3, 11 | S | 0015 | H9 | nein |
| 13 | Identität: `git_name`, `git_email` | 8 | S | 0014 | M14 | ja |
| 14a | Arbeits-Branch, Commit je Speichern (hinter Schalter) | 3, 6b, 8, 13 | M | 0015 | M12 | nur mit Schalter |
| 14b | Einreichen, Aktualisieren, Zustandsautomat, Arbeitsstand verwerfen | 12a, 14a | L | 0015, 0016 | H4, H5 | ja |
| 15a | Freigabestatus je Event in der Oberfläche | 14b | M | 0015 | H5 | ja |
| 15b | Konfliktauflösung in der Oberfläche | 14b | M | 0016 | | ja |
| 16a | Prüfung auf Geheimnisse (`check --secrets`) | 5 | M | 0020 | | nein |
| 16b | Bereinigungsskript und Erprobung am Testrepository | 11 | M | 0020 | M14 | nein |
| 17 | Dokumentation auf den neuen Stand | alle zuvor | S | alle | N6 | nein |
| 19 | Incident-Branch und Live-Modus (**nach Nutzerentscheidung**) | 14b | M | 0015 | H6 | ja |
| 20 | Berichte und Exporte aus dem freigegebenen Stand | 14b | S | 0015 | M11 | ja |
| 8c | Audit-Log mit Hash-Kette, Verankerung in Commits (optional) | 8, 14a | S/M | 0013 | | nein |
| 22 | Verschlüsselung: SQLCipher für `ops.db`/Index oder Vault-Verschlüsselung (**nur nach Nutzerentscheidung**) | 8, Entscheidung 11 | M bis L | 0023 | | ja |
| 9 | Mehrere Vaults, URL-Präfix (**zurückgestellt**) | #127, 6b, 8 | M | 0018 | M15 | ja |

Größe: S bis etwa 150 Zeilen, M bis etwa 400, L darüber (dann in zwei PRs teilen). Teile mit L werden geteilt, wenn der Entwurf größer wird.

---

## Teil 0a: Sofort-Härtung, Treiber und Schreiben (K1, H2)

- **Stand:** Wesentliche Teile dieses Teils sind mit PR #138 umgesetzt (Erkenntnisse unten). Dieser Plan hält fest, was gilt.
- **Inhalt:** Merge-Treiber als `<python> -P -c <Start> <Paketordner> %O %A %B` eintragen (`sync._use_merge_driver`; Paketordner aus `__file__` der App, nie aus dem Vault; der Start entfernt cwd und leere Einträge aus `sys.path`; `-I` wurde verworfen, weil es `pip --user` bricht). Das Arbeitsverzeichnis des Treibers bleibt die Vault-Wurzel, der Schutz kommt vom `-P`-Start. Ein Treiberabsturz ist ein Fehler, kein Konflikt. Start der App mit Vault als cwd verweigern. **Blocklist** der Wurzel (Suffixe `.py`/`.pyc`/`.pyw`/`.pyd`/`.so` inkl. Tags, Ordner mit `__init__.*`, Namen `chronicle`/`sitecustomize`/`usercustomize`). `store._atomic_write`: Temp-Datei per `mkstemp` oder `O_CREAT|O_EXCL|O_NOFOLLOW` im selben Ordner, `lstat` des Ziels. `folder_lock`: `.chronicle.lock` mit `O_NOFOLLOW` öffnen, Link ist Fehler. Modusprüfung `git ls-tree -r` auf `120000` und `160000` **im Merge-Ergebnis** (Rücknahme mit `git reset --merge`, nicht `--hard`); `base_timelines` überspringt Links, damit ein Link im Remote den Vault nicht dauerhaft sperrt. Im Dateisystem sind Links im ganzen Baum ein Prüffehler (`os.scandir`-Durchlauf, der Datenordner selbst darf ein Link sein); `.gitmodules` wird nicht eigens abgewiesen. (Die volle Allowlist der Vault-Wurzel kommt mit Format 1 in Teil 5.)
- **Akzeptanz:** Test: `yaml.py` im Vault-Ordner wird beim Merge **nicht** importiert (Marker-Datei wird nicht geschrieben). Test: Symlink `.ev-1.yaml.tmp` und `.chronicle.lock` führen zu Fehler, Zieldatei unverändert. Test: Link `x.yaml` auf Datei außerhalb wird mit Dateiname abgewiesen, Zielinhalt erscheint nicht in der Meldung.
- **Risiko:** gering. Der Vault-Ordner mit Links wird unbrauchbar, was gewollt ist.

## Teil 0b: Sofort-Härtung, YAML, Meldungen, Muster (H1, M1, M2)

- **Inhalt:** Vorabscan über den Ereignisstrom von `yaml.parse` (ADR 0021, Punkt 19: Tiefe 12, Anker, Alias, Tags, Merge-Key `<<`, doppelte Schlüssel, 20.000 Knoten, Nicht-String-Schlüssel), dann erst `SafeLoader`. Für Fremdinhalt der reine Python-Lader, der C-Lader nur für geprüfte eigene Dateien. Der Scan läuft in `loader.parse_timeline`, `merge.main` und `sync.base_timelines`. Pydantic-Modelle `hide_input_in_errors=True`, Meldungen auf eine Zeile kürzen. `USERNAME_PATTERN` mit `fullmatch`, ebenso Keys und weitere Muster (`re.match` mit `$` akzeptiert `"anna\n"`, belegt).
- **Akzeptanz:** 120 KB `[`-Verschachtelung ergibt eine Fehlermeldung statt SIGSEGV (Test in Unterprozess). Alias, Anker, Tag, `<<`, doppelter Schlüssel: je ein Test mit Datei und Zeile. `"anna\n"` wird abgelehnt. Keine Meldung enthält Dateiinhalt (Test mit einem Link-Ziel als Inhalt). `scripts/measure_index.py` zeigt die Wirkung des Lader-Wechsels (Vollaufbau 10.000 Dateien war 0,63 s).
- **Risiko:** Bestehende Dateien mit Aliasen oder doppelten Schlüsseln werden unbrauchbar. Im PR mit `chronicle check data` belegen.

## Teil 1: Sicheres Lesen, Rest

- **Inhalt:** Größenbegrenzung vor dem Lesen, Steuer- und Bidi-Zeichen, strikte Typen für Textfelder (`no`, `on`, `1:30` in YAML 1.1, N3), Homoglyphen-Warnung (N2), Prüfung auf Keys, die sich nur in der Schreibweise unterscheiden, **auch lokal** (N5), `TrustedHostMiddleware` und `Origin`-Prüfung in `api.py` (N1). Die Allowlist der Wurzel folgt in Teil 5.
- **Akzeptanz:** Je Regel ein Test; `data/` besteht unverändert.
- **Risiko:** gering.

## Teil 2a bis 2c: Zweiten Schreibpfad über die Datenbank entfernen (Issue #123)

**Klarstellung (Entscheidung des Projektinhabers):** Die Datenbank bleibt. Sie dient weiter für Konten, Sitzungen, Audit-Log, etwaige Schlüssel-Metadaten (`ops.db`) und als Index. Entfernt wird nur der Weg, Topics, Events und Anhänge **über die Datenbank** zu schreiben und zu speichern. Die Wahrheit für Inhalte liegt im Vault.

- **Inhalt:** Alle Zweige `if data_dir is not None` in `repository.py` entfernen, in drei PRs (Events, Topics und Verknüpfungen, Anhänge/Grabsteine/Start nur mit Datenordner). Danach streichen: `deleted_events`, `deleted_evidence`, gespeicherte Anhangs-Bytes im Index. Tabellen für Konten, Sitzungen, Audit und Einstellungen bleiben unberührt. Ein Schritt in `db.py` entfernt die toten Tabellen idempotent. Vorher in der Migration (Teil 7) und im Export (`exporter.py`) den Weg für Datenbank-Installationen sichern.
- **Akzeptanz:** `repository.py` hat einen Schreibpfad. `serve` ohne `--data` endet mit klarer Meldung. Tests laufen im Dateimodus (Fixtures in `conftest.py`).
- **Risiko:** Größter Umbau. Betreiber ohne Datenordner müssen vorher exportieren (offene Frage 6).

## Teil 3: Modul `git.py`

- **Inhalt:** ADR 0021, Punkte 1 bis 14 und 25 (der Treiber-Start nach Punkt 18 liegt bereits in Teil 0a und wird hier nur in `git.py` übernommen): Programmpfad und **Mindestversion gepatchter Stand ab 2.45.1 beziehungsweise aktuelle Wartungsversion** (Verifikation der genannten CVE-Hinweise vor der Umsetzung), `cwd`, `branch_slug()` mit `fullmatch` und `check-ref-format`, Umgebungs-Whitelist, feste `-c` (inkl. `core.sshCommand=ssh`, `core.pager=cat`, `core.autocrlf=false`), Fetch/Push nur benötigter Refs (H4), Prüfung des erwarteten `origin`, URL-Regeln, Zeitlimits, Parsing nur stabiler Formate. `sync.py` nutzt nur noch `git.py`.
- **Vorab:** Test-Matrix (macOS Keychain, Linux libsecret, Git Credential Manager, `ssh-agent`, SSH- und GPG-Signierung) mit Ergebnis im PR; die Whitelist folgt der Matrix (M8).
- **Akzeptanz:** Kein `subprocess` außerhalb von `chronicle/git.py` (Test). Zeitlimit beendet einen hängenden Prozess. Böse URLs und Branch-Namen werden abgelehnt (Tabellentest). Ein `core.sshCommand` in `.git/config` eines Testklons wird nicht ausgeführt. Hooks laufen nicht. Alle Tests in `tests/test_sync.py` bleiben grün.
- **Risiko:** Eingeschränkte Umgebung bricht Anmeldung am Hoster. Daher die Matrix.

## Teil 4: Manifest und Formatversion

- **Inhalt:** `VaultManifest` (`format`, `id` als Etikett, `name`, `limits` nur senkend), Erkennen von Format 0 und 1, Ablehnen unbekannter Formate. `chronicle vault info`.
- **Akzeptanz:** `format: 2` führt zu einer Meldung zum Aktualisieren. `limits` über der Obergrenze werden abgelehnt. Bidi im `name` wird abgelehnt.

## Teil 5: Format 1 lesen und prüfen

- **Inhalt:** Lader und Regeln aus ADR 0012 (inkl. **voller Allowlist der Wurzel**, die die Blocklist aus Teil 0a ablöst, K1) und ADR 0021; `also_in`-Hinweis an die CI (M5); JSON-Schemas; `check`, `plan`, `build`, `apply` für beide Formate. Intern dieselbe `Timeline`-Darstellung.
- **Akzeptanz:** Beispiel-Vault in `tests/fixtures/vault/` besteht. Jede Prüfregel hat einen Fehlertest, auch `yaml.py` in der Wurzel. Schreibweisen-Duplikate werden gemeldet.
- **Risiko:** Gleichwertigkeit der inneren Darstellung (Heimat im Pfad). Gezielt reviewen.

## Teil 7: Migration `vault migrate`

- **Inhalt:** ADR 0022, Punkte 4 bis 7, Gleichwertigkeitsprüfung, deterministische Ausgabe. `sha256` Pflicht in Format 1.
- **Akzeptanz:** `data/` migriert und ist gleichwertig. Zweiter Lauf ergibt gleiche Bytes. Absichtlich verfälschte Ausgabe lässt den Befehl scheitern und den Ausgabeordner entfernen.
- **Risiko:** Sinnverlust bei `refs` zu `also_in` (Richtung gedreht), durch Mengenvergleich abgesichert.

## Teil 6a und 6b: Format 1 schreiben und bedienen

- **6a:** `store.py` schreibt Events als eigene Dateien (anlegen, ändern feldweise mit Kommentaren, löschen, Heimat wechseln, `also_in`), Topics (Ordner), atomar, mit Versionsvergleich und sicherem Schreiben (Teil 0a). Anhänge ändern sich beim Heimatwechsel nicht (`evidence/<event-key>/`).
- **6b:** `repository.py` und Index auf Event-Dateien, Anhänge mit `sha256`, Link-Anhänge (`link`, `evidence.link_hosts`, ADR 0019 Punkt 7), Markdown als Anhangstyp (Punkt 8), Server liest Format 1. **Format 0 wird erst abgeschaltet, wenn Teil 7 gemergt ist.**
- **Akzeptanz:** Alle Schreibtests laufen auf Format 1, Rollback und 409 wie heute, eine Änderung schreibt genau eine Datei, 10.000 Events unter 1 Sekunde je Änderung. Die Schritte aus #125 sind bereits auf `main` (`c7bb327`, `5c017a6`, PR #136) und bleiben.
- **Risiko:** Größter fachlicher Teil. Reihenfolge 2 vor 6 einhalten.

## Teil 18: Beispiel-Vault, alte Lesepfade

- **Inhalt:** `data/` wird per `vault migrate` zu `examples/vault/`. `topic-files.yml` und `ci.yml` angepasst. Format-0-Lader nur noch für `vault migrate`.
- **Akzeptanz:** CI grün, `data/` entfernt. Direkt nach 6b.

## Teil 8 und 8b: Zustandsordner und Container

- **8:** `CHRONICLE_HOME` (0700), `config.toml` mit lokaler ID, Klon-Kennung, Manifest-Etikett, `origin`, Linkvorlage; `init`, `vault add` (Allowlist der `.git/config`), `list`, `remove`; `serve` liest die Registry; `ops.db` zieht um (`init --adopt-ops`); Spalten `git_name`, `git_email`, Audit `vault_id`. **`backup.py` sichert `config.toml` und `ops.db`, Wiederherstellung wird getestet.**
- **8 (Ergänzung):** Abgrenzung der Orte nach ADR 0013, Punkt 5 (Tabelle). `ops.db` bekommt eine Tabelle `key_metadata` (Kennung, Fingerabdruck, Datum) **nur wenn** ADR 0023 (b) gewählt wird, vorher nicht.
- **8c (optional):** Audit-Log mit `prev_hash`/`entry_hash` (SHA-256), Prüfbefehl `chronicle audit verify`, Kopfwert als Trailer `Chronicle-Audit-Head` in Commits (ab 14a). Macht Veränderungen erkennbar, verhindert sie nicht. Test: Löschen oder Ändern eines mittleren Eintrags wird gemeldet.
- **8b:** Container mit `git`, `ssh`, beschreibbarem Vault-Volume, `/state`, Deploy Key als Datei, `read_only` und `cap_drop` wo möglich. Bis dahin liest der Container nur.
- **Akzeptanz:** Start ohne Parameter öffnet den Vault. Doppelte Manifest-ID wird abgelehnt. Bootstrap-Wege funktionieren. Docker-Smoke-Test in der CI passt.
- **Risiko:** `docker-compose.yml`, `Dockerfile`, CI-Job `docker` ändern sich.

## Teil 10: Cache-Ordner und `meta`-Kopf (verkleinert)

- **Inhalt:** `cache/<local-id>/index.db`, `meta`, Gültigkeitsregeln. Aktualisierung bleibt über den Fingerabdruck je Datei (ADR 0017, Punkt 4), Git nur als Beschleuniger mit `--no-renames`.
- **Akzeptanz:** Löschen von `cache/` ändert nichts außer der Startzeit. Inkrementeller Index gleich frisch gebautem (Vergleichstest).

## Teil 11: `vault init`, `vault clone`, Vorlage

- **GitHub ist der Standard:** Vorlage-Repository, Ruleset-JSON, Linkvorlage und Workflow sind GitHub-Voreinstellungen; die Schicht ist ein Erweiterungspunkt, andere Hoster sind „unterstützt, nicht Standard“ (ADR 0011, Punkt 6). `vault doctor` (12b) nutzt `gh`.
- **Inhalt:** Manifest mit **neuer** `id`, `.gitignore`, `.gitattributes` (nur Vorlagenzeilen), `.github/CODEOWNERS`, **`.github/ruleset.json`** (importierbar), Workflow-Aufruf mit SHA, PR-Vorlage, Dependabot, README mit Checkliste; `vault init --adopt`; `vault clone` mit URL-Regeln und `core.symlinks=false`.
- **Akzeptanz:** Neuer Vault besteht `check`. Böse URLs abgelehnt. Kein Netz im Test.
- **Risiko:** Die Checkliste kann veralten; Ersatz ist `vault doctor` (12b).

## Teil 12, 12a, 12b: CI, Annahmen, Doktor

- **12:** `.github/workflows/vault-check.yml` (`workflow_call`), `pull_request` **und `merge_group`**, nie übersprungen, `contents: read`, keine Secrets, `pip --require-hashes`, Doppel-Pinning (Workflow und Code-Repo-SHA), `also_in`-Hinweis. `topic-files.yml` prüft vorerst `data/`.
- **12a:** In einem Testrepository prüfen und im PR berichten: (1) GitHub-Merge ruft **keinen** eigenen Merge-Treiber auf, (2) Sichtbarkeit und Verhalten von `refs/pull/*` bei Bereinigung, (3) Squash-Merge und Signaturen, (4) übersprungener erforderlicher Check, (5) Tarifgrenzen für Rulesets bei privaten Repositories.
- **12b:** `chronicle vault doctor`: lesend, über das `gh` des Benutzers (keine gespeicherten Tokens), prüft Schutz des Standard-Branches, Freigabeanzahl, Code-Owner, erforderliche Checks, Force-Push, Bypass-Liste.
- **Risiko:** Kopplung von Vault-CI und Code-Version (SHA wird beim Formatwechsel angehoben).

## Teil 13: Identität

- **Inhalt:** `git_name`, `git_email` in Oberfläche und CLI, Pflicht vor dem ersten Schreiben, Vorgabe `noreply`-Adresse (kein Vorbelegen aus privater Adresse).
- **Akzeptanz:** Benutzer ohne `git_email` kann lesen, nicht schreiben.

## Teil 14a und 14b: Arbeits-Branch und Einreichen

- **14a:** Arbeits-Branch `chronicle/<klon-kennung>/<datum>-<name>`, lokaler Commit je Speichern (Autor = App-Benutzer, Trailer), höchstens eine Schreibsitzung je Klon. **Hinter Funktionsschalter; nicht ohne 14b ausgeliefert.**
- **14b:** `sync.synchronise` wird zu „Einreichen“ und „Aktualisieren“ (ADR 0015, Punkte 4 bis 8; ADR 0016): Zustandsautomat (offen, eingereicht, geändert nach Einreichen, abgeschlossen, verworfen), „Arbeitsstand verwerfen“, Remote-Branch eingeführt statt Force, Netz außerhalb der Sperre mit Zeitlimit (#131), Blob-ID-Vergleich, Abschluss über `git diff --quiet origin/<standard> HEAD`, Link aus der Registry-Vorlage. Der Merge-Treiber auf Event-Dateien vereinfacht.
- **Akzeptanz:** Kein Push auf den Standard-Branch (Test). Squash-Merge im Testrepository führt zu „abgeschlossen“. Zwei Klone, verschiedene Felder desselben Events: feldweise zusammengeführt, gleiches Feld: Abbruch mit Dateiliste. Hängender Remote hält die Sperre nicht.
- **Risiko:** Größter Eingriff in `sync.py`. Rücknahmen mit `git reset --merge`, nie `reset --hard` (Fehlerklasse von #126).

## Teil 15a und 15b: Oberfläche

- **15a:** Freigabestatus je Event (Blob-ID), Kopfzeile, Hinweis „Schutz des Hosters nicht bestätigt“, wenn `doctor` nicht grün war.
- **15b:** Konfliktauflösung (#131), Anhangs-Konflikte (ADR 0016, Punkt 7).
- **Risiko:** Auflösung in der Oberfläche braucht eigene Sicherheitsprüfung (CSRF, Parallelität).

## Teil 16a und 16b: Geheimnisse und Bereinigung

- **16a:** `chronicle check --secrets` (ADR 0020, Punkt 3), Prüfung vor jedem Speichern, Allowlist.
- **16b:** Bereinigungsskript mit Trockenlauf, am Testrepository durchgespielt (Issue #98), inklusive `refs/pull/*`, Spiegel und Entfernen von `origin` durch `filter-repo`. **Erst danach kann ADR 0020 „Angenommen“ werden.**

## Teil 17: Dokumentation (am Ende)

- README, `docs/notation-v1.md`, CONTRIBUTING (Hinweis „0003 ist die letzte“ ist veraltet), Statuszeilen ADR 0008/0009, Betriebsanleitung für Branch Protection, Tarifhinweise.

## Teil 19: Incident-Branch (nach Nutzerentscheidung)

- ADR 0015, Punkte 12 bis 14. **Nutzerentscheidung nötig (offene Frage 9).** Ohne Zustimmung entfällt der Teil.

## Teil 20: Berichte aus freigegebenem Stand

- Bericht und Export bauen standardmäßig aus `origin/<standard>`, ungeprüfte Events nur auf ausdrücklichen Wunsch und gekennzeichnet (ADR 0015, Punkt 15).

## Teil 22: Verschlüsselung (nur nach Nutzerentscheidung)

- ADR 0023. Mit der Empfehlung (a)+(c) entfällt der Teil bis auf Anleitung und optionale Prüfung der Festplattenverschlüsselung in `vault doctor`. Gewählt werden könnte SQLCipher für `ops.db`/Index (kleiner Teil) oder (b) Vault-Verschlüsselung mit age (mehrere Teile, neues Format, Änderungen an Merge-Treiber, Index und CI; vorher Prototyp).

## Teil 9: Mehrere Vaults (zurückgestellt)

- Erst bei Bedarf und nach #127. Standard bleibt eine Installation mit einem Vault.

---

## Vorschläge für Issues (nicht angelegt)

| Vorschlag | Titel | Inhalt in einem Satz |
|---|---|---|
| Epic | Vaults in Git: Code und Inhalte trennen, Freigabe per Pull Request | Verweist auf ADR 0011 bis 0022 und diesen Plan |
| 0a | Sicherheit: Merge-Treiber importiert Code aus dem Vault, Schreiben folgt Symlinks | `python -m` mit Vault als cwd, `.ev-1.yaml.tmp`, `.chronicle.lock` |
| 0b | Sicherheit: tief verschachteltes YAML stürzt den C-Lader ab, doppelte Schlüssel, `USERNAME_PATTERN` | belegt im Review |
| 3 | Ein Modul für alle Git-Aufrufe, `.git/config`-Allowlist | ADR 0021 |
| 14b | „Abgleichen“ durch „Einreichen“ ersetzen | ersetzt Teile von #90 und #131 |
| 16 | Prüfung auf Geheimnisse und Bereinigungsverfahren | zusammen mit #98 |
| bestehend | #123 | ADR 0011: Variante A |
| bestehend | #125 | Schritte sind in `main` (`c7bb327`, `5c017a6`, #136), Rest erledigt Format 1 |
| bestehend | #127, #128, #131 | #127 vor Teil 9, #128 vereinfacht sich in 14b, #131 in 14b und 15b |

---

## Offene Fragen

Die ersten Fragen muss der Projektinhaber **wirklich entscheiden**. Zu den übrigen steht ein begründeter Vorgabewert, der gilt, wenn nichts anderes gesagt wird.

### Entscheidung nötig

1. **Anhänge in Git (ADR 0019): entschieden am 2026-10-27, Ja.** Bilder, Markdown, PDF, Text und Logs bis 10 MiB liegen im Vault; größere Binärdateien liegen außerhalb (OneDrive/SharePoint) und werden im Event verlinkt (ADR 0019, Punkte 7 und 8, Teil 6b). **Offen bleiben GitHub-Tarif, Vertrag zur Auftragsverarbeitung und Region** (Voraussetzung für den ersten Vault mit echten Daten, nicht für das Bauen).

   Ursprüngliche Frage: GitHub ist als Standard-Hoster entschieden; offen war, welcher GitHub-Tarif, ob ein Vertrag zur Auftragsverarbeitung besteht und welche Region gilt. Das ändert die bestätigte Entscheidung aus ADR 0008/0009. Vorschlag des Reviews: **Ja, aber nur unter Bedingungen:** privater Vault, Tarif mit Branch Protection und Rulesets, Vertrag zur Auftragsverarbeitung und Region geklärt, Bereinigungsverfahren am Testrepository erprobt (Teil 16b). ~~Dieser PR wird bis zu Ihrer Entscheidung nicht gemergt.~~ Die Entscheidung zu den Anhängen ist gefallen; vor dem Merge sind die ADR-Nummern anzupassen (0011 ist inzwischen vergeben) und die Konflikte zu lösen. Geschützte Branches und erforderliche Reviews in privaten Repositories sind nach unserem Kenntnisstand bezahlt (zu prüfen, Teil 12a).
6. **Zweiten Schreibpfad über die Datenbank streichen (#123, ADR 0011): entschieden.** Die Datenbank bleibt für Login, Audit und etwaige Verschlüsselungs-Metadaten, die Wahrheit für Inhalte liegt im Vault. Offen bleibt nur der Zeitpunkt für Installationen ohne Datenordner: Exportweg und Migration (Teil 7) kommen zuerst.
9. **Live-Dokumentation bei Vorfällen (ADR 0015, Punkte 12 bis 14).** Vorschlag: Incident-Branch `incident/<id>` ohne Review (nur Status-Check), in der Oberfläche als „ohne Freigabe“ gekennzeichnet, danach ein Sammel-Pull-Request nach `main` mit Review. Schwächt das Vier-Augen-Prinzip für die Dauer eines Vorfalls bewusst. Ohne Ihre Zustimmung bleibt es bei einem Pull Request je Person (Teil 19 entfällt).
10. **Aufbewahrung und Löschung (ADR 0020).** Gibt es Fristen und Löschpflichten, die vor dem ersten echten Vault feststehen müssen? Vorschlag: ja, **Voraussetzung** vor dem ersten Vault mit echten Daten, zusammen mit der oder dem Datenschutzbeauftragten.

11. **Verschlüsselung der Einträge (ADR 0023).** Gibt es einen konkreten Bedarf (Topics mit eingeschränktem Leserkreis, Hoster nicht vertrauenswürdig genug, Vertragsanforderung)? Empfehlung: zunächst (a) Zugriffsschutz des Hosters plus (c) lokale Verschlüsselung (Festplatte), (b) erst bei Bedarf und dann auf Dateiebene mit Klartext-Metadaten und age.

### Vorgabe gilt, außer Sie widersprechen

2. **Signierte Commits:** nur Empfehlung. Bei Squash-Merge geht die Signatur verloren (ADR 0014).
3. **Einzelperson:** Pull Request mit 0 erforderlichen Freigaben und erforderlicher CI statt Direkt-Modus (ADR 0015, Punkt 16).
4. **Branch Protection:** Checkliste, importierbares Ruleset-JSON in der Vorlage und lesender Befehl `vault doctor` ohne gespeichertes Token (ADR 0015, Punkt 10).
5. **Pull Request:** Link statt Token. Die Linkvorlage steht je Vault in der Registry.
7. **Plattformen:** nur macOS und Linux.
8. **Mehrbenutzer:** schon im ersten Ausbau mehrere App-Benutzer, aber ein Arbeits-Branch je Klon und höchstens eine aktive Schreibsitzung (ADR 0015, Punkt 1). Ein gemeinsamer Teamserver bleibt später.

## Bereiche mit der größten Unsicherheit (zum gezielten Review)

1. ADR 0012: Heimat im Pfad, Verschieben beim Wechsel der Heimat (Rename-Erkennung bei kleinen Dateien).
2. ADR 0021, Punkte 5 und 25: Umgebungs-Whitelist gegen Credential Helper und Signierung. Nur die Matrix kann das klären.
3. ADR 0021, Punkt 1: genaue Mindestversion von Git und die genannten CVE-Hinweise sind nicht von mir verifiziert.
4. ADR 0021, Punkt 19: Der Vorabscan ist neuer Code im sicherheitskritischen Pfad. Fehler dort sind ein Umgehungsweg.
5. ADR 0015, Punkte 4 und 5: Zustandsautomat und Blob-ID-Status bei Squash und bei Änderungen nach dem Merge.
6. ADR 0015, Punkt 9: Tarifaussagen zu GitHub sind Annahmen und gegen die aktuellen Bedingungen zu prüfen (Teil 12a).
7. ADR 0020, Punkt 10: Bereinigung ist nicht erprobt (Teil 16b).
8. ADR 0019: Schwellen 200 MiB und 1 GiB sind geschätzt.
9. ADR 0014, Punkt 2: Aussage zur Ablehnung privater Adressen durch GitHub (Fehler GH007) ist aus dem Review übernommen, nicht selbst geprüft.
