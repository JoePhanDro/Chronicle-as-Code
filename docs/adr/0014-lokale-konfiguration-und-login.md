# ADR 0014: Lokale Konfiguration, Vault-Registry und Anmeldung

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0001 (Konten, Argon2id, Sitzungen), ADR 0007 (Lesesperre), ADR 0010 (Betriebsdatei), ADR 0012, ADR 0013, `chronicle/api.py` (`_bootstrap_admin`, `_announce_setup`), `chronicle/admin_cli.py`
- **Ändert:** ADR 0010 Punkt 1: Die Betriebsdatei liegt im lokalen Zustandsordner und nicht neben dem Index.

## Kontext

Mit Vaults (ADR 0012) liegen alle Inhalte in Git-Repositories. Lokal bleibt wenig übrig: ein Login und das Wissen, wo die Vaults auf der Festplatte liegen. Heute übergibt man `--db` und `--data` beim Start (oder `CHRONICLE_DB`, `CHRONICLE_DATA`). Die Betriebsdatei `chronicle.ops.db` liegt neben dem Index (ADR 0010).

## Entscheidung

1. **Ein Zustandsordner je Installation (`CHRONICLE_HOME`).** Standard `~/.chronicle`, im Container `/state`. Er gehört dem Benutzer allein (Modus `0700`, Dateien `0600`). Die Anwendung prüft die Rechte beim Start und weigert sich bei einem Ordner, den andere beschreiben können.
   ```
   $CHRONICLE_HOME/
     config.toml            Registry der Vaults (kein Geheimnis, aber Pfade)
     ops.db                 Konten, Sitzungen, Login-Sperren, Einstellungen, Audit-Log
     cache/<local-id>/      abgeleiteter Index (ADR 0018), jederzeit löschbar
   ```
2. **Die Registry nennt Vaults, sonst nichts.**
   ```toml
   [[vault]]
   local_id    = "c3a9…"          # von der Installation vergeben (Zufall), Schlüssel für Cache, Audit, URL
   clone_id    = "k7f2"           # Kurzkennung des Klons, steht im Namen des Arbeits-Branches (ADR 0016)
   name        = "Beispiel GmbH"  # Anzeigename, gilt in der Oberfläche
   path        = "/Users/anna/vaults/beispiel-gmbh"
   manifest_id = "6f1c0c7e-…"     # Etikett aus vault.yaml, wird beim Start verglichen
   origin      = "git@github.com:beispiel/vault.git"   # erwartete URL von origin (ADR 0022, Punkt 7)
   compare_url = "https://github.com/{org}/{repo}/compare/{base}...{head}?expand=1"
   # optional: workflow = "direct"              (ADR 0016, Punkt 16; Standard ist "review")
   # optional: allowed_hosts = ["github.com"]   (ADR 0022, Punkt 10)
   ```
   Beim Start prüft die Anwendung für jeden Eintrag, dass `path` ein Git-Repository ist, dessen Wurzel gleich `path` ist (wie `sync._is_repository_root`), dass `vault.yaml` existiert und die `manifest_id` übereinstimmt. Bei Abweichung wird der Vault nicht geöffnet. Das fängt vertauschte oder ersetzte Ordner ab, ist aber **kein Sicherheitsmerkmal**: Die Manifest-ID steht im Vault und ist vom Vault kontrollierbar (ADR 0013). Zwei Einträge mit gleicher Manifest-ID werden abgelehnt. Maßgeblich für Trennung ist die `local_id`, die nie aus dem Vault kommt.
   Vor jedem `fetch` und `push` vergleicht `git.py` `origin` aus der Konfiguration des Klons mit dem Registry-Eintrag und bricht bei Abweichung ab. Beim Eintragen eines vorhandenen Klons wird dessen `.git/config` gegen eine Allowlist geprüft (ADR 0022, Punkt 7).
