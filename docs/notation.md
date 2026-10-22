# Chronicle-Notation

- **Status:** Entwurf zu [ADR 0008](adr/0008-files-as-source-of-truth.md), Punkt 9
- **Stand:** Code vom 2026-10-22 (`chronicle/models.py`, `chronicle/repository.py`, `chronicle/evidence.py`, `chronicle/loader.py`)
- **Entschieden, noch nicht umgesetzt:** `triggered_by` ist eine Liste mit 0 bis n Einträgen (#87). Auslösungen bilden keinen Zyklus (#85).

Abschnitte, die mit **Vorschlag** gekennzeichnet sind, sind nicht umgesetzt. Abschnitte mit „Ziel-Stand“ beschreiben die getroffene Entscheidung, die der Code noch nicht hat. Alles andere beschreibt das heutige Verhalten.

## 1. Grundidee

Ein Chronicle-Stand ist ein Ordner mit YAML-Dateien. Jede Datei beschreibt genau ein Topic, die Events, die darin definiert sind, und Verweise auf Events aus anderen Topics. Die Evidence-Dateien liegen daneben. Die Notation bildet Topics, Events und ihre Verknüpfungen vollständig ab. Konten, Sitzungen und das Audit-Log gehören nicht dazu (ADR 0005).

## 2. Dateien und Ordner

- Jede Datei mit der Endung `.yaml` oder `.yml` ist eine Topic-Datei. Unterordner werden durchsucht.
- Ein Topic steht in genau einer Datei.
- Der Pfad einer Evidence-Datei ist relativ zum Ordner der Topic-Datei. Er darf kein `..` enthalten und keine Symlinks nach außen. Evidence liegt also im Ordner der Datei oder darunter. Empfohlen ist `evidence/<event-key>/<dateiname>` neben der Topic-Datei.
- Die Namen `index.html` und `index.html.tmp` sind als Evidence-Pfad reserviert.

## 3. Aufbau einer Datei

```yaml
topic:        # Pflicht: das Topic
  key: ...
events:       # optional: Events, deren Heimat diese Datei ist
  - key: ...
refs:         # optional: Keys von Events aus anderen Dateien
  - ...
```

Unbekannte Felder sind ein Fehler. Ein Tippfehler im Schlüssel wird also nicht still verworfen.

## 4. Felder

### Topic

| Feld | Pflicht | Regel |
|---|---|---|
| `key` | ja | Muster `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, eindeutig über alle Dateien (siehe 7) |
| `title` | ja | nicht leer |
| `kind` | nein | `incident`, `vulnerability`, `project`, `special` oder `other` (Standard: `other`) |
| `summary` | nein | Text, Standard leer |

### Event

| Feld | Pflicht | Regel |
|---|---|---|
| `key` | ja | gleiches Muster wie beim Topic, eindeutig über alle Dateien |
| `t0` | ja | ISO-8601-Zeitpunkt mit Offset, z. B. `2026-10-14T08:40:00+02:00`. Gespeichert als UTC |
| `phase` | ja | `Identification`, `Exploit`, `Analysis`, `Containment`, `Mitigation`, `Recovery`, `Lessons Learned` oder `Activity` |
| `actor` | ja | nicht leer. Akteure sind Attribute, keine eigenen Knoten (ADR 0002) |
| `system` | nein | Text |
| `action` | ja | nicht leer |
| `status` | ja | `open`, `in_progress` oder `resolved` |
| `triggered_by` | nein | Liste von Event-Keys mit 0 bis n Einträgen. Ziel-Stand, heute ein einzelner Key (#87). Siehe 5 |
| `details` | nein | Markdown |
| `evidence` | nein | Liste von Evidence-Einträgen (siehe 6) |

Die Phasenfarben gehören zur Darstellung und sind nicht Teil der Notation.

### Evidence

| Feld | Pflicht | Regel |
|---|---|---|
| `file` | ja | relativer Pfad, siehe 2 |
| `type` | ja | `screenshot`, `log`, `report` oder `other` |
| `description` | nein | Text |

### Reihenfolge

Events werden nach `t0` sortiert, bei Gleichstand nach `key`. Ein Export ist damit reproduzierbar.

## 5. Verknüpfungen

Es gibt drei Arten von Verknüpfungen.

**a) Zugehörigkeit zu Topics.** Jedes Event hat genau eine Heimat: die Datei, in deren `events:` es steht. Weitere Topics binden es über `refs:` in ihrer eigenen Datei ein. Dabei gilt:
- Ein Event steht in genau einer Datei unter `events:`.
- Ein Key darf in derselben Datei nicht zugleich unter `events:` und `refs:` stehen.
- `refs:` verweist nur auf Events, die irgendwo definiert sind.
- Ein Event kann in beliebig vielen Topics per `refs:` stehen.

**b) Auslösung.** `triggered_by` nennt die Keys der Events, die dieses Event ausgelöst haben. Es ist eine Liste mit 0 bis n Einträgen. Die Ziele können in anderen Dateien stehen, müssen aber existieren. Ein Event kann sich nicht selbst auslösen. Heute ist nur ein einzelner Key erlaubt, die Umstellung auf eine Liste ist in #87 erfasst.

**c) Zyklen.** Auslösungen dürfen keinen Kreis bilden. Eine Auslösekette darf nicht zu ihrem Ausgangs-Event zurückführen. Der Code prüft das heute nicht, die Prüfung ist in #85 erfasst.

## 6. Evidence

- Die Datei muss existieren und innerhalb des Ordners der Topic-Datei liegen.
- Erlaubt sind PNG, JPEG, GIF, WebP, PDF und UTF-8-Text (`.txt`, `.log`). Endung und Inhalt müssen zusammenpassen.
- SVG und HTML sind verboten, weil Browser darin Skripte ausführen (ADR 0004).
- Höchstens 10 MiB je Datei.
- Der Dateiname dient nur der Anzeige. Ausgeliefert wird die Datei über ihren Pfad im Chronicle-Stand.

**Vorschlag:** Pro Evidence-Eintrag ein Feld `sha256`. Es wäre Pflicht, damit fehlende oder veränderte Anhänge auffallen (ADR 0008, Punkt 6).

## 7. Prüfregeln

Die Prüfung (Import heute, Prüfmodus nach Issue #85) verlangt:

1. Das Schema stimmt. Unbekannte Felder und fehlende Pflichtfelder sind Fehler.
2. Event-Keys sind eindeutig über alle Dateien. Innerhalb einer Datei prüft das `models.py`, über Dateien hinweg `import_timelines` in `repository.py`.
3. Ein Key steht nicht zugleich unter `events:` und `refs:` derselben Datei.
4. `refs:` und `triggered_by` verweisen auf existierende Events.
5. Ein Event löst nicht sich selbst aus, und Auslösungen bilden keinen Zyklus (#85).
6. Evidence-Dateien existieren, liegen innerhalb des Ordners und sind zulässig.

**Lücke (#85):** Topic-Keys werden über Dateien hinweg **nicht** geprüft. Zwei Dateien mit demselben Topic-Key überschreiben sich still. Der Import führt ein Upsert auf den Key aus (`_write_topic`), Titel, Art und Zusammenfassung der zweiten Datei gewinnen, und die Events landen im selben Topic. Die Regel 2 gilt für Event-Keys. Die Prüfung soll für Topic-Keys entsprechend gelten.

## 8. Beispiel

Beide Dateien liegen im selben Ordner, daher zeigt der Evidence-Pfad auf `evidence/` daneben.

`inc-2026-042.yaml`:

```yaml
topic:
  key: inc-2026-042
  title: "INC-2026-042: Ausnutzung von CVE-2026-XXXX"
  kind: incident
  summary: "Webportal, Remote Code Execution."
events:
  - key: exploit-2026-10-14
    t0: 2026-10-14T08:40:00+02:00
    phase: Exploit
    actor: Angreifer (extern)
    system: wp-portal-01
    action: "Remote Code Execution erfolgreich"
    status: in_progress
    triggered_by:
      - cve-2026-xxxx-veroeffentlicht
    evidence:
      - file: evidence/exploit-2026-10-14/waf-block.png
        type: screenshot
        description: "WAF blockiert nachfolgende Anfragen"
refs:
  - cve-2026-xxxx-veroeffentlicht
```

`cve-2026-xxxx.yaml`:

```yaml
topic:
  key: cve-2026-xxxx
  title: "CVE-2026-XXXX: Deserialisierung im Portal"
  kind: vulnerability
events:
  - key: cve-2026-xxxx-veroeffentlicht
    t0: 2026-10-10T12:00:00+02:00
    phase: Identification
    actor: Hersteller
    action: "Advisory veröffentlicht"
    status: resolved
```

Das Event `cve-2026-xxxx-veroeffentlicht` hat seine Heimat im Topic `cve-2026-xxxx`. Es steht im Topic `inc-2026-042` per `refs:`. Das Event `exploit-2026-10-14` verweist mit `triggered_by` darauf.

## 9. Nicht Teil der Notation

Konten, Rollen, Sitzungen, Login-Sperren und das Audit-Log gehören zum Betrieb (ADR 0005). Ebenfalls nicht enthalten sind Phasenfarben, Ansichten wie Timeline und Graph und die Sichtbarkeit einzelner Events.

## 10. Vorschläge (nicht umgesetzt)

- **`sha256` pro Evidence** als Pflichtfeld (ADR 0008, Punkt 6).
- **Versionsfeld der Notation**, etwa `format: 1` im Block `topic`. Heute gibt es keines. Da unbekannte Felder Fehler sind, braucht jede spätere Änderung eine Anpassung von `models.py`. Mit Versionsfeld wären Änderungen an der Notation erkennbar.
- **JSON-Schema** aus den Pydantic-Modellen für Editoren und die CI (ADR 0008, Punkt 9).
- **Prüfmodus mit Vorschau** (Issue #85): Er zeigt, welche Events angelegt, geändert oder entfernt würden.

## 11. Offene Punkte

- **Topic-Keys über Dateien hinweg:** Die Prüfung fehlt (siehe 7). Sie ist in #85 erfasst.
- **Übergang von der Zeichenkette zur Liste:** Liest der Import `triggered_by: <key>` weiter als Liste mit einem Eintrag, oder bricht er ab? Vorschlag in #87 ist der Übergang bis zur nächsten Notationsversion.
- **Evidence-Pfade relativ zur Topic-Datei:** So ist es heute umgesetzt. Ist das so gewollt, oder soll der Pfad relativ zum Datenordner sein?
