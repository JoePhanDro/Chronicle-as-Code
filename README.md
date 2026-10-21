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
