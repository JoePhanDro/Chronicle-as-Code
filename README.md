<p align="center">
  <img src="chronicle/static/icon.png" width="128" alt="Chronicle-Logo">
</p>

# Chronicle

Chronicle organisiert Sicherheitswissen: **Events** (Ereignisse mit Zeitpunkt, Akteur, System und Belegen) gehören zu **Topics** (Incidents, Schwachstellen, Projekte, besondere Ereignisse). Daraus entstehen Timeline, Entitäten-Graph und Detailansicht.

![Dashboard: Timeline oben, Entitäten-Graph unten, Phasen farblich markiert](docs/screenshots/dashboard.png)

*Dashboard eines Topics aus dem Beispiel `data/`, so wie es auf der Topic-Seite eingebettet ist. Fiktive Demo-Daten.*

> **Status:** Entwicklungsstand. Anmeldung, Rollen, Benutzerverwaltung, Pflege von Topics und Events in der Oberfläche (auch direkt in den Topic-Dateien), Evidence, Berichte, API und CLI laufen. Die Oberfläche gibt es in einem hellen und einem dunklen Design. Offene Punkte stehen in den [Issues](https://github.com/JoePhanDro/Chronicle-as-Code/issues).

## Inhalt

- [Schnellstart mit Docker](#schnellstart-mit-docker)
- [Lokal ohne Docker](#lokal-ohne-docker)
- [Berichte](#berichte)
- [Konfiguration](#konfiguration)
- [Datenformat (`timeline.yaml`)](#datenformat-timelineyaml)
- [Datenhaltung und Bearbeitung](#datenhaltung-und-bearbeitung)
- [Oberfläche](#oberfläche)
- [API](#api)
- [Rollen und Sicherheit](#rollen-und-sicherheit)
- [Projektstruktur](#projektstruktur)
- [Entwicklung und Tests](#entwicklung-und-tests)
- [Bekannte Einschränkungen](#bekannte-einschränkungen)
- [Dokumentation und Beitragen](#dokumentation-und-beitragen)

## Schnellstart mit Docker

Voraussetzung: Docker mit Compose.

```bash
cp .env.example .env
# In .env CHRONICLE_ADMIN_USER und CHRONICLE_ADMIN_PASSWORD setzen (Passwort mind. 12 Zeichen).
# Ohne diese Werte zeigt der Server beim Start einen Einmal-Code im Log, siehe unten.

docker compose --profile import run --rm importer   # Index aus den Topic-Dateien in ./data aufbauen
docker compose up -d                                # Server und HTTPS-Proxy starten
```

Dann https://localhost/login öffnen. Der Server ist nur über den Proxy erreichbar, der HTTPS auf Port 443 erzwingt (Port 80 leitet um). Liegen `certs/cert.pem` und `certs/key.pem` vor, nimmt der Proxy sie, sonst erzeugt er ein eigenes, selbst signiertes Zertifikat, und der Browser warnt. Für eine Domain mit echtem Zertifikat siehe [docs/betrieb-tls.md](docs/betrieb-tls.md).

**Lokale Entwicklung ohne HTTPS:** `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d` startet nur den Server auf `http://127.0.0.1:8000`, ohne Proxy.

Die Topic-Dateien in `./data` sind die Quelle der Wahrheit ([ADR 0011](docs/adr/0011-files-only-write-path.md)): Der Server schreibt Änderungen aus der Oberfläche in diese Dateien. Der Ordner muss für den Benutzer `1000` beschreibbar sein, unter dem der Container läuft. Falls nicht: `sudo chown -R 1000:1000 data`. Unter macOS und Windows ist das meist nicht nötig. Am besten steht `data` unter Versionskontrolle (Git), denn gelöschte Events lassen sich nur darüber zurückholen.

Der Import baut den Index auf und läuft bewusst nicht bei jedem `up`. Er ist idempotent, kann also beliebig oft laufen, und ist nötig, wenn Dateien außerhalb von Chronicle geändert wurden (alternativ **Neu laden** in der Oberfläche).

**Umstieg aus dem Datenbankmodus:** Wer bisher ohne `--data` gearbeitet und Events in der Oberfläche geändert hat, hat diese Änderungen nur in der Datenbank. Vor dem Umstellen exportieren und die Dateien nach `./data` übernehmen, sonst überschreibt der Index-Aufbau aus den alten Dateien den Stand der Datenbank:

```bash
mkdir -p export
docker compose run --rm -v "$PWD/export:/export" --entrypoint python api \
  -m chronicle.exporter --db /db/chronicle.db --out /export
```

Grabsteine (gelöschte Events der älteren Version) gibt es nicht mehr, ihre Tabellen werden beim ersten Öffnen der Datenbank entfernt. Was dort nur als Grabstein vorliegt, geht verloren. Vorher mit der alten Version unter **Verwaltung, Gelöschte Events** wiederherstellen und exportieren.

**Erster Start ohne `.env`:** Solange kein Konto existiert, schreibt der Server beim Start einen Einmal-Code ins Log:

```bash
docker compose logs api | grep "Einmal-Code"
```

Dann `https://localhost/setup` öffnen, den Code, einen Benutzernamen und ein Passwort (mindestens 12 Zeichen) eintragen. Der Benutzer wird Admin und ist sofort angemeldet. Danach ist `/setup` geschlossen. Wer den Code nicht aus dem Log lesen kann, kann den Admin nicht über die Oberfläche anlegen. Der Weg über die Kommandozeile bleibt möglich.

**Admin über die Kommandozeile anlegen:**

```bash
docker compose run --rm --entrypoint python api \
  -m chronicle.admin_cli --db /db/chronicle.db create --username admin --role admin
```

## Lokal ohne Docker

Voraussetzungen: Python 3.12 und Node.js 20 (nur für das CSS-Build).

```bash
npm ci && npm run build:css                 # Tailwind-CSS bauen
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

python -m chronicle.admin_cli --db ./chronicle.db create --username admin --role admin
python -m chronicle.importer --db ./chronicle.db --data ./data
CHRONICLE_COOKIE_SECURE=0 python -m chronicle.serve --db ./chronicle.db --data ./data --port 8000
```

`CHRONICLE_COOKIE_SECURE=0` ist nur für Tests über HTTP gedacht. Ohne diese Variable setzt der Server das Cookie
mit `Secure`, und Browser schicken es über `http://localhost` trotzdem zurück.

## Berichte

Ein Bericht ist eine einzelne HTML-Datei zu einem Topic, etwa als Anhang zu einem Post-Mortem (ADR 0001, ADR 0006). Er enthält Timeline, Graph, Event-Details, die verknüpften Events aus anderen Topics und alle Evidence-Dateien eingebettet. Er braucht weder Server noch Anmeldung und zeigt den Stand zum Zeitpunkt des Downloads.

- **In der Anwendung:** Admins öffnen **Berichte**, wählen ein Topic und laden die Datei. Die API hat dafür `GET /api/v1/topics/{key}/report`. Andere Rollen bekommen 403.
- **Als PDF** gibt es zwei Arten, beide ohne Graph und ohne Anmeldung lesbar: `technisch` mit allen Events, Details, Ursachen und Wirkungen, Evidence (Bilder eingebettet, andere Dateien aufgelistet), und `management` mit kurzen Kennzahlen und nur den Event-Titeln samt Zeit. Die Oberfläche bietet beide unter **Berichte**, die API über `GET /api/v1/topics/{key}/report.pdf?art=technisch|management`. Zeiten stehen in UTC. Die PDFs nutzen die eingebauten Schriften, Zeichen außerhalb von Latin-1 erscheinen als `?`.

Der Bericht ist eine Kopie der Daten. Wer die Datei hat, sieht alles. Teile sie nur bewusst und lösche sie danach.

## Konfiguration

| Variable | Wirkung | Standard |
|---|---|---|
| `CHRONICLE_DB` | Pfad zur SQLite-Datenbank für die API | `/data/chronicle.db` |
| `CHRONICLE_ADMIN_USER` | Erster Admin, nur wenn noch kein Konto existiert | leer (kein Bootstrap) |
| `CHRONICLE_ADMIN_PASSWORD` | Passwort zu `CHRONICLE_ADMIN_USER`. Der Admin muss es beim ersten Login ändern | leer |
| `CHRONICLE_COOKIE_SECURE` | `0` schaltet das `Secure`-Flag am Sitzungscookie ab (nur lokal über HTTP) | `1` |
| `CHRONICLE_TRUSTED_PROXIES` | Adressen oder Netze von Reverse Proxies, z. B. `172.18.0.2, 10.0.0.0/8`. Nur von dort wird `X-Forwarded-For` gelesen | leer |
| `CHRONICLE_DATA` | Datenordner mit den Topic-Dateien, die Quelle der Wahrheit (siehe unten). Pflicht: Ohne ihn startet der Server nicht. `python -m chronicle.serve --data <ordner>` setzt es | – |

Die Werte im Compose-Stack kommen aus `.env` (Vorlage: `.env.example`). `.env` wird nicht versioniert.

## Datenformat (`timeline.yaml`)

Eine Datei beschreibt ein Topic und die Events, die es definiert. Ein Beispiel liegt in `data/timeline.yaml`. Der Importer liest jede `*.yaml` im Datenordner, eine Datei je Topic. Verweise zwischen Dateien (`triggered_by`, `refs`) sind in beide Richtungen erlaubt ([ADR 0005](docs/adr/0005-export-and-backup.md)). Jede Datei ist Fremdinhalt und wird vor dem Laden geprüft: höchstens 4 MiB und 100.000 Knoten, Verschachtelung höchstens 12 Ebenen, keine Anker, Aliase, Tags, doppelten Schlüssel (Regeln in [docs/notation.md](docs/notation.md), Abschnitt 7). Das reicht für mehr als 1.000 Events je Datei.

```yaml
topic:
  key: inc-2026-042                  # eindeutig, Pflicht
  title: "INC-2026-042: …"           # Pflicht
  kind: incident                     # incident | vulnerability | project | special | other
  summary: "…"                       # optional

events:
  - key: exploit-2026-10-14          # eindeutig über alle Topics, Pflicht
    t0: 2026-10-14T08:40:00+02:00    # Pflicht, mit Zeitzone (gespeichert als UTC)
    phase: Exploit                   # Identification | Exploit | Analysis | Containment | Mitigation | Recovery | Lessons Learned
    actor: Angreifer (extern)
    system: wp-portal-01             # optional
    action: "…"
    status: in_progress              # open | in_progress | resolved
    triggered_by: cve-2026-xxxx      # optional: Key eines anderen Events
    details: |                       # optional, Markdown
      …
    evidence:                        # optional, Pfade relativ zur Datei
      - file: evidence/waf-block.png
        type: screenshot             # screenshot | log | report | other
        description: "…"

refs:                                # optional: Events, die in einer anderen Datei definiert sind
  - cve-2026-xxxx
```

- **Heimat:** Jedes Event wird in genau einer Datei definiert. Andere Topics binden es über `refs` ein.
- **Unbekannte Felder** sind Fehler. Das verhindert stille Tippfehler.
- **Evidence** wird beim Import geprüft und in der Datenbank gespeichert. Erlaubt sind PNG, JPEG, GIF, WebP, PDF und UTF-8-Text (`.txt`, `.log`), höchstens 10 MiB je Datei. Endung und Inhalt müssen zusammenpassen. SVG und HTML sind nicht erlaubt, weil Browser darin Skripte ausführen ([ADR 0004](docs/adr/0004-evidence-storage.md)).
- **Farben** folgen der Phase: rot für Identification und Exploit, gelb für Analysis, orange für Containment, grün für Mitigation und Recovery, blau für Lessons Learned.

### Prüfen ohne Server

`chronicle check` prüft einen Ordner mit Topic-Dateien. Es braucht weder Datenbank noch Server und schreibt nichts. Es meldet alle Probleme auf einmal: Schema, doppelte Topic- und Event-Keys, Verweise, Kreise in `triggered_by` und fehlende Anhänge.

```bash
python -m chronicle check data                  # alles prüfen, inklusive Anhänge
python -m chronicle check data --ohne-anhaenge  # ohne Anhänge, z. B. in der CI
```

Die Regeln stehen in [docs/notation.md](docs/notation.md). Der Import prüft dieselben Regeln, bevor er schreibt.

Editoren mit YAML-Unterstützung (z. B. VS Code mit der Erweiterung „YAML“) prüfen und ergänzen die Felder anhand des JSON Schemas. Exportierte Dateien und der Beispieldatensatz enthalten den Verweis bereits. `python -m chronicle schema` gibt das Schema aus, die Kopie im Repo liegt unter [schema/timeline.schema.json](schema/timeline.schema.json).

### Index schreiben

`chronicle apply` schreibt den Stand aus den Dateien in die Datenbank, die die Anwendung liest. Es ist wiederholbar: Ein zweiter Lauf ohne Änderung schreibt nichts und zählt keine Versionen hoch. Prüfung und Schreiben laufen in einer Transaktion, bei einem Fehler bleibt die Datenbank unverändert.

```bash
python -m chronicle apply data --db chronicle.db
```

Die Messung für Index und Topic-Seiten steht in [scripts/measure_index.py](scripts/measure_index.py).

### Statische Seiten

`chronicle build` schreibt pro Topic eine eigenständige HTML-Seite und eine `index.html` in einen Ordner. Die Seiten enthalten Stylesheet und Anhänge selbst, sie brauchen also keinen Server. Sie sind wie der Bericht aus der Datenbank als Snapshot gekennzeichnet und enthalten vertrauliche Inhalte. Die Ausgabe ist reproduzierbar: Derselbe Ordner ergibt dieselben Dateien.

```bash
npm run build:css                                # einmal, erzeugt das Stylesheet
python -m chronicle build data --out site
```

## Datenhaltung und Bearbeitung

**Datenordner (`--data` bzw. `CHRONICLE_DATA`, Pflicht):** Die Topic-Dateien sind die Quelle der Wahrheit ([ADR 0008](docs/adr/0008-files-as-source-of-truth.md)). Eine Bearbeitung in der Oberfläche oder über die API ändert nur die betroffenen Felder der Datei, Kommentare, Reihenfolge und Layout bleiben erhalten. Danach wird der Index aus den Dateien neu aufgebaut. Hat jemand die Datei außerhalb von Chronicle geändert, schlägt das Speichern mit einem Konflikt fehl, bis `chronicle apply` ausgeführt wurde.

- **Topics:** Ein neues Topic bekommt eine eigene Datei (`tp-<Datum>-<zufällig>.yaml`) und einen Key, den der Server vergibt. Den Key gibt man nicht ein, und er ändert sich nicht.
- **Events:** Ein neues Event wird an das Ende der Datei seines Topics geschrieben, den Key vergibt ebenfalls der Server (`ev-<Datum>-<zufällig>`). Ein gelöschtes Event verschwindet aus der Datei, und es gibt keinen Grabstein: Die Historie liefert Git. Ein Event, das ein anderes auslöst, lässt sich nicht löschen, solange der Verweis besteht.
- **Verknüpfungen** zwischen Topics stehen unter `refs` in der Datei des anderen Topics. Wer ein Event einem weiteren Topic zuordnet, bekommt dort eine Zeile, und wer die Zuordnung entfernt, verliert sie. Beim Löschen eines Events werden seine Verweise aus den anderen Topic-Dateien vorher entfernt.
- **Topic löschen:** Das löscht die Datei des Topics samt allen Events, die dort ihr Heimat-Topic haben. Events anderer Topics, die nur verknüpft sind, bleiben, nur ihr Verweis verschwindet. Hängt ein Event eines anderen Topics an einem der gelöschten Events (`triggered_by`), wird nichts gelöscht, und die Seite nennt die Events, bei denen der Verweis zuerst zu entfernen ist. Die Anhänge bleiben im Ordner `evidence/` liegen.
- **Anhänge** liegen als Dateien unter `evidence/<Event-Key>/` im Datenordner. Die Topic-Datei nennt Pfad, Typ und Beschreibung. Gleiche Namen mit anderem Inhalt bekommen eine Nummer, nichts wird überschrieben. Ein Anhang, der bereits am Event hängt, wird nicht doppelt angelegt. Der Ordner `evidence/` gehört in die Synchronisation (z. B. OneDrive), nicht in Git (ADR 0008).
- **Neu laden:** Wer Dateien von Hand oder durch ein Git-Pull ändert, drückt als Analyst oder Admin in der Kopfzeile **Neu laden** (im Menü „Konto“ auf dem Handy). Chronicle liest den Ordner dann neu ein. Ist eine Datei ungültig, bleibt der bisherige Stand bestehen, und die Seite nennt den Fehler.
- **Abgleichen:** Mehrere Kopien des Datenordners teilen sich einen Git-Remote. Unter **Daten › Abgleichen** (Analyst, Admin) zeigt Chronicle zuerst, was sich gegenüber dem Remote geändert hat. Ein Klick auf **Abgleichen** committet die Topic-Dateien, übernimmt den Stand des Remotes und lädt hoch. Einrichtung: Der Datenordner muss selbst das Wurzelverzeichnis eines Git-Repositorys sein (`git init`, dann `git remote add origin <URL>`) und auf einem Branch stehen. Der Zugang zum Remote (SSH-Schlüssel oder gespeicherte Anmeldung) muss ohne Rückfrage funktionieren, denn der Server kann nichts eintippen lassen. Ein Remote, der nach 120 Sekunden nicht antwortet, bricht den Abgleich ab; lokal bleibt alles erhalten. Anhänge (`evidence/`) sind nicht Teil davon.
  - Änderungen an verschiedenen Feldern führt ein Merge-Treiber selbst zusammen. Kommentare der eigenen Datei bleiben, Kommentare, die nur die andere Seite hat, gehen dabei verloren.
  - Haben beide Seiten dasselbe Feld geändert, bricht der Abgleich ab, und nichts wird hochgeladen. Die Seite zeigt je Datei beide Stände mit den abweichenden Zeilen. Man wählt je Datei **Diese Kopie behalten** oder **Stand des Remotes übernehmen**; die gewählte Datei ersetzt die andere vollständig. Danach wird geprüft und hochgeladen. Jeder Lauf steht im Audit-Log (`sync`), eine Auflösung mit den gewählten Seiten.

**Weitere Regeln:**

- Jede Änderung erhöht die Version eines Events. Wer auf einer veralteten Fassung speichert, bekommt `409` und muss neu laden.
- Ein Event, das ein anderes auslöst, lässt sich nicht löschen, solange der Verweis besteht.
- Evidence wird nur über `GET /api/v1/evidence/{id}` ausgeliefert, und nur an angemeldete Nutzer. Dateien können Analyst und Admin in der Oberfläche oder über die API hochladen und entfernen.

Der Server startet nicht ohne Datenordner. Einen Betrieb mit der Datenbank als Quelle gibt es nicht mehr ([ADR 0011](docs/adr/0011-files-only-write-path.md)). Wer von dort kommt, exportiert mit der alten Version, siehe den Umstieg im Schnellstart.

### Export und Backup

Zwei Befehle, mit unterschiedlichem Zweck:

- **Export** schreibt jedes Topic als YAML im Importformat, Evidence daneben. Die Dateien lassen sich mit dem Importer in eine leere Datenbank einspielen und ergeben dort wieder denselben Export. Konten, Sitzungen und das Audit-Log gehören nicht dazu.
- **Backup** kopiert den Index und die Betriebsdatei (`chronicle.ops.db` neben `chronicle.db`) konsistent, auch während der Dienst läuft. Die Betriebsdatei enthält Passwort-Hashes, Sitzungen und das Audit-Log und ist nicht aus den Dateien rekonstruierbar. Den Index kann man dagegen aus den Topic-Dateien neu aufbauen. Beide Kopien sind nur für den Eigentümer lesbar und gehören entsprechend gesichert.

```bash
mkdir -p export backup
docker compose run --rm -v "$PWD/export:/export" --entrypoint python api \
  -m chronicle.exporter --db /db/chronicle.db --out /export
docker compose run --rm -v "$PWD/backup:/backup" --entrypoint python api \
  -m chronicle.backup --db /db/chronicle.db --out /backup
```

Die Ordner `export` und `backup` müssen für den Benutzer `1000` beschreibbar sein, unter dem der Container läuft. Falls nicht: `sudo chown 1000:1000 export backup`.

**Wiederherstellen** aus einem Backup, nur bei gestopptem Dienst:

```bash
docker compose stop api
docker compose run --rm -v "$PWD/backup:/backup:ro" --entrypoint python api -c \
  "import shutil; shutil.copyfile('/backup/chronicle-JJJJMMTT-HHMMSS.db', '/db/chronicle.db')"
docker compose up -d
```

Den Dateinamen ersetzt man durch das gewünschte Backup. Der Export ist kein Ersatz für ein Backup. Er stellt Inhalte in lesbarer Form bereit, nicht Konten oder Protokoll.

## Oberfläche

Die Oberfläche ist serverseitig gerendert, Formulare sind mit einem CSRF-Token der Sitzung geschützt. Das Design (hell, dunkel oder wie das Betriebssystem), die Zeitzone der Anzeige (UTC oder lokale Zeit des Browsers) und die Dichte der Tabellen stellt man unter **Einstellungen** ein. Sie gelten pro Browser. Texte der Oberfläche sind deutsch, Phasennamen, Evidence, Timeline und Graph bleiben englisch, wie im Datenmodell.

| Pfad | Wer | Inhalt |
|---|---|---|
| `/login` | alle | Anmeldung mit Benutzername und Passwort |
| `/setup` | solange kein Konto existiert | ersten Admin mit dem Einmal-Code aus dem Server-Log anlegen |
| `/account/password` | angemeldet | eigenes Passwort ändern. Wer ein Passwort ändern muss, landet hier |
| `/` | angemeldet | Startseite |
| `/events` | angemeldet | alle Events, zeitlich sortiert und seitenweise. Mit `?topic=<key>` nur die Events eines Topics (Heimat und verknüpft), mit `?system=<name>` oder `?actor=<name>` nur die eines Systems oder Akteurs |
| `/suche` | angemeldet | Suche in Ereignissen, Akteuren und Systemen, als getrennte Treffer. Aufruf aus der Kopfzeile oder mit `/` |
| `/hilfe` | angemeldet | Tastenkürzel und Erklärung der Einstellungen |
| `/topics` | angemeldet | alle Topics: offene zuerst, dann nach neuestem Event. Zeigt Status, Phase des neuesten Events und Anzahl der Events. Ab sechs Topics gibt es einen Filter nach Text und Status |
| `/topics/new` | analyst, admin | Topic anlegen (Titel, Art, Zusammenfassung). Den Key vergibt der Server |
| `/topics/{key}` | angemeldet | Topic mit Status und seinen Events (je mit Phasenfarbe und Status), darunter das Dashboard als eingebettete Ansicht. Analyst und Admin bearbeiten das Topic, verknüpfen und lösen Events, schließen alle offenen Heimat-Events mit einem Knopf ab (das Topic hat keinen eigenen Status, er folgt den Events) und löschen das Topic samt seinen Heimat-Events |
| `/topics/{key}/dashboard` | angemeldet | Dashboard des Topics: Timeline, Entitäten-Graph und Details (die Ansicht auf `/topics/{key}` lädt sie als Frame) |
| `/events/new` | analyst, admin | Event anlegen. Die Zeit wird in einer wählbaren Zone eingegeben, die Zeile darunter zeigt, was das in UTC ergibt |
| `/events/{key}` | angemeldet | Details mit Phase und Status, Beschreibung (Markdown, auch Mermaid-Diagramme), Auslöser und Folgen, Evidence mit Vorschau, Topics. Analyst und Admin ändern Phase und Status direkt über die beiden Auswahlfelder im Kopf, bearbeiten die übrigen Felder im Bereich „Weitere Felder bearbeiten“, laden Evidence hoch und löschen das Event |
| `/reports` | admin | Topics mit Download des HTML-Berichts und der PDFs |
| `/admin/users` | admin | Konten anlegen, Rolle und Status ändern, Passwort setzen, Sperre aufheben |
| `/admin/audit` | admin | Audit-Log, filterbar nach Aktion, Benutzer und Datum |

Mermaid-Diagramme in Event-Beschreibungen werden im Browser gezeichnet. Die Bibliothek lädt dafür vom CDN und nur, wenn ein Diagramm auf der Seite steht. Die Diagramme übernehmen die Farben des gewählten Designs.

## API

Alle Pfade liegen unter `/api/v1`. Schreibende Anfragen brauchen den Header `X-CSRF-Token` mit dem Wert aus `GET /api/v1/auth/me`.

| Methode | Pfad | Rolle | Inhalt |
|---|---|---|---|
| GET | `/health` | öffentlich | Status und Version |
| POST | `/auth/login` | öffentlich | JSON `{username, password}`, setzt Sitzungscookie |
| POST | `/auth/logout` | angemeldet | beendet die Sitzung |
| POST | `/auth/password` | angemeldet | eigenes Passwort ändern, JSON `{current_password, new_password}`, beendet andere Sitzungen |
| GET | `/auth/me` | angemeldet | eigenes Konto und CSRF-Token |
| GET | `/topics` | angemeldet | alle Topics mit Anzahl der Events |
| GET | `/topics/{key}` | angemeldet | ein Topic |
| GET | `/topics/{key}/events` | angemeldet | Events eines Topics, zeitlich sortiert |
| GET | `/events` | angemeldet | alle Events, optional `?topic=<key>` |
| GET | `/events/{key}` | angemeldet | ein Event mit Evidence-Metadaten und Topics |
| POST | `/topics` | analyst, admin | Topic anlegen (`title`, `kind`, `summary`). `key` ist optional, ohne Key vergibt der Server einen (`tp-<Datum>-<zufällig>`) |
| PUT | `/topics/{key}` | analyst, admin | Titel, Art oder Zusammenfassung ändern. Der Key bleibt |
| DELETE | `/topics/{key}` | analyst, admin | Topic samt seinen Heimat-Events löschen (409, wenn Events anderer Topics an diesen Events hängen) |
| POST | `/topics/{key}/events` | analyst, admin | Event mit dem Topic verknüpfen (JSON `{event}`) |
| DELETE | `/topics/{key}/events/{event_key}` | analyst, admin | Verknüpfung lösen. Das Heimat-Topic eines Events lässt sich nicht lösen (422) |
| POST | `/events` | analyst, admin | Event anlegen (`topic` = Heimat, `topics` = weitere). `key` ist optional, ohne Key vergibt der Server einen (`ev-<Datum>-<zufällig>`). Evidence im Body gibt es nicht, siehe unten |
| PUT | `/events/{key}` | analyst, admin | Event ändern, Body mit `version` (sonst 409) |
| DELETE | `/events/{key}` | analyst, admin | Event löschen (409, solange ein anderes Event darauf verweist) |
| POST | `/events/{key}/evidence` | analyst, admin | Datei hochladen, multipart mit `file`, `type`, `description` |
| GET | `/evidence/{id}` | angemeldet | Inhalt einer Evidence-Datei |
| GET | `/topics/{key}/report` | admin | HTML-Bericht des Topics als Download |
| GET | `/topics/{key}/report.pdf?art=` | admin | PDF-Bericht, `technisch` (Standard) oder `management` |
| DELETE | `/evidence/{id}` | analyst, admin | Evidence entfernen |
| GET | `/users` | admin | Konten |
| POST | `/users` | admin | Konto anlegen |
| PATCH | `/users/{id}` | admin | Rolle, Aktivierung oder Passwort ändern. Ein neues Passwort muss der Nutzer beim nächsten Login ändern |
| POST | `/users/{id}/unlock` | admin | Login-Sperre eines Kontos für alle Adressen aufheben |
| GET | `/audit` | admin | Protokoll, optional `?action=`, `?actor=`, `?since=` (Unix-Sekunden) |

Die Antworten sind JSON. Eine interaktive Beschreibung der API gibt es noch nicht.

## Rollen und Sicherheit

| Rolle | Darf |
|---|---|
| `viewer` | lesen |
| `analyst` | lesen, Topics und Events anlegen, ändern und löschen, Events verknüpfen, Evidence hochladen und entfernen |
| `admin` | alles, plus Benutzer, Audit-Log, Berichte und Sperr-Passwort |

- Passwörter mit Argon2id gehasht, Mindestlänge 12 Zeichen.
- **Sperre (Lesen ohne Konto):** Ein Admin setzt unter „Einstellungen“ ein Sperr-Passwort. Dann lesen Besucher ohne Konto nur nach Eingabe dieses Passworts auf der Anmeldeseite. Der Server liefert vorher keine Daten aus. Ändern dürfen weiter nur Konten mit Rolle `analyst` oder `admin`. Entsperren gilt pro Browser, Passwort ändern oder Sperre aufheben beendet alle Entsperrungen ([ADR 0007](docs/adr/0007-reading-lock.md)).
- Sitzung über ein zufälliges Cookie (`HttpOnly`, `SameSite=Strict`, `Secure`). In der Datenbank liegt nur der SHA-256-Hash. Laufzeit 8 Stunden.
- Nach 5 Fehlversuchen innerhalb von 15 Minuten sperrt die Anmeldung Konto und IP-Adresse gemeinsam. Nach 20 Fehlversuchen sperrt sie die IP-Adresse für alle Konten. Ein Konto allein wird nie gesperrt. Hinter einem Reverse Proxy setzt man `CHRONICLE_TRUSTED_PROXIES`, damit die echte Adresse zählt ([Issue #27](https://github.com/JoePhanDro/Chronicle-as-Code/issues/27)).
- Ändern von Rolle oder Passwort und Deaktivieren beenden die Sitzungen des Kontos.
- Ein vergebenes Passwort (Bootstrap-Admin, durch einen Admin angelegt oder zurückgesetzt) muss der Nutzer beim nächsten Login ändern. Bis dahin sind nur der Passwortwechsel, `me` und Logout erlaubt.
- Beim Wechsel bleibt die aktuelle Sitzung bestehen, alle anderen Sitzungen des Kontos enden.
- Der letzte aktive Admin lässt sich nicht herabstufen oder deaktivieren.
- Jeder Login, jeder Fehlversuch und jede Änderung an Konten oder Events landet im Audit-Log, ohne Inhalte.

Der Standard-Stack hat kein TLS und bindet nur auf `127.0.0.1`. Für einen Betrieb im Netzwerk gibt es einen Caddy-Proxy mit HTTPS, beschrieben in [docs/betrieb-tls.md](docs/betrieb-tls.md) ([Issue #35](https://github.com/JoePhanDro/Chronicle-as-Code/issues/35)).

## Projektstruktur

```
chronicle/
  api.py             HTTP-App: setzt Seiten und Router zusammen, Bootstrap-Admin, Einmal-Code für /setup
  api_routes/        JSON-API unter /api/v1, ein Router je Bereich: topics, events, sync, admin
  pages.py           Oberfläche: setzt die Router von page_routes/ zusammen
  page_routes/       Seiten und Formulare, ein Router je Bereich: events, topics, reports, general (Suche, Hilfe, Neu laden)
  admin_pages.py     Verwaltung: Konten, Sperre, Audit-Log
  administration.py  Regeln für Konten und Sperren, von API und Verwaltung gemeinsam genutzt
  auth.py            Anmeldung, Sitzung, CSRF, Rollen
  reading_lock.py    Lesesperre mit Passwort für Besucher ohne Konto
  accounts.py        Konten, Sitzungen, Drosselung, Audit-Log (Betriebsdatei)
  security.py        Passwort-Hashing, Token
  repository.py      Einstieg für Datenoperationen, führt die folgenden Module zusammen
  queries.py         Leseabfragen auf dem Index (Events, Topics, Evidence, Filter)
  index.py           Index aus den Topic-Dateien aufbauen (Import, Neu laden)
  folder_writes.py   Gemeinsames beim Schreiben: Sperre, Rollback, Verweiszeilen
  events.py          Events anlegen, ändern, löschen
  topics.py          Topics anlegen, ändern, löschen, Events verknüpfen
  evidence_store.py  Anhänge hinzufügen und entfernen
  errors.py          Fehlerklassen des Repositorys
  store.py           gezielte Änderungen in den Topic-Dateien (Dateimodus)
  event_input.py     Eingabemodell für Schreibzugriffe, Keys und Zeitangaben
  db.py              SQLite-Schema und Migration
  models.py          Pydantic-Schema der YAML (strikt)
  loader.py          Suche, Validierung und Evidence-Prüfung der YAML
  yamlscan.py        Lesen von Fremd-YAML mit Grenzen (Größe, Tiefe, keine Aliase)
  check.py           chronicle check: Ordner prüfen, schreibt nichts
  importer.py        Index aus den Topic-Dateien aufbauen (Befehl `chronicle.importer`, wie **Neu laden**)
  exporter.py        Datenbank als YAML-Dateien je Topic exportieren
  backup.py          konsistente Kopie der Datenbank
  sync.py, merge.py  Abgleich mit dem Git-Remote und Merge-Treiber für Topic-Dateien
  build.py           chronicle build: statische Seiten
  schema.py          JSON Schema der Topic-Dateien
  evidence.py        erlaubte Dateien, Namen, Auslieferung
  markdown.py        Markdown der Event-Beschreibung, bereinigt
  renderer.py        Dashboard-Ansicht als HTML, Grundlage der Berichte
  report.py          Bericht zu einem Topic aus der Datenbank
  pdf_report.py      PDF-Berichte (technisch, Management Summary)
  cli.py, __main__.py  chronicle check | apply | build | schema
  serve.py           API mit uvicorn starten
  admin_cli.py       Konten über die Kommandozeile verwalten
  templates/         Jinja2-Vorlagen für Oberfläche und Dashboard
  static/            input.css (Quelle), app.css (gebaut, nicht versioniert), diagrams.js (Mermaid)
  static/vendor/     vis-timeline und vis-network, unverändert mitgeliefert (Herkunft in der README dort)
data/                Beispiel-YAML und Platzhalter-Evidence
docs/                ADRs, Notation der Topic-Dateien, TLS-Betrieb
tests/               pytest; tests/ui/ sind Browser-Tests
PRODUCT.md           Produktkontext (Nutzer, Zweck, Grundsätze)
DESIGN.md            Designsystem (Farben, Typografie, hell und dunkel, Komponenten)
```

## Entwicklung und Tests

```bash
npm ci && npm run build:css      # nach jeder Template-Änderung mit neuen Tailwind-Klassen
ruff check .                     # Lint
pytest                           # Tests (requirements-dev.txt)
coverage run --source=chronicle -m pytest -q && coverage report   # Abdeckung
pytest -m browser                # Browser-Tests (einmalig: python -m playwright install chromium)
```

Die CI läuft bei jedem Pull-Request: Lint und Tests mit Render-Lauf des Dashboards und JavaScript-Syntaxprüfung („Tests & smoke build“), Browser-Tests, Docker-Build mit Start des API-Containers und Health-Check, die Prüfung des Caddy-Proxys und `chronicle check` auf den Beispieldaten. Ein Merge erfolgt von Hand und nur bei grünen Checks (siehe [CONTRIBUTING.md](CONTRIBUTING.md)).

## Bekannte Einschränkungen

Die wichtigsten offenen Punkte:

- Ein Bericht liefert ein Topic ohne Anmeldung, sobald man die Datei hat. Er ist ein Snapshot und kein Live-Stand ([#24](https://github.com/JoePhanDro/Chronicle-as-Code/issues/24)).
- Mermaid-Diagramme brauchen beim Anzeigen Zugriff auf das CDN. Berichte und statische Seiten sind deshalb nahezu, aber nicht vollständig offline nutzbar.
- Im Dateimodus bleiben Anhänge beim Löschen eines Events oder Topics im Ordner `evidence/` liegen, und es gibt keine Wiederherstellung in der Oberfläche. Die frühere Fassung der Topic-Datei liefert Git.

Vollständige Liste: [Issues](https://github.com/JoePhanDro/Chronicle-as-Code/issues).

## Dokumentation und Beitragen

- Architekturentscheidungen: [`docs/adr/`](docs/adr/)
  - [ADR 0001](docs/adr/0001-backend-auth-pages.md): Server, Anmeldung, Seiten (akzeptiert)
  - [ADR 0002](docs/adr/0002-topics-and-events.md): Events und Topics (zu bestätigen)
  - [ADR 0003](docs/adr/0003-editing-in-the-ui.md): Bearbeiten in der Oberfläche (abgelöst durch ADR 0008 und ADR 0011)
  - [ADR 0004](docs/adr/0004-evidence-storage.md): Evidence in der Datenbank, Abweichung von ADR 0001 (zu bestätigen)
  - [ADR 0005](docs/adr/0005-export-and-backup.md): YAML-Export, Backup und Import mehrerer Topic-Dateien (zu bestätigen)
  - [ADR 0006](docs/adr/0006-reports-from-the-app.md): Berichte aus der Datenbank, nur für Admins (zu bestätigen)
  - [ADR 0007](docs/adr/0007-reading-lock.md): Lesesperre mit Passwort (angenommen)
  - [ADR 0008](docs/adr/0008-files-as-source-of-truth.md): Dateien als Quelle der Wahrheit (akzeptiert)
  - [ADR 0009](docs/adr/0009-cli-first.md): CLI als Kernprodukt, Server als Oberfläche (akzeptiert für den CLI-Teil)
  - [ADR 0010](docs/adr/0010-operations-separate.md): Betriebsdaten getrennt vom Index (akzeptiert)
  - [ADR 0011](docs/adr/0011-files-only-write-path.md): Dateien sind der einzige Schreibpfad (akzeptiert, umgesetzt)
  - [ADR 0012](docs/adr/0012-code-repo-und-vault-repos.md): Code-Repository und Vault-Repositories (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0013](docs/adr/0013-vault-layout-und-format.md): Layout und Format des Vaults (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0014](docs/adr/0014-lokale-konfiguration-und-login.md): Lokale Konfiguration und Login (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0015](docs/adr/0015-identitaet-und-autorenschaft.md): Identität und Autorenschaft (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0016](docs/adr/0016-freigabe-workflow.md): Freigabe-Workflow (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0017](docs/adr/0017-konflikte-und-merge.md): Konflikte und Merge (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0018](docs/adr/0018-index-als-abgeleiteter-zustand.md): Index als abgeleiteter Zustand (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0019](docs/adr/0019-mehrere-vaults-und-mandantentrennung.md): Mehrere Vaults und Mandantentrennung (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0020](docs/adr/0020-anhaenge-im-vault.md): Anhänge im Vault (Entscheidung des Projektinhabers: in Git, große Dateien per Link) (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0021](docs/adr/0021-sensible-daten-und-loeschkonzept.md): Sensible Daten und Löschkonzept (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0022](docs/adr/0022-git-integration-und-eingabepruefung.md): Git-Integration und Eingabeprüfung (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0023](docs/adr/0023-migration-und-formatversionen.md): Migration und Formatversionen (Vorschlag, [Architektur](docs/architecture/vault-git.md))
  - [ADR 0024](docs/adr/0024-verschluesselung-der-eintraege.md): Verschlüsselung der Einträge (Entscheidung: zunächst keine, nicht im Scope, [Architektur](docs/architecture/vault-git.md))
- Notation der Topic-Dateien: [`docs/notation.md`](docs/notation.md). Produktkontext und Designsystem: [`PRODUCT.md`](PRODUCT.md) und [`DESIGN.md`](DESIGN.md).
- Wie man beiträgt, steht in [CONTRIBUTING.md](CONTRIBUTING.md).

Die Beispieldaten in `data/` sind fiktiv. Die Evidence-PNGs in `data/evidence/` sind Platzhalter, aus SVG-Vorlagen gerendert, keine echten Screenshots. `docs/screenshots/dashboard.png` zeigt das Dashboard mit diesen Demo-Daten.
