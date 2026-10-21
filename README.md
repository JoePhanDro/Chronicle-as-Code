# Chronicle

Wandelt eine `timeline.yaml` mit Incident-Daten in ein interaktives HTML-Dashboard um:
Zeitstrahl (vis-timeline) oben, Details mit Markdown und Evidence sowie ein Entitäten-Graph (vis-network) unten.

## Start

```bash
# 1. Vorlage anpassen: data/timeline.yaml und Evidence-Dateien in data/ ablegen
# 2. Bauen und starten
docker compose up --build

# 3. Dashboard öffnen
#    http://localhost:8080
```

Nach Änderungen an der YAML nur neu generieren und die Seite neu laden:

```bash
docker compose run --rm generator
```

Ohne Docker (Python 3.11+):

```bash
npm ci && npm run build:css      # Tailwind-CSS bauen (einmalig bzw. nach Template-Änderungen)
pip install -r requirements.txt
python -m chronicle --data ./data --out ./output
python -m http.server 8080 --directory ./output
```

## Server, Anmeldung und Konten (Phase 2)

Incidents liegen in SQLite. Die YAML ist Importformat. Alle Daten-Endpunkte verlangen eine Anmeldung.

```bash
# Ersten Admin anlegen (Passwort wird verdeckt abgefragt)
python -m chronicle.admin_cli --db ./chronicle.db create --username admin --role admin

# Incident importieren und Server starten
python -m chronicle.importer --db ./chronicle.db --data ./data
python -m chronicle.serve --db ./chronicle.db --port 8000
```

Alternativ legt der Server beim ersten Start einen Admin an, wenn noch kein Konto existiert:
`CHRONICLE_ADMIN_USER` und `CHRONICLE_ADMIN_PASSWORD` setzen.

Login im Browser: http://localhost:8000/login

| Methode | Pfad | Rolle | Inhalt |
|---|---|---|---|
| GET | `/api/v1/health` | öffentlich | Status und Version |
| POST | `/api/v1/auth/login` | öffentlich | JSON `{username, password}`, setzt Sitzungscookie |
| POST | `/api/v1/auth/logout` | angemeldet | beendet die Sitzung (Header `X-CSRF-Token`) |
| GET | `/api/v1/auth/me` | angemeldet | eigenes Konto und CSRF-Token |
| GET | `/api/v1/incidents` | angemeldet | alle Incidents |
| GET | `/api/v1/incidents/{id}` | angemeldet | ein Incident |
| GET | `/api/v1/incidents/{id}/events` | angemeldet | Events mit Evidence |
| GET | `/api/v1/users` | admin | Konten |
| POST | `/api/v1/users` | admin | Konto anlegen |
| PATCH | `/api/v1/users/{id}` | admin | Rolle, Aktivierung oder Passwort ändern |
| GET | `/api/v1/audit` | admin | Protokoll von Logins und Änderungen |

Rollen: `admin` (alles), `analyst` (Lesen, später Bearbeiten), `viewer` (Lesen).

Sicherheit im Überblick:

- Passwörter mit Argon2id gehasht, Mindestlänge 12 Zeichen.
- Sitzung über zufälliges Cookie (`HttpOnly`, `SameSite=Strict`, `Secure`), in der Datenbank nur als SHA-256-Hash. Laufzeit 8 Stunden.
- Schreibende Anfragen brauchen das CSRF-Token im Header `X-CSRF-Token`.
- Nach 5 Fehlversuchen innerhalb von 15 Minuten wird ein Benutzername oder eine IP-Adresse gesperrt.
- Ändern von Rolle oder Passwort und Deaktivieren beenden die Sitzungen des Kontos.
- Der letzte aktive Admin lässt sich nicht herabstufen oder deaktivieren.
- Jeder Login, jeder Fehlversuch und jede Kontoänderung wird im Audit-Log festgehalten.

`CHRONICLE_COOKIE_SECURE=0` schaltet das `Secure`-Flag für lokale Tests über HTTP ab. Im Betrieb nicht setzen.

## Aufbau

```
chronicle/
  models.py      Pydantic-Schema (strikt, unbekannte Felder sind Fehler)
  loader.py      sucht timeline.yaml, validiert, prüft Evidence-Pfade
  renderer.py    baut Graph-Daten und rendert das Jinja2-Template
  builder.py     kopiert Evidence, schreibt index.html atomar
  templates/     dashboard.html (Tailwind, vis-timeline, vis-network, marked, DOMPurify)
data/            Eingabe: timeline.yaml + Evidence
Dockerfile       ein Image für Generator und Webserver
docker-compose.yml
```

## Schema (Kurzfassung)

```yaml
title: "INC-…"            # Pflicht
summary: "…"              # optional
events:
  - id: analysis          # optional, sonst evt-001, evt-002, …
    t0: 2026-10-14T09:30:00+02:00   # Pflicht, mit Zeitzone
    phase: Analysis       # Identification | Exploit | Analysis | Containment | Mitigation | Recovery | Lessons Learned
    actor: CSIRT Analyst A
    system: wp-portal-01  # optional
    action: "…"           # Pflicht
    status: in_progress   # open | in_progress | resolved
    triggered_by: exploit # optional: Event-ID oder Akteur-Name
    details: |            # optional, Markdown
      …
    evidence:             # optional, Pfade relativ zur timeline.yaml
      - file: evidence/waf-block.svg
        type: screenshot  # screenshot | log | report | other
        description: "…"
```

Farben folgen der `phase`: rot für Identification/Exploit, gelb für Analysis, orange für Containment, grün für Mitigation/Recovery, blau für Lessons Learned.

## Hinweise

- Der Generator bricht mit einer klaren Fehlermeldung ab, wenn das Schema verletzt ist, eine Evidence-Datei fehlt oder ein Pfad aus dem Timeline-Ordner herausführt.
- Der Webserver lauscht nur auf `127.0.0.1`.
- Markdown wird im Browser mit DOMPurify bereinigt.
- Tailwind wird lokal gebaut (kein Play-CDN). Die übrigen CDN-Skripte sind per Subresource Integrity (SRI) gepinnt. Bei einem Versionswechsel muss der `integrity`-Hash im Template mit aktualisiert werden.
- Base-Images im Dockerfile sind per Digest gepinnt.
- Die Beispieldaten in `data/` sind fiktiv. Die SVG-Dateien sind Platzhalter, keine echten Screenshots.
