# ADR 0028: Incident-Klassifikation auf Topic-Ebene

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-11-08
- **Bezug:** ADR 0008 (Dateien als Quelle der Wahrheit), ADR 0018 (Index), `docs/notation.md` Abschnitt 4, `chronicle/merge.py`, Issues #225, #208, #210, #229

## Kontext

Verglichen mit XDR-Incident-Seiten, TheHive/DFIR-IRIS und den Meldepflichten (NIS2, DORA) kennt ein Topic in Chronicle bisher nur `title`, `kind` und `summary`. Alles, was Berichte an Dritte und Priorisierung brauchen, fehlt: Schweregrad, Auswirkungen, TLP für die Weitergabe, Ursache und Maßnahmen.

## Entscheidung

1. **Neuer optionaler Block `incident` am Topic** (an jedem Topic erlaubt, nicht nur `kind: incident`, weil sich `kind` ändern kann):
   - `severity`: `low`, `medium`, `high` oder `critical` — **feste Stufen**, wie `Status` und `Phase`, nicht pro Instanz konfigurierbar.
   - `tlp`: `CLEAR`, `GREEN`, `AMBER` oder `RED` (Traffic Light Protocol) für die Weitergabe von Berichten.
   - `impact`: Freitext (max. 2000 Zeichen).
   - `affected_systems`, `data_categories`: Listen (max. 50 Einträge à 100 Zeichen), gespeichert sortiert und ohne Doppelte wie `tags`.
   - `affected_persons`: geschätzte Zahl der Betroffenen, int ≥ 0, optional.
   - `cause`: Grundursache, Freitext (max. 2000 Zeichen).
   - `measures`: Eindämmung/Maßnahmen, kurz, Freitext (max. 2000 Zeichen).
2. **Alles optional.** Ein leerer Block wird nicht geschrieben: leere Felder tauchen nicht in der Datei auf, und eine Datei ohne Block bleibt byte-identisch. Das Format bekommt keine neue Version; ältere Chronicle-Versionen lehnen Dateien mit dem Block wegen `extra="forbid"` ab.
3. **Klassifizieren dürfen Writer-Rollen** (admin, analyst) wie alle anderen Topic-Inhalte; Viewer lesen nur. Nichts ist Pflicht im Meldebericht — gesetzte Felder erscheinen im PDF (Kopf-Tabelle, TLP-Banner, Fußzeile statt „Vertraulich") und im HTML-Bericht (Kopfzeile).
4. **Merge-Treiber:** `incident` wird feldweise three-way gemergt wie `title`/`kind`/`summary`. Dabei werden auch `reporting` und `notifications` in die Topic-Felder des Treibers aufgenommen (bisher gingen die bei einem Merge stillschweigend verloren). Gleiche Feldwahl auf beiden Seiten: Konflikt. Unterschiedliche Felder: konfliktfrei.

## Wirkung auf die Teile

| Teil | Änderung |
|---|---|
| Modell, JSON-Schema, Notation | Block und Prüfungen nach 1./2.; Schema neu erzeugen (`python -m chronicle schema`) |
| Dateien schreiben (`store.py`), Export (`exporter.py`) | Block nur schreiben, wenn gesetzt (`stored()` lässt leere Felder weg) |
| Merge-Treiber (`merge.py`) | `incident`, `reporting`, `notifications` in `TOPIC_FIELDS` und `_apply_topic` |
| Index | Spalte `incident` (JSON) über `_ensure_column`; keine Migration |
| API | Voller Block in Lesen/Schreiben, da `Topic` das Modell erbt |
| UI | Formular-Block auf der Topic-Seite (Writer), Badges auf Topic-Seite und in der Liste, Schweregrad-Filter in der Liste, Anzeige der gesetzten Felder auf der Topic-Seite |
| Berichte | PDF: TLP-Banner oben, Klassifikations-Tabelle im Kopf, Fußzeile `TLP:<stufe>` statt „Vertraulich“; HTML-Bericht: Kopfzeile mit den Feldern und TLP-Badge |