3. **Keine Geheimnisse in der Konfiguration.** Weder Zugangsdaten für Git noch Tokens des Hosters. Git holt sie selbst (SSH-Agent, Credential Helper des Betriebssystems). Die Anwendung ruft nie `git credential` auf und liest keine Credential-Dateien.
4. **Befehle für die Verwaltung** (CLI, nicht in der Weboberfläche; die Oberfläche zeigt nur den Stand):
   - `chronicle init` legt den Zustandsordner an, erzeugt `ops.db` und fragt den ersten Admin ab (Bootstrap, siehe 6).
   - `chronicle vault add <pfad>` trägt einen vorhandenen Klon ein. `chronicle vault clone <url> [<pfad>]` klont und trägt ein (URL-Regeln in ADR 0022). `chronicle vault list`, `chronicle vault remove <id>` (löscht nur den Eintrag und den Cache, nie den Ordner).
   - `chronicle serve` ohne `--db` und `--data` liest die Registry. `--vault <id|name>` wählt den Vault. `--db` und `--data` bleiben eine Übergangszeit als veraltete Optionen.
5. **Was lokal im Login-Zustand liegt** (`ops.db`, unverändert zu ADR 0010, plus Erweiterungen aus ADR 0015): Benutzername, Argon2id-Hash (Bibliotheksvorgaben von `argon2-cffi`, Mindestlänge 12), Rolle, `must_change_password`, SHA-256 der Sitzungstokens, CSRF-Token der Sitzung, Login-Fehlversuche, Einstellungen (Hash des Sperrpassworts, ADR 0007), Audit-Log. Das Audit-Log bekommt eine Spalte `vault_id`.
   **Abgrenzung (Entscheidung des Projektinhabers: die Datenbank bleibt für Login, Audit und etwaige Verschlüsselung):**

   | Ort | Inhalt | Quelle der Wahrheit? | Wiederherstellbar aus |
   |---|---|---|---|
   | Vault (Git) | Topics, Events, Anhänge, Manifest | ja, für alle Inhalte | Git-Historie |
   | `ops.db` | Konten, Sitzungen, Sperren, Einstellungen, Audit-Log, Schlüssel-Metadaten (Kennung, Empfänger-Fingerabdruck, Rotationsdatum; **nie** der private Schlüssel) | ja, für Betrieb | nur Backup |
   | `cache/<local-id>/index.db` | Index der Inhalte | nein | Vault-Dateien |
   | `config.toml` | Vault-Registry | ja, lokal | Backup oder neu eintragen |

   **Audit-Log lokal oder Git-Historie als Audit-Quelle?** Beide dienen verschiedenen Fragen:
   - **Git-Historie plus Review-Verlauf des Hosters** beantwortet „wer hat welchen Inhalt wann geändert und wer hat es freigegeben“. Sie ist über den Hoster gegen nachträgliches Ändern geschützt (Force-Push verboten, ADR 0016) und von mehreren Personen einsehbar. Das ist die **maßgebliche Quelle für Inhalte**.
   - **Das lokale Audit-Log** beantwortet „wer hat sich an dieser Installation angemeldet, wer hat Konten geändert, welche Fehlversuche gab es, was hat die App gelesen oder exportiert (Berichte)“. Das steht nicht in Git. Es ist **ergänzend** und nicht manipulationssicher: Wer die Datei besitzt, kann sie ändern.
   - **Option „Manipulationserkennung“ (empfohlen als eigener Teil, Plan Teil 8c):** Jeder Audit-Eintrag trägt `prev_hash` und `entry_hash = SHA-256(prev_hash ‖ Inhalt des Eintrags)`. Das macht Löschen oder Ändern mittendrin sichtbar, nicht verhinderbar. Der aktuelle Kopfwert kann als Trailer `Chronicle-Audit-Head: <hash>` in die Commits der App aufgenommen werden, wodurch die Kette an die Git-Historie (und damit an den Hoster) verankert wird: Eine spätere Kürzung des Logs fällt gegen die Commits auf. Das ist eine Option, kein Muss, und setzt nur SHA-256 aus der Standardbibliothek ein.
   - Inhalte des Audit-Logs sind ohne Ereignisinhalte (wie heute), damit es keine zweite Kopie sensibler Daten gibt.
