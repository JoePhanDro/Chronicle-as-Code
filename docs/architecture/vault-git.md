# Architektur: Chronicle mit Vault-Repositories in Git

- **Status:** Entwurf, nach Review (Sicherheit und Architektur) überarbeitet, noch nicht umgesetzt
- **Stand:** 2026-10-25, Code auf `main` bei Commit `d68216f`
- **Entscheidungen:** ADR 0012 bis 0024 (GitHub ist Standard-Hoster, die Datenbank bleibt für Login, Audit und Schlüssel-Metadaten, Verschlüsselung der Einträge ist nicht im Scope, ADR 0024 hält die Optionen für später fest) in [`docs/adr/`](../adr/). Umsetzungsplan: [`vault-git-plan.md`](vault-git-plan.md)

## 1. Ziel in einem Absatz

„As code“ heißt: in Git. Die **Programmlogik** liegt im Code-Repository. Die **Inhalte** (Topics, Events, Anhänge) eines Einsatzortes liegen in einem eigenen Git-Repository, dem **Vault**. Mehrere Personen arbeiten am Vault, prüfen ihre Änderungen gegenseitig per Pull Request und geben sie frei. Die Daten liegen als Dateien auf der Festplatte. Lokal bleiben nur ein Login (mit Bootstrap beim ersten Start) und die Verknüpfung zu den Vaults (Pfade), damit die Anwendung beim Start weiß, wo die Dateien liegen.

## 2. Ausgangslage (Ist-Zustand)

Vieles ist schon da. Das Konzept baut darauf auf und ändert wenig am Fundament:

