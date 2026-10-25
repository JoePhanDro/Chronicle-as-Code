# ADR 0013: Lokale Konfiguration, Vault-Registry und Anmeldung

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0001 (Konten, Argon2id, Sitzungen), ADR 0007 (Lesesperre), ADR 0010 (Betriebsdatei), ADR 0011, ADR 0012, `chronicle/api.py` (`_bootstrap_admin`, `_announce_setup`), `chronicle/admin_cli.py`
- **Ändert:** ADR 0010 Punkt 1: Die Betriebsdatei liegt im lokalen Zustandsordner und nicht neben dem Index.

## Kontext

Mit Vaults (ADR 0011) liegen alle Inhalte in Git-Repositories. Lokal bleibt wenig übrig: ein Login und das Wissen, wo die Vaults auf der Festplatte liegen. Heute übergibt man `--db` und `--data` beim Start (oder `CHRONICLE_DB`, `CHRONICLE_DATA`). Die Betriebsdatei `chronicle.ops.db` liegt neben dem Index (ADR 0010).

## Entscheidung

1. **Ein Zustandsordner je Installation (`CHRONICLE_HOME`).** Standard `~/.chronicle`, im Container `/state`. Er gehört dem Benutzer allein (Modus `0700`, Dateien `0600`). Die Anwendung prüft die Rechte beim Start und weigert sich bei einem Ordner, den andere beschreiben können.
   ```
   $CHRONICLE_HOME/
     config.toml            Registry der Vaults (kein Geheimnis, aber Pfade)
     ops.db                 Konten, Sitzungen, Login-Sperren, Einstellungen, Audit-Log
     cache/<vault-id>/      abgeleiteter Index (ADR 0017), jederzeit löschbar
   ```
2. **Die Registry nennt Vaults, sonst nichts.**
   ```toml
   [[vault]]
   id   = "6f1c0c7e-…"      # aus vault.yaml, wird bei jedem Start verglichen
   name = "Beispiel GmbH"
   path = "/Users/anna/vaults/beispiel-gmbh"
   # optional: workflow = "direct"        (ADR 0015, Punkt 9; Standard ist "review")
   # optional: allowed_hosts = ["github.com"]   (ADR 0021, Punkt 6)
   ```
   Beim Start prüft die Anwendung für jeden Eintrag, dass `path` ein Git-Repository ist, dessen Wurzel gleich `path` ist (wie `sync._is_repository_root`), dass `vault.yaml` existiert und die `id` übereinstimmt. Bei Abweichung wird der Vault nicht geöffnet. Das verhindert, dass ein vertauschter oder ersetzter Ordner unter dem Namen eines anderen Einsatzortes erscheint.
3. **Keine Geheimnisse in der Konfiguration.** Weder Zugangsdaten für Git noch Tokens des Hosters. Git holt sie selbst (SSH-Agent, Credential Helper des Betriebssystems). Die Anwendung ruft nie `git credential` auf und liest keine Credential-Dateien.
4. **Befehle für die Verwaltung** (CLI, nicht in der Weboberfläche; die Oberfläche zeigt nur den Stand):
   - `chronicle init` legt den Zustandsordner an, erzeugt `ops.db` und fragt den ersten Admin ab (Bootstrap, siehe 6).
   - `chronicle vault add <pfad>` trägt einen vorhandenen Klon ein. `chronicle vault clone <url> [<pfad>]` klont und trägt ein (URL-Regeln in ADR 0021). `chronicle vault list`, `chronicle vault remove <id>` (löscht nur den Eintrag und den Cache, nie den Ordner).
   - `chronicle serve` ohne `--db` und `--data` liest die Registry. `--vault <id|name>` wählt den Vault. `--db` und `--data` bleiben eine Übergangszeit als veraltete Optionen.
5. **Was lokal im Login-Zustand liegt** (`ops.db`, unverändert zu ADR 0010, plus Erweiterungen aus ADR 0014): Benutzername, Argon2id-Hash (Bibliotheksvorgaben von `argon2-cffi`, Mindestlänge 12), Rolle, `must_change_password`, SHA-256 der Sitzungstokens, CSRF-Token der Sitzung, Login-Fehlversuche, Einstellungen (Hash des Sperrpassworts, ADR 0007), Audit-Log. Das Audit-Log bekommt eine Spalte `vault_id`.
6. **Bootstrap.** Es bleiben die drei bestehenden Wege, alle nur solange noch kein Konto existiert:
   1. `chronicle init` fragt Benutzername und Passwort auf dem Terminal ab (verdeckt). Das ist der empfohlene Weg am Arbeitsplatz.
   2. Umgebungsvariablen `CHRONICLE_ADMIN_USER` und `CHRONICLE_ADMIN_PASSWORD` (Docker). Der Admin muss das Passwort beim ersten Login ändern.
   3. Einmal-Code im Log für `/setup` (ADR 0001, `_announce_setup`), mit Fehlversuch-Drosselung.

   Ohne Konto und ohne Registry-Eintrag zeigt der Server nichts außer `/setup` und einem Hinweis. Mit Konto, aber ohne Vault zeigt er Admins eine Seite „Kein Vault verbunden“ mit den CLI-Befehlen.
7. **Rollen sind je Installation, nicht je Vault** (`admin`, `analyst`, `viewer`). Sie steuern, was die Oberfläche anbietet. Sie sind keine Zugriffsgrenze auf die Daten (siehe Konsequenzen und ADR 0014).

## Konsequenzen

- Ein Backup des Zustandsordners (ohne `cache/`) sichert die Konten. Die Inhalte sichert Git.
- Die Betriebsdatei ist nicht mehr im Daten- oder Vault-Ordner und kann nie versehentlich committet werden.
- Ein Vault-Verzeichnis ohne `vault.yaml` wird abgelehnt statt still geöffnet.
- **Ehrlicher Schutzumfang:** Wer Lesezugriff auf das Vault-Verzeichnis hat, liest alle Inhalte, unabhängig vom Login der Anwendung. Der Login schützt die Oberfläche (Browser, API, Schreibvorgänge über die App), nicht die Dateien. Vertraulichkeit der Daten kommt aus der Festplattenverschlüsselung, den Benutzerrechten des Betriebssystems und dem Zugriff auf das Remote-Repository.
- Der Betrieb als Dienst für mehrere Personen mit gemeinsamem Konto-Bestand bleibt möglich (ein Zustandsordner, ein Klon), schwächt aber die Autorenschaft (ADR 0014).
- Windows ist nicht Ziel: `store.py` nutzt `fcntl` für die Sperre, Dateirechte `0700`/`0600` greifen dort nicht.

## Offene Punkte

- Ob die Registry zusätzlich den Namen des Standard-Branches und den Remote-Namen speichert. Vorläufig liest die Anwendung beides aus dem Repository.
- Plattform-übliche Ordner (macOS `~/Library/Application Support`, Linux XDG) statt `~/.chronicle`. Ein Ordner ist einfacher zu sichern und zu testen. Die Wahl ist leicht zu ändern, solange `CHRONICLE_HOME` gilt.