6. **Bootstrap.** Es bleiben die drei bestehenden Wege, alle nur solange noch kein Konto existiert:
   1. `chronicle init` fragt Benutzername und Passwort auf dem Terminal ab (verdeckt). Das ist der empfohlene Weg am Arbeitsplatz.
   2. Umgebungsvariablen `CHRONICLE_ADMIN_USER` und `CHRONICLE_ADMIN_PASSWORD` (Docker). Der Admin muss das Passwort beim ersten Login ändern.
   3. Einmal-Code im Log für `/setup` (ADR 0001, `_announce_setup`), mit Fehlversuch-Drosselung.

   Ohne Konto und ohne Registry-Eintrag zeigt der Server nichts außer `/setup` und einem Hinweis. Mit Konto, aber ohne Vault zeigt er Admins eine Seite „Kein Vault verbunden“ mit den CLI-Befehlen.
7. **Rollen sind je Installation, nicht je Vault** (`admin`, `analyst`, `viewer`). Sie steuern, was die Oberfläche anbietet. Sie sind keine Zugriffsgrenze auf die Daten (siehe Konsequenzen und ADR 0015).

## Konsequenzen

- Ein Backup des Zustandsordners (ohne `cache/`) sichert die Konten und die Registry. `chronicle/backup.py` kopiert heute nur Index und `ops.db` und wird um `config.toml` erweitert; Wiederherstellung und Test (Plan Teil 8). Die Inhalte sichert Git.
- **Container-Betrieb.** Das Image (`python:3.12-slim`, ohne `git` und `ssh`, `read_only`, `cap_drop: ALL`) schreibt bereits in den eingebundenen Datenordner (ADR 0011), hat aber **kein Git**. Sync im Container verlangt Git, SSH, den Zustandsordner als Volume (`/state`) und Zugangsdaten für den Hoster (Deploy Key als Datei, nicht im Image). Das ist ein eigener Teil im Plan (Teil 8b). Bis dahin gilt: der Container bearbeitet einen bereits geklonten Vault, Commits und Abgleich macht, wer den Ordner verwaltet.
- Die Betriebsdatei ist nicht mehr im Daten- oder Vault-Ordner und kann nie versehentlich committet werden.
- Ein Vault-Verzeichnis ohne `vault.yaml` wird abgelehnt statt still geöffnet.
- **Ehrlicher Schutzumfang:** Wer Lesezugriff auf das Vault-Verzeichnis hat, liest alle Inhalte, unabhängig vom Login der Anwendung. Der Login schützt die Oberfläche (Browser, API, Schreibvorgänge über die App), nicht die Dateien. Vertraulichkeit der Daten kommt aus der Festplattenverschlüsselung, den Benutzerrechten des Betriebssystems und dem Zugriff auf das Remote-Repository.
- Der Betrieb als Dienst für mehrere Personen mit gemeinsamem Konto-Bestand bleibt möglich (ein Zustandsordner, ein Klon), schwächt aber die Autorenschaft (ADR 0015).
- Windows ist nicht Ziel: `store.py` nutzt `fcntl` für die Sperre, Dateirechte `0700`/`0600` greifen dort nicht.

## Offene Punkte

- Ob die Registry zusätzlich den Namen des Standard-Branches und den Remote-Namen speichert. Vorläufig liest die Anwendung beides aus dem Repository.
- Plattform-übliche Ordner (macOS `~/Library/Application Support`, Linux XDG) statt `~/.chronicle`. Ein Ordner ist einfacher zu sichern und zu testen. Die Wahl ist leicht zu ändern, solange `CHRONICLE_HOME` gilt.
