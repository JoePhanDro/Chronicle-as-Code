# Chronicle

Chronicle organisiert Sicherheitswissen: **Events** (Ereignisse mit Zeitpunkt, Akteur, System und Belegen) gehören zu **Topics** (Incidents, Schwachstellen, Projekte, besondere Ereignisse). Daraus entstehen Timeline, Entitäten-Graph und Detailansicht.

> **Status:** Entwicklungsstand. Anmeldung, Rollen, Benutzerverwaltung über API und CLI, Event-Pflege in der Oberfläche und der API-Server laufen. Timeline und Graph in der Oberfläche (Phase 3) sind noch nicht umgesetzt. Offene Punkte stehen in den [Issues](https://github.com/JoePhanDro/Chronicle-as-Code/issues).

## Inhalt

- [Schnellstart mit Docker](#schnellstart-mit-docker)
- [Lokal ohne Docker](#lokal-ohne-docker)
- [Konfiguration](#konfiguration)
- [Datenformat (`timeline.yaml`)](#datenformat-timelineyaml)
- [Datenhaltung und Bearbeitung](#datenhaltung-und-bearbeitung)
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
# Ohne diese Werte legt der Server keinen Admin an, siehe unten.

docker compose --profile import run --rm importer   # Topic-Datei aus ./data importieren
docker compose up -d api                            # Server starten
```

Dann http://localhost:8000/login öffnen. Der Port ist nur auf `127.0.0.1` gebunden.

Der Import läuft bewusst nicht bei jedem `up`. Er ist idempotent, kann also beliebig oft laufen.

**Admin ohne `.env` anlegen:**

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
CHRONICLE_COOKIE_SECURE=0 python -m chronicle.serve --db ./chronicle.db --port 8000
```

`CHRONICLE_COOKIE_SECURE=0` ist nur für Tests über HTTP gedacht. Ohne diese Variable setzt der Server das Cookie
mit `Secure`, und Browser schicken es über `http://localhost` trotzdem zurück.

## Konfiguration

| Variable | Wirkung | Standard |
|---|---|---|
| `CHRONICLE_DB` | Pfad zur SQLite-Datenbank für die API | `/data/chronicle.db` |
| `CHRONICLE_ADMIN_USER` | Erster Admin, nur wenn noch kein Konto existiert | leer (kein Bootstrap) |
| `CHRONICLE_ADMIN_PASSWORD` | Passwort zu `CHRONICLE_ADMIN_USER` | leer |
| `CHRONICLE_COOKIE_SECURE` | `0` schaltet das `Secure`-Flag am Sitzungscookie ab (nur lokal über HTTP) | `1` |

Die Werte im Compose-Stack kommen aus `.env` (Vorlage: `.env.example`). `.env` wird nicht versioniert.

## Datenformat (`timeline.yaml`)

Eine Datei beschreibt ein Topic und die Events, die es definiert. Ein Beispiel liegt in `data/timeline.yaml`.

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
      - file: evidence/waf-block.svg
        type: screenshot             # screenshot | log | report | other
        description: "…"

refs:                                # optional: Events, die in einer anderen Datei definiert sind
  - cve-2026-xxxx
```

- **Heimat:** Jedes Event wird in genau einer Datei definiert. Andere Topics binden es über `refs` ein.
- **Unbekannte Felder** sind Fehler. Das verhindert stille Tippfehler.
- **Farben** folgen der Phase: rot für Identification und Exploit, gelb für Analysis, orange für Containment, grün für Mitigation und Recovery, blau für Lessons Learned.

## Datenhaltung und Bearbeitung

Die Datenbank ist nach dem Import die Quelle der Wahrheit ([ADR 0003](docs/adr/0003-editing-in-the-ui.md)):

- Ein erneuter Import überschreibt keine Events, die in der Oberfläche bearbeitet wurden. Gelöschte Events kehren nicht zurück. Beides meldet der Import.
- Ein Import entfernt keine Topic-Verknüpfungen. Die ändert man in der Oberfläche.
- Jede Änderung erhöht die Version eines Events. Wer auf einer veralteten Fassung speichert, bekommt `409` und muss neu laden.
- Ein Event, das ein anderes auslöst, lässt sich nicht löschen, solange der Verweis besteht.

**Achtung:** Es gibt noch keinen Export zurück in YAML. Die Datenbank ist dann die einzige vollständige Kopie. Sichere das SQLite-File oder das Volume `chronicle-db` selbst ([Issue #23](https://github.com/JoePhanDro/Chronicle-as-Code/issues/23)).

## Oberfläche

| Pfad | Wer | Inhalt |
|---|---|---|
| `/login` | alle | Anmeldung mit Benutzername und Passwort |
| `/` | angemeldet | Startseite |
| `/events` | angemeldet | alle Events, zeitlich sortiert |
| `/events/new` | analyst, admin | Event anlegen |
| `/events/{key}` | angemeldet | Details, Evidence, Topics; bearbeiten und löschen für analyst und admin |

Die Oberfläche ist serverseitig gerendert. Formulare sind mit einem CSRF-Token der Sitzung geschützt.

## API

Alle Pfade liegen unter `/api/v1`. Schreibende Anfragen brauchen den Header `X-CSRF-Token` mit dem Wert aus `GET /api/v1/auth/me`.

| Methode | Pfad | Rolle | Inhalt |
|---|---|---|---|
| GET | `/health` | öffentlich | Status und Version |
| POST | `/auth/login` | öffentlich | JSON `{username, password}`, setzt Sitzungscookie |
| POST | `/auth/logout` | angemeldet | beendet die Sitzung |
| GET | `/auth/me` | angemeldet | eigenes Konto und CSRF-Token |
| GET | `/topics` | angemeldet | alle Topics mit Anzahl der Events |
| GET | `/topics/{key}` | angemeldet | ein Topic |
| GET | `/topics/{key}/events` | angemeldet | Events eines Topics, zeitlich sortiert |
| GET | `/events` | angemeldet | alle Events |
| GET | `/events/{key}` | angemeldet | ein Event mit Evidence und Topics |
| POST | `/events` | analyst, admin | Event anlegen (`topic` = Heimat, `topics` = weitere) |
| PUT | `/events/{key}` | analyst, admin | Event ändern, Body mit `version` (sonst 409) |
| DELETE | `/events/{key}` | analyst, admin | Event löschen (409, solange ein anderes Event darauf verweist) |
| GET | `/users` | admin | Konten |
| POST | `/users` | admin | Konto anlegen |
| PATCH | `/users/{id}` | admin | Rolle, Aktivierung oder Passwort ändern |
| GET | `/audit` | admin | Protokoll von Anmeldungen und Änderungen |

Die Antworten sind JSON. Eine interaktive Beschreibung der API gibt es noch nicht.

## Rollen und Sicherheit

| Rolle | Darf |
|---|---|
| `viewer` | lesen |
| `analyst` | lesen, Events anlegen, ändern, löschen |
| `admin` | alles, plus Benutzer und Audit-Log |

- Passwörter mit Argon2id gehasht, Mindestlänge 12 Zeichen.
- Sitzung über ein zufälliges Cookie (`HttpOnly`, `SameSite=Strict`, `Secure`). In der Datenbank liegt nur der SHA-256-Hash. Laufzeit 8 Stunden.
- Nach 5 Fehlversuchen innerhalb von 15 Minuten sind Benutzername und IP-Adresse gesperrt. Hinter einem Reverse Proxy ist die IP-Sperre so noch nicht richtig ([Issue #27](https://github.com/JoePhanDro/Chronicle-as-Code/issues/27)).
- Ändern von Rolle oder Passwort und Deaktivieren beenden die Sitzungen des Kontos.
- Der letzte aktive Admin lässt sich nicht herabstufen oder deaktivieren.
- Jeder Login, jeder Fehlversuch und jede Änderung an Konten oder Events landet im Audit-Log, ohne Inhalte.

Der Compose-Stack hat noch kein TLS. Für einen Betrieb im Netzwerk braucht es einen Reverse Proxy mit HTTPS ([Issue #35](https://github.com/JoePhanDro/Chronicle-as-Code/issues/35)).

## Projektstruktur

```
chronicle/
  api.py           HTTP-App: API, Routen für Benutzer und Audit, Bootstrap-Admin
  pages.py         Oberfläche: Events anlegen, bearbeiten, löschen (Formulare)
  auth.py          Anmeldung, Sitzung, CSRF, Rollen
  accounts.py      Konten, Sitzungen, Drosselung, Audit-Log (Datenbank)
  security.py      Passwort-Hashing, Token
  repository.py    Topics und Events: Import und Schreibzugriffe
  event_input.py   Eingabemodell für Schreibzugriffe (gleiche Regeln wie der Import)
  db.py            SQLite-Schema und Migration
  importer.py      YAML in die Datenbank importieren
  serve.py         API mit uvicorn starten
  admin_cli.py     Konten über die Kommandozeile verwalten
  models.py        Pydantic-Schema der YAML (strikt)
  loader.py        Suche, Validierung und Evidence-Prüfung der YAML
  renderer.py      Dashboard-Export (statisch, siehe Issue #24)
  builder.py       schreibt den Dashboard-Export
  templates/       Jinja2-Vorlagen für Oberfläche und Dashboard
  static/          input.css (Quelle) und app.css (gebaut, nicht versioniert)
data/              Beispiel-YAML und Platzhalter-Evidence
docs/adr/          Architekturentscheidungen
tests/             pytest
```

## Entwicklung und Tests

```bash
npm ci && npm run build:css      # nach jeder Template-Änderung mit neuen Tailwind-Klassen
pytest                           # Tests (requirements-dev.txt)
```

Die CI läuft bei jeder Pull-Request: Tests, Smoke-Lauf des Dashboard-Exports, JavaScript-Syntaxprüfung, Docker-Build und Start des API-Containers mit Health-Check. Ein Merge ist nur möglich, wenn beide Checks grün sind.

## Bekannte Einschränkungen

Die wichtigsten offenen Punkte, mit Issue-Nummern:

- Evidence wird nur als Pfad gespeichert, nicht ausgeliefert ([#22](https://github.com/JoePhanDro/Chronicle-as-Code/issues/22))
- Kein Export zurück in YAML ([#23](https://github.com/JoePhanDro/Chronicle-as-Code/issues/23))
- Der statische Dashboard-Export liefert alle Daten ohne Anmeldung, wenn man ihn ausführt ([#24](https://github.com/JoePhanDro/Chronicle-as-Code/issues/24))
- Timeline und Graph gibt es nur im statischen Export, nicht hinter der Anmeldung ([#25](https://github.com/JoePhanDro/Chronicle-as-Code/issues/25))
- Topics lassen sich nur über API und YAML anlegen ([#26](https://github.com/JoePhanDro/Chronicle-as-Code/issues/26))
- Keine Suche und keine Paginierung ([#30](https://github.com/JoePhanDro/Chronicle-as-Code/issues/30))

Vollständige Liste: [Issues](https://github.com/JoePhanDro/Chronicle-as-Code/issues).

## Dokumentation und Beitragen

- Architekturentscheidungen: [`docs/adr/`](docs/adr/)
  - [ADR 0001](docs/adr/0001-backend-auth-pages.md): Server, Anmeldung, Seiten (akzeptiert)
  - [ADR 0002](docs/adr/0002-topics-and-events.md): Events und Topics (zu bestätigen)
  - [ADR 0003](docs/adr/0003-editing-in-the-ui.md): Bearbeiten in der Oberfläche (zu bestätigen)
- Wie man beiträgt, steht in [CONTRIBUTING.md](CONTRIBUTING.md).

Die Beispieldaten in `data/` sind fiktiv. Die SVG-Dateien sind Platzhalter, keine echten Screenshots.
