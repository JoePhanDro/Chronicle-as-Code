# Chronicle

Wandelt eine `timeline.yaml` mit Incident-Daten in ein interaktives HTML-Dashboard um:
Zeitstrahl (vis-timeline) oben, Details mit Markdown und Evidence sowie ein Entitäten-Graph (vis-network) unten.

## Start mit Docker

```bash
cp .env.example .env          # Admin-Benutzer und Passwort setzen (Passwort mind. 12 Zeichen)
docker compose --profile import run --rm importer   # Topic-Datei importieren (beliebig oft, ohne Duplikate)
docker compose up -d api      # API starten, danach http://localhost:8000/login
```

Der Import läuft bewusst nicht bei jedem `up`, auch wenn er idempotent ist.
Ohne Admin-Eintrag in `.env` legt der Server keinen Admin an. Dann:
`docker compose run --rm api python -m chronicle.admin_cli --db /db/chronicle.db create --username admin --role admin`.

Der statische Dashboard-Export (ohne Anmeldung, nur als Datei) läuft weiterhin ohne Compose:
`python -m chronicle --data ./data --out ./output`.

## Server, Anmeldung und Konten (Phase 2)

Topics und Events liegen in SQLite. Die YAML ist Importformat. Alle Daten-Endpunkte verlangen eine Anmeldung.

```bash
# Ersten Admin anlegen (Passwort wird verdeckt abgefragt)
python -m chronicle.admin_cli --db ./chronicle.db create --username admin --role admin

# Topic importieren und Server starten
python -m chronicle.importer --db ./chronicle.db --data ./data
python -m chronicle.serve --db ./chronicle.db --port 8000
```

Login im Browser: http://localhost:8000/login (mit Docker: siehe oben)

| Methode | Pfad | Rolle | Inhalt |
|---|---|---|---|
| GET | `/api/v1/health` | öffentlich | Status und Version |
| POST | `/api/v1/auth/login` | öffentlich | JSON `{username, password}`, setzt Sitzungscookie |
| POST | `/api/v1/auth/logout` | angemeldet | beendet die Sitzung (Header `X-CSRF-Token`) |
| GET | `/api/v1/auth/me` | angemeldet | eigenes Konto und CSRF-Token |
| GET | `/api/v1/topics` | angemeldet | alle Topics mit Anzahl der Events |
| GET | `/api/v1/topics/{key}` | angemeldet | ein Topic |
| GET | `/api/v1/topics/{key}/events` | angemeldet | Events eines Topics, zeitlich sortiert, mit Evidence |
| GET | `/api/v1/events` | angemeldet | alle Events |
| GET | `/api/v1/events/{key}` | angemeldet | ein Event mit Evidence und allen zugehörigen Topics |
| POST | `/api/v1/events` | analyst, admin | Event anlegen (Body wie in der Topic-Datei, plus `topic` und `topics`) |
| PUT | `/api/v1/events/{key}` | analyst, admin | Event ändern, Body mit `version` der gelesenen Fassung (sonst 409) |
| DELETE | `/api/v1/events/{key}` | analyst, admin | Event löschen (409, solange ein anderes Event darauf verweist) |
| GET | `/api/v1/users` | admin | Konten |
| POST | `/api/v1/users` | admin | Konto anlegen |
| PATCH | `/api/v1/users/{id}` | admin | Rolle, Aktivierung oder Passwort ändern |
| GET | `/api/v1/audit` | admin | Protokoll von Logins und Änderungen |

Rollen: `admin` und `analyst` lesen und bearbeiten Events, `viewer` liest nur.

Oberfläche: http://localhost:8000/events zeigt alle Events. Anlegen, Bearbeiten und Löschen laufen über Formulare
(mit CSRF-Schutz). Die Datenbank ist nach dem Import die Quelle der Wahrheit (ADR 0003):

- Ein erneuter Import überschreibt keine Events, die in der Oberfläche bearbeitet wurden, und holt keine gelöschten zurück. Er meldet beides.
- Ein Import entfernt keine Topic-Verknüpfungen. Die werden in der Oberfläche geändert.
- Jede Änderung hat eine Version. Wer auf einer veralteten Fassung speichert, bekommt 409 und muss neu laden.

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

Eine Datei beschreibt ein Topic (Incident, Schwachstelle, Projekt, besonderes Ereignis) und die Events dazu.
Events haben einen stabilen `key` und können zu mehreren Topics gehören.

```yaml
topic:
  key: inc-2026-042                  # eindeutig, Pflicht
  title: "INC-2026-042: …"           # Pflicht
  kind: incident                     # incident | vulnerability | project | special | other
  summary: "…"                       # optional

events:
  - key: exploit-2026-10-14          # eindeutig über alle Topics, Pflicht
    t0: 2026-10-14T08:40:00+02:00    # Pflicht, mit Zeitzone
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

**Heimat und Verweise:** Jedes Event wird in genau einer Datei definiert. Andere Topics binden es über `refs` ein.
Ein Event in zwei Dateien zu definieren, ist ein Fehler.

**Import ist idempotent.** Dieselbe Datei beliebig oft importieren ergibt denselben Stand. Events werden über ihren
Key aktualisiert, nicht neu angelegt. Entfernt man ein Event aus einer Datei, wird es nur von diesem Topic gelöst,
bleibt aber erhalten. Schlägt eine Prüfung fehl (z. B. unbekanntes `triggered_by`), wird nichts geschrieben.

Farben folgen der `phase`: rot für Identification/Exploit, gelb für Analysis, orange für Containment, grün für Mitigation/Recovery, blau für Lessons Learned.

## Hinweise

- Der Generator bricht mit einer klaren Fehlermeldung ab, wenn das Schema verletzt ist, eine Evidence-Datei fehlt oder ein Pfad aus dem Timeline-Ordner herausführt.
- Der Webserver lauscht nur auf `127.0.0.1`.
- Markdown wird im Browser mit DOMPurify bereinigt.
- Tailwind wird lokal gebaut (kein Play-CDN). Die übrigen CDN-Skripte sind per Subresource Integrity (SRI) gepinnt. Bei einem Versionswechsel muss der `integrity`-Hash im Template mit aktualisiert werden.
- Base-Images im Dockerfile sind per Digest gepinnt.
- Die Beispieldaten in `data/` sind fiktiv. Die SVG-Dateien sind Platzhalter, keine echten Screenshots.
