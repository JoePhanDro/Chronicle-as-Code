# ADR 0029: Getypte Entities/IOCs an Events

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-11-08
- **Bezug:** ADR 0008 (Dateien als Quelle der Wahrheit), ADR 0018 (Index), ADR 0021 (sensible Daten/Löschung), ADR 0026 (Muster für Listenfelder), `docs/notation.md` Abschnitt 4, Issues #210, #9, #98, #259

## Kontext

IOCs und Entities existieren bisher nur als Text in Beschreibungen (Issue #9 → #210). Ohne Typ und ohne Normalisierung ist „ dieselbe IP taucht woanders auf?“ nicht beantwortbar. Vergleich mit TheHive/DFIR-IRIS: dort sind Observables/Entities eigene getypte Objekte.

## Entscheidung

1. **Entities leben inline am Event**, als Liste `entities` aus `{type, value}` — wie `tags`, `techniques` und `detection`. Keine eigene Tabelle, kein eigener Datei-Knoten. Begründung: Die Datei bleibt Quelle der Wahrheit (ADR 0008); die Events tragen bereits Listen; der Cross-Event-Lookup („wo kam diese IP vor?“) löst der Index per `json_each(events.entities)` auf. Eine eigene Entity-Tabelle mit Keys wäre bei höchstens 100.000 Events Overhead ohne Mehrwert.
2. **Typen:** `ip`, `hash`, `domain`, `url`, `user`, `host`, `email` — fest (Literal), erweiterbar.
3. **Normalisierung beim Speichern**, damit gleiche Werte gleich aussehen und die Suche greift:
   - `ip`: validiert über `ipaddress`, kanonisch (IPv6 komprimiert)
   - `hash`: hex, Länge 32/40/64/128 (md5/sha1/sha256/sha512), lowercase
   - `domain`, `host`: lowercase, ohne trailing dot, gültige Labels, ≤ 253 Zeichen
   - `url`: **Defanging wird aufgelöst** (`hxxp(s)://` → `http(s)://`, `[:]` → `:`, `[.]` → `.`), nur http/https mit Host, ohne Zugangsdaten; Scheme und Host lowercase, ≤ 2000 Zeichen
   - `email`: lowercase, E-Mail-Muster
   - `user`: getrimmt, 1–100 Zeichen, keine Steuerzeichen
4. **Eine Menge:** `entities` ist wie `tags` eine Menge — sortiert, ohne Duplikate (Schlüssel `(type, value)`), höchstens 100 je Event. Gleiche Werte in verschiedenen Events/Topics sind damit dieselbe Entity.
5. **Index:** Spalte `events.entities` (JSON) über `_ensure_column`, plus Spalte `entities` in der FTS-Tabelle `events_fts` (Rebuild-Mechanismus wie bei `tags`/`comments`), damit die Volltextsuche Entity-Werte findet. Gezieltes Suchen/ Filtern: `EventFilter.entity` per `json_each` auf `$.value`, API `GET /api/v1/events?entity=…`, Filterfeld in der Event-Liste, klickbare Chips auf der Event-Seite.
6. **Merge:** `entities` wird als Menge gemergt wie `refs`/`techniques` (Union beider Seiten, Duplikate egal).
7. **Löschung/sensible Daten (ADR 0021):** Entities sind Vorfallsdaten im Vault wie alles andere. Entfernen läuft über das bestehende Editieren des Events; die Git-Historie behält alte Werte (ADR 0021 Punkt 7 und 12) — kein Sonder-Löschkonzept. `chronicle check --secrets` scannt die Textfelder; Entity-Werte sind getypte IOCs und werden nicht gesondert behandelt.
8. **Alte Dateien bleiben gültig und byte-identisch:** ohne Entities wird das Feld nicht geschrieben; keine neue Formatversion.

## Wirkung auf die Teile

| Teil | Änderung |
|---|---|
| Modell, JSON-Schema, Notation | `Entity`, `Event.entities`, Normalisierung; Schema neu erzeugen |
| Dateien schreiben (`store.py`), Export (`exporter.py`) | Feld nur schreiben, wenn gesetzt |
| Merge-Treiber (`merge.py`) | `entities` in `SET_FIELDS` (Mengen-Merge) |
| Index | Spalte `entities` + FTS-Spalte `entities` (Rebuild bei alter FTS-Tabelle) |
| API | `entities` in EventWrite (Lesen/Schreiben), `entity`-Parameter in `GET /api/v1/events` |
| UI | Textarea im Event-Formular (eine Entity pro Zeile als `Typ: Wert`), Chips auf der Event-Seite, Filterfeld in der Event-Liste, Preset-Feld `entity` |
| Nebenbei behoben | Detection-Only-Edits wurden bisher stillschweigend verworfen (Feld fehlte im Änderungsvergleich); `close-events` übernimmt jetzt tags/detection/entities/comments statt sie zu leeren |