| Baustein | Stand auf `main` | Datei |
|---|---|---|
| Dateien als Wahrheit, Index in SQLite | umgesetzt (ADR 0008, 0010, 0011) | `store.py`, `index.py`, `queries.py`, `db.py` |
| Datenordner als eigenes Git-Repository, Vorschau und Abgleich | umgesetzt | `sync.py` |
| Merge-Treiber auf Feldebene | umgesetzt, nur lokal wirksam | `merge.py` |
| Prüfung ohne Server, `check`, `plan`, `apply`, `build` | umgesetzt | `check.py`, `cli.py` |
| CI-Prüfung der Topic-Dateien | umgesetzt (`topic-files.yml`) | `.github/workflows/` |
| Konten, Sitzungen, Audit in eigener Datei | umgesetzt (ADR 0010) | `accounts.py`, `ops.db` |
| Zweiter Schreibpfad über die Datenbank | **entfernt** (ADR 0011, #123); der Index speichert keine Anhangs-Bytes und keine Grabsteine | `events.py`, `topics.py`, `evidence_store.py`, `folder_writes.py` |
| Anhänge in Git | bisher bewusst nicht (ADR 0008, 0009) | `evidence.py` |

Was fehlt: das Konzept „Vault“ als Einheit (Manifest, Registry), ein Layout, das Zusammenarbeit verträgt, ein Freigabe-Weg über Pull Requests, ein gehärteter Git-Aufruf, ein Umgang mit Identität, sensiblen Daten und Migration.

## 3. Komponenten

```mermaid
flowchart LR
  subgraph Host["Hoster (GitHub)"]
    VR["Vault-Repository<br/>(je Einsatzort)"]
    PR["Pull Requests<br/>Reviews, CODEOWNERS,<br/>Branch Protection"]
    CI["Vault-CI<br/>chronicle check"]
    VR --- PR --- CI
  end

  subgraph Code["Code-Repository"]
    APP["Anwendung + CLI<br/>(chronicle)"]
    WF["wiederverwendbarer<br/>Prüf-Workflow"]
  end

  subgraph WS["Arbeitsplatz einer Person"]
    subgraph HOME["CHRONICLE_HOME (0700)"]
      CFG["config.toml<br/>Vault-Registry"]
      OPS["ops.db<br/>Konten, Sitzungen, Audit"]
      IDX["cache/&lt;local-id&gt;/index.db<br/>abgeleitet"]
    end
    CLONE["Klon des Vaults<br/>(Dateien auf Platte)"]
    SRV["Server (FastAPI)<br/>Oberfläche + API"]
    GIT["git.py<br/>einziger Git-Aufruf"]
    BR["Browser"]
  end

  BR -->|"HTTP, Login, CSRF"| SRV
  SRV --> CFG
  SRV --> OPS
  SRV --> IDX
  SRV -->|"liest/schreibt Dateien"| CLONE
  SRV --> GIT
  GIT -->|"fetch / push (https, ssh)"| VR
  GIT --> CLONE
  APP -.->|"gleicher Code"| SRV
  WF -.->|"per SHA angeheftet"| CI
  CI -->|"Status: erforderlich"| PR
```

Zuständigkeiten:

- **Vault-Repository.** Nur Inhalte und das Manifest. Keine Konten, keine Geheimnisse, keine Datenbanken (ADR 0012, 0013, 0021).
- **Anwendung und CLI.** `chronicle` enthält Schema, Prüfung, Index, Rendering und den Server. Der Server enthält keine eigene Fachlogik (ADR 0009).
- **`git.py`.** Der einzige Ort, an dem `git` gestartet wird (ADR 0022).
- **`CHRONICLE_HOME`.** Alles Lokale, das nicht Inhalt ist: Registry, Konten, abgeleiteter Index (ADR 0014, 0018).
- **Hoster.** Rechte, Freigabe, Prüfungen. Er ist die Stelle, die die Freigabe erzwingt (ADR 0015, 0016).

## 4. Datenfluss

### 4.1 Ein Event anlegen und freigeben

```mermaid
sequenceDiagram
  actor A as Anna (Analystin)
  participant S as Server (lokal)
  participant F as Klon (Dateien)
  participant G as git.py
  participant H as Hoster
  actor B as Ben (Prüfer)

  A->>S: Event speichern (Formular, CSRF)
  S->>S: Eingabe prüfen (event_input, Schema)
  S->>F: Datei topics/T/events/K.yaml atomar schreiben
  S->>G: commit (Autor Anna, Trailer Chronicle-User)
  S->>S: Index für das Event aktualisieren
  S-->>A: gespeichert, Merkmal "wartet auf Freigabe"
  A->>S: Zur Freigabe einreichen
  S->>S: check (alle Regeln)
  S->>G: fetch origin, origin/main einführen (Merge-Treiber)
  S->>G: push chronicle/anna/... (nur Arbeits-Branch)
  S-->>A: Link zum Pull Request
  A->>H: Pull Request öffnen
  H->>H: Vault-CI: chronicle check --secrets
  B->>H: Review, Freigabe (CODEOWNERS)
  H->>H: Merge auf main
  A->>S: Aktualisieren
  S->>G: fetch, main ff-only, Merkmal "freigegeben"
```

### 4.2 Aktualisieren und Index

`Aktualisieren` holt `origin`, schiebt den lokalen Standard-Branch per `--ff-only` vor, führt ihn in den Arbeits-Branch ein und aktualisiert den Index über `git diff --name-status <indizierter Commit> HEAD` (ADR 0018). Ist der Index ungültig, wird er komplett aus den Dateien gebaut.

### 4.3 Lesen

Die Oberfläche liest ausschließlich aus dem Index. Anhänge liest der Server aus der Datei im Klon und vergleicht sie mit der Prüfsumme (ADR 0010, 0020).

## 5. Startsequenz

```mermaid
flowchart TD
  S0([Start: chronicle serve]) --> S1{CHRONICLE_HOME<br/>vorhanden, Rechte 0700?}
  S1 -- nein --> S1a[Fehler: erst chronicle init<br/>oder Rechte korrigieren]
  S1 -- ja --> S2{Konto in ops.db?}
  S2 -- nein --> S2a["Bootstrap: Env-Variablen<br/>oder /setup mit Einmal-Code im Log<br/>(chronicle init fragt am Terminal)"]
  S2a --> S3
  S2 -- ja --> S3[Registry lesen]
  S3 --> S4{Vault eingetragen?}
  S4 -- nein --> S4a["Seite: Kein Vault verbunden<br/>(CLI: vault add / vault clone)"]
  S4 -- ja --> S5["je Vault prüfen: Git-Wurzel gleich Pfad,<br/>vault.yaml vorhanden, Manifest-Etikett stimmt,<br/>origin wie in der Registry, .git/config nur Allowlist, format bekannt"]
  S5 -- Abweichung --> S5a[Vault nicht öffnen, Grund anzeigen]
  S5 -- ok --> S6[Index prüfen: gültig? sonst bauen]
  S6 --> S7[Anmeldung /login]
  S7 --> S8[Vault öffnen: Standard ein Vault,<br/>Umschalter bei mehreren zurückgestellt]
```

Eine Anmeldung erlaubt keinen Zugriff auf die Dateien. Sie erlaubt nur den Zugang zur Oberfläche und zur API. Wer die Dateien lesen kann, liest sie ohnehin (ADR 0014).

## 6. Git-Integration

Aufruf des Programms `git` in einem einzigen Modul (ADR 0022): Argumentliste statt Shell, feste Umgebung, `-c`-Schalter gegen Hooks, externe Protokolle und Submodule, `fsckObjects`, Zeitlimits, URL- und Branch-Prüfung, bereinigte Fehlermeldungen, Symlinks im Klon aus (`core.symlinks=false`). Die Anwendung ändert die Git-Konfiguration des Benutzers nicht und speichert keine Zugangsdaten. Authentifizierung gegenüber dem Hoster übernimmt Git (SSH-Agent, Credential Helper). Der Hoster-Zugang der App beschränkt sich auf `fetch` und `push` über Git, keine API, kein Token.

Verworfen: libgit2 und Dulwich (kein Merge-Treiber, eingeschränkte Authentifizierung und Signaturen), GitPython (Wrapper um dasselbe Programm mit mehr Angriffsfläche).

## 7. Vertrauensgrenzen

```mermaid
flowchart LR
  U["Browser / Nutzer"] -- "TB1" --> SRV["Server-Prozess"]
  SRV -- "TB2" --> VF["Vault-Dateien<br/>(Eingabe von Dritten)"]
  SRV -- "TB3" --> GIT["git-Prozess"]
  GIT -- "TB4" --> REM["Remote / Hoster"]
  SRV -- "TB5" --> ST["CHRONICLE_HOME<br/>(Konten, Index)"]
  REM -- "TB6" --> CI["Vault-CI<br/>(Fremdinhalte, keine Secrets)"]
```

| Grenze | Was überschreitet sie | Schutz |
|---|---|---|
| TB1 Browser zu Server | HTTP, Formulare, Uploads | Login, Sitzungscookie (`HttpOnly`, `SameSite=Strict`), CSRF, Rollen, Drosselung (ADR 0001); neu: Vault-Kennung in jeder Anfrage (ADR 0019) |
| TB2 Server zu Vault-Dateien | YAML, Markdown, Anhänge von **anderen Personen**; auch das Verzeichnis selbst (Python-Suchpfad) | strenge Prüfung vor dem Lesen: Allowlist der Wurzel, keine Links, Größen, Ereignisstrom-Scan (Tiefe, Alias, Duplikate), Bidi-Zeichen, Pfadregeln (ADR 0022); bereinigtes Markdown; Anhänge mit Sandbox-CSP |
| TB3 Server zu `git` | Argumente, Umgebung, Ausgabe | ein Modul, Muster für Branch und Key, `--`, feste `-c`, Zeitlimits (ADR 0022) |
| TB4 `git` zu Remote | Objekte, Zugangsdaten | https/ssh nur, `fsckObjects`, keine Zugangsdaten in URLs, Zugangsdaten bei Git/SSH-Agent, nicht in der App |
| TB5 Server zu Zustandsordner | Hashes, Sitzungen, Registry | Rechte `0700`/`0600`, Registry mit Vault-`id`, kein Geheimnis im Vault |
| TB6 Remote zu CI | Pull-Request-Inhalte (auch aus Forks) | `pull_request`, nur Lesen, keine Secrets, SHA-angeheftete Aktionen und Workflows (ADR 0022) |

## 8. Bedrohungsmodell (STRIDE-light)

| # | Bedrohung | Kategorie | Maßnahme | Rest |
|---|---|---|---|---|
| T1 | Eine Person ändert Dateien lokal und umgeht damit die App-Rollen | Elevation | Rollen sind nur Oberfläche. Entscheidend ist der Hoster: Schutz des Standard-Branches, Pull Request, Freigabe (ADR 0015, 0016) | Wer Schreibrechte auf dem Hoster hat, kann Vorschläge machen. Prüfer müssen prüfen |
| T2 | Commit unter fremdem Namen | Spoofing | Pull-Request-Autor ist der Hoster-Account. Signierte Commits optional bis Pflicht (ADR 0015) | Ohne Signatur sind Name und Adresse Eigenangaben |
| T3 | Kompromittiertes Konto schleust Inhalte ein (bösartiges YAML, Link auf `~/.ssh`, riesige Datei) | Tampering, Info Disclosure, DoS | Prüfung vor dem Lesen (ADR 0022, Punkte 15 bis 24), Vault-CI mit denselben Regeln | Neue Angriffsarten in YAML-Bibliothek oder Markdown-Bereinigung |
| T4 | Täuschung im Review: doppelter Schlüssel, Bidi-Zeichen, Unicode-Gleichaussehende in Keys | Spoofing der Prüfung | Strikter Lader, Zeichenregeln, Keys nur ASCII-Muster (ADR 0022, 0013) | Gleich aussehende ASCII-Keys (`l` und `1`) |
| T5 | Befehlseinschleusung über URL, Branch-Name oder Pfad beim Aufruf von Git | Tampering | Argumentliste, `--`, feste Muster, `protocol.ext.allow=never`, URL-Regeln (ADR 0022) | Fehler in Git selbst: Mindestversion, Updates |
| T6 | Bösartiger Klon (Hooks, Submodule, Filter, Attribute) | Elevation | Hooks aus Vorlage nicht geklont, `core.hooksPath` leer, Submodule aus, Treiber nur aus lokaler Konfiguration (ADR 0022, 17) | Git-Schwachstellen im Checkout (Mindestversion, `fsckObjects`) |
| T7 | Geheimnis oder personenbezogene Daten in der Historie | Info Disclosure | Muster-Prüfung, Hoster-Scanning, privater Vault, Verfahren zur Bereinigung (ADR 0021) | Kopien bleiben (Forks, Klone, PR-Referenzen) |
| T8 | Verwechslung von Mandanten (Schreiben in den falschen Vault) | Tampering, Info Disclosure | Genau ein aktiver Vault, `id` in Registry und in jeder Anfrage, Tests (ADR 0019) | Fehler im Code, der Pfade zusammensetzt |
| T9 | Ersetzter Vault-Ordner (anderer Inhalt unter bekanntem Pfad) | Spoofing | Vergleich der `id` aus `vault.yaml` beim Start (ADR 0014) | Wer den Ordner kontrolliert, kontrolliert die Daten (Betriebssystemrechte) |
| T10 | Diebstahl von `ops.db` (Hashes, Sitzungen) | Info Disclosure | Rechte `0700`/`0600`, Argon2id, nur Hash der Sitzungstokens (ADR 0001, 0014) | Offline-Angriff auf schwache Passwörter (Mindestlänge 12) |
| T11 | Laptop-Verlust: alle Vaults im Klartext auf Platte | Info Disclosure | Festplattenverschlüsselung ist **Voraussetzung** (Betriebsregel, nicht Teil der App) | Betrifft den Betrieb |
| T12 | Anhang mit aktiven Inhalten (SVG, HTML, PDF mit Skript) | Elevation (XSS) | Typ- und Signaturprüfung, SVG/HTML verboten, `sandbox`-CSP, `nosniff`, Download für Nicht-Bilder (ADR 0004) | PDF-Reader des Nutzers |
| T13 | Pull Request aus Fork führt CI mit Secrets aus | Elevation | `pull_request`, keine Secrets, `contents: read`, SHA-Pins (ADR 0022, 18) | Fehlkonfiguration im Vault |
| T14 | Streit über Verlauf: jemand löscht oder verändert Historie | Repudiation | Force-Push verboten, Administratoren eingeschlossen; Review-Verlauf beim Hoster; Audit der App als Ergänzung (ADR 0015, 0016) | Ein Hoster-Administrator kann Regeln ändern |
| T15 | Race: zwei Schreiber im selben Ordner (App und Editor) | Tampering | Dateisperre, Versionsvergleich (SHA-256 der Datei) vor dem Schreiben, atomares Schreiben (ADR 0008; `store.py`) | Externe Editoren ohne Sperre |
| T16 | Abfluss über Fehlermeldungen oder Logs (Tokens in URLs, Dateiinhalt in Pydantic-Meldungen) | Info Disclosure | Keine Zugangsdaten in URLs, `hide_input_in_errors`, gekürzte Meldungen (ADR 0022, 10, 24) | — |
| T17 | **Code aus dem Vault:** `yaml.py` oder `chronicle/` in der Vault-Wurzel wird vom Merge-Treiber (`python -m`, Vault als cwd) importiert. **Belegt auf `main`** | Elevation | Treiber-Start mit `python -P -c <Start> <Paketordner>` (Paketordner aus `__file__` der App), App nie mit Vault als cwd, Blocklist (umgesetzt, PR #138) und später Allowlist der Vault-Wurzel in `check` und CI (ADR 0022, 2, 17, 18); Plan Teil 0a | Weitere Importwege über Umgebungsvariablen (`PYTHONPATH` bleibt bewusst wirksam, damit `pip --user` funktioniert): Betriebsregel |
| T18 | **Absturz durch YAML:** tief verschachtelte `[` (ca. 120 KB) lassen den C-Lader mit SIGSEGV abstürzen. **Belegt auf `main`** | DoS | Ereignisstrom-Vorabscan (Tiefe, Anker, Alias, Tags, Duplikate, Knotenzahl), reiner Python-Lader für Fremdinhalt (ADR 0022, 19) | Neue Parser-Fehler |
| T19 | **Symlink beim Schreiben:** `.ev-1.yaml.tmp` oder `.chronicle.lock` als Link, die App schreibt durch den Link. **Belegt auf `main`** | Tampering | `O_NOFOLLOW`/`O_EXCL`/`mkstemp`, `lstat`, Modusprüfung `ls-tree -r` bei jedem `check` und Merge-Ergebnis (ADR 0022, 15, 16) | — |
| T20 | **`.git/config` eines vorhandenen Klons** (`core.sshCommand` u. ä.) wird ausgeführt. **Belegt** | Elevation | Allowlist beim `vault add`, erwartete `origin`-URL in der Registry, `-c core.sshCommand=ssh` (ADR 0022, 6, 7) | Nach `vault add` geänderte Konfiguration: Prüfung vor jedem Fetch/Push |
| T21 | **Ambient Authority:** der SSH-Agent des Benutzers bietet alle Schlüssel an, ein Fetch/Push kann mehr als nötig | Elevation | Nur benötigte Refs, Empfehlung eigener Deploy Key je Vault mit `IdentitiesOnly=yes` (ADR 0022, 8) | Betrieb |
| T22 | Bericht oder Export enthält ungeprüfte Events als „geprüft“ | Spoofing | Berichte standardmäßig aus `origin/<standard>`, Kennzeichnung (ADR 0016, 15) | — |
| T23 | DNS-Rebinding gegen den lokalen Server auf `127.0.0.1` | Elevation | Host-Prüfung, `Origin`-Prüfung zusätzlich zu CSRF (ADR 0022, 27) | — |
| T24 | Manifest-Etikett (`id`) wird kopiert oder behauptet | Spoofing | Lokale ID als Schlüssel, Duplikate abgelehnt (ADR 0013, 0014) | — |
| T25 | `.gitattributes`/Zeilenenden verändern Anhänge, Prüfsumme bricht oder täuscht | Tampering | Feste Konfiguration und Attribute, Allowlist für `.gitattributes` (ADR 0022, 22) | — |

Nicht Teil dieses Modells: Angriffe auf das Betriebssystem des Arbeitsplatzes, Schwachstellen in Browser und Hoster, Insider mit Administratorrechten auf dem Hoster.

## 9. Fehlerfälle

| Fall | Verhalten |
|---|---|
| Git ist nicht installiert oder zu alt | Server startet, zeigt Hinweis, Schreiben und Abgleich gesperrt, Lesen aus dem Index möglich |
| Remote nicht erreichbar | `fetch`/`push` melden Zeitüberschreitung, lokal bleibt alles erhalten, Einreichen lässt sich später wiederholen |
| `push` abgewiesen (Branch Protection, keine Rechte) | Meldung des Hosters (bereinigt) wird gezeigt, Arbeits-Branch bleibt lokal |
| Konflikt beim Einführen von `origin/main` | Merge wird abgebrochen, Dateien werden genannt, Auflösung in der Oberfläche (Plan Teil 15b) oder im Terminal |
| Merge-Ergebnis besteht `check` nicht | Zurücksetzen auf den Stand vor dem Merge, Probleme werden angezeigt |
| Vault-Ordner fehlt, `id` stimmt nicht, `format` unbekannt | Vault wird nicht geöffnet, andere Vaults bleiben nutzbar |
| Index fehlt oder ist beschädigt | Neuaufbau aus den Dateien, kein Datenverlust |
| Datei wurde außerhalb der App geändert | Versionskonflikt beim Speichern (409), Hinweis auf Aktualisieren |
| Zwei Instanzen auf demselben Klon | Dateisperre; wer sie nicht bekommt, bekommt einen Fehler statt Datenverlust |
| Anhang fehlt oder Prüfsumme stimmt nicht | Anzeige mit Warnung, Lieferung verweigert (Konflikt), `check` schlägt fehl |
| Angriffsmuster in Inhalten (Link, Alias, Bidi) | Datei wird abgelehnt, Vault bleibt mit dem letzten gültigen Index lesbar, Fehler mit Dateiname |
| Pull Request wurde auf dem Hoster verworfen | Arbeits-Branch bleibt lokal. Die Änderungen sind weiter sichtbar („wartet auf Freigabe“), bis sie über „Arbeitsstand verwerfen“ zurückgenommen werden (Zustandsautomat in ADR 0016, Punkt 5) |
| Remote-Arbeits-Branch ist weitergewandert | Remote-Branch wird eingeführt (Merge, nie `--force`), dann erneut gepusht |
| Squash-Merge auf dem Hoster | Status „freigegeben“ über Blob-ID je Pfad, „abgeschlossen“ über Inhaltsgleichheit, nicht über Commit-Verwandtschaft |
| `origin` in der Klon-Konfiguration weicht von der Registry ab | Fetch und Push werden verweigert |
| Git zu alt oder fehlt | siehe oben; Version laut ADR 0022, Punkt 1 |

## 10. Bewusste Grenzen

- **Kein Zugriffsschutz auf Dateiebene durch die App.** Vertraulichkeit entsteht durch Hoster-Rechte, Festplattenverschlüsselung und Betriebssystem (ADR 0014).
- **Kein Server-Modus für Teams im ersten Ausbau** (ADR 0015). Jede Person arbeitet mit eigenem Klon.
- **Keine Pull-Request-Anlage über die API** im ersten Ausbau (ADR 0016). Link statt Token.
- **Kein Windows.** `fcntl`-Sperre, Dateirechte (ADR 0014).
- **Keine Verweise zwischen Vaults**, und mehrere Vaults in einer Oberfläche sind zurückgestellt (ADR 0019).
- **Container.** Das heutige Image (`python:3.12-slim`, ohne `git`/`ssh`, `read_only`, `cap_drop: ALL`) schreibt in den eingebundenen Datenordner (ADR 0011), hat aber kein Git. Der Stack läuft hinter einem TLS-Proxy (`docs/betrieb-tls.md`). Git, SSH und Sync im Container sind ein eigener Plan-Teil (ADR 0014, Teil 8b).
- **Vertraulichkeit pro Vault, nicht pro Topic.** `also_in` umgeht Topic-CODEOWNERS (ADR 0013).
- **„Freigegeben“ heißt „im Standard-Branch“.** Ein erzwungenes Vier-Augen-Prinzip besteht nur mit passendem Hoster-Tarif und geprüfter Schutzregel (`chronicle vault doctor`, ADR 0016, Punkt 10).
- **Historie lässt sich nicht verlässlich aus allen Kopien tilgen** (ADR 0021).
