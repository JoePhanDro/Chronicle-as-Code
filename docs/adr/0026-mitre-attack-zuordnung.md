# ADR 0026: MITRE-ATT&CK-Zuordnung bei Events

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-28
- **Bezug:** ADR 0008 (Dateien als Quelle der Wahrheit), ADR 0018 (Index als abgeleiteter Zustand), `docs/notation.md` Abschnitt 4, `chronicle/merge.py`, Issues #209, #9

## Kontext

Für Incident Response ist die Einordnung eines Events in MITRE ATT&CK ein naheliegender Mehrwert: Berichte sprechen dieselbe Sprache wie andere Teams, und man kann fragen „wo kam diese Technik im Vorfall vor?“. Chronicle kennt dazu nichts. Heute steht eine Technik höchstens im Freitext.

Die Zuordnung ist eine Angabe am Event, kein eigener Knoten wie ein Akteur (ADR 0002).

## Entscheidung

1. **Neues optionales Feld `techniques` am Event:** eine Liste von Technik-IDs, z. B. `[T1190, T1059.001]`. Beliebig viele, höchstens 50 je Event.
2. **Format:** `T` und vier Ziffern, optional ein Punkt und drei Ziffern für die Unter-Technik (`^T\d{4}(\.\d{3})?$`). Das deckt Enterprise, Mobile und ICS ab. Eingaben werden getrimmt und in Großbuchstaben gewandelt. Alles andere ist ein Prüffehler.
3. **Eine Menge:** Reihenfolge und Wiederholungen haben keine Bedeutung. Gespeichert wird sortiert und ohne Doppelte (wie `triggered_by`).
4. **Nur die ID wird gespeichert, kein Name.** Chronicle liefert keinen ATT&CK-Katalog mit und prüft nicht, ob die ID existiert. Gründe: Der Katalog hat Versionen, in denen IDs entstehen, wegfallen und umbenannt werden, die Datei dürfte nicht davon abhängen. Ein Katalog wäre Fremdinhalt mit eigenen Nutzungsbedingungen und müsste gepflegt werden. Die Datei bleibt aus sich selbst lesbar und prüfbar.
5. **Anzeige:** Die ID steht als Link auf die Seite der Technik bei attack.mitre.org (`T1059.001` wird zu `/techniques/T1059/001/`). Der Link ist rein dem Klick vorbehalten, Chronicle lädt nichts von dort. Ein Name kann später aus einer optionalen, lokalen Katalogdatei kommen. Das ist nicht Teil dieser Entscheidung.
6. **Alte Dateien bleiben gültig und byte-gleich.** Ohne Technik schreibt Chronicle das Feld nicht. Das Format bekommt keine neue Version. Ältere Chronicle-Versionen lehnen Dateien mit dem Feld ab (`extra="forbid"`), sie sollen nichts still verlieren.

## Wirkung auf die Teile

| Teil | Änderung |
|---|---|
| Modell, JSON-Schema, Notation | Feld und Prüfung nach 2. und 3. Das Schema wird neu erzeugt (`python -m chronicle schema`). |
| Dateien schreiben (`store.py`), Export | Feld nur schreiben, wenn gesetzt. |
| Merge-Treiber | `techniques` wird als Menge zusammengeführt wie `refs`: Eine Seite fügt hinzu, die andere entfernt eine andere: beides gilt. Wer dieselbe Technik auf einer Seite entfernt und auf der anderen belässt, entfernt sie. Es entsteht kein Konflikt. |
| Index | Spalte `techniques` (JSON-Liste, wie `triggered_by`) über `_ensure_column`. Keine Migration, vor dem Update kann keine Datei das Feld haben. Filter über `json_each`. |
| API | `techniques` in Lesen und Schreiben, da `EventWrite` das Modell erbt. Ein Schreiben ohne das Feld leert es (wie bei den übrigen Feldern). |
| Formular, Anzeige | Eingabe als kommagetrennte Liste mit Prüfung, Chips mit Link am Event, in Liste und Dashboard. Filter nach Technik in der Ereignisliste. |
| Bericht (PDF, HTML) | Techniken je Event, dazu eine Übersicht „Techniken im Vorfall“ mit Zahl der Events. |

## Vorgehen in Schritten

Jeder Schritt ist ein Pull Request, die Tests bleiben grün.

1. **Daten:** Modell, Schema, Notation, Schreiben, Merge, Index, API, Export. Dazu dieses ADR.
2. **Eingabe und Anzeige:** Formular, Event-Seite, Liste, Filter nach Technik.
3. **Bericht:** Techniken im PDF und im HTML-Bericht samt Übersicht.

## Alternativen

- **Katalog mitliefern und gegen ihn prüfen.** Verworfen für jetzt (Punkt 4). Lässt sich nachrüsten, ohne das Dateiformat zu ändern.
- **Freie Tags statt eines festen Formats.** Verworfen: Ohne Format lassen sich Techniken nicht zuverlässig suchen, filtern und verlinken.
- **Technik und Taktik (z. B. „Initial Access“) speichern.** Die Taktik folgt aus der Technik. Sie müsste aus einem Katalog kommen und würde veralten. Die Phasen (`phase`) sind Chronicles eigenes Modell und bleiben getrennt.
- **Technik als eigener Knoten mit Seite je Technik.** Mehr Aufwand ohne Mehrwert, solange die Filter und die Übersicht reichen.

## Konsequenzen

- Zuordnungen sind prüfbar im Format, suchbar und im Bericht lesbar, ohne Abhängigkeit von einer ATT&CK-Version.
- Eine Tippfehler-ID, die dem Format entspricht (z. B. `T9999`), wird nicht erkannt. Das ist ein bewusster Preis ohne Katalog.
- Dateien mit dem neuen Feld lesen ältere Versionen nicht. Alle Kopien sollten zuvor aktualisiert sein.

## Offen

- Ob ein optionaler lokaler Katalog (Namen, Taktiken) gewünscht ist, und aus welcher Quelle er käme.
- Ob die Technik-IDs in der Volltextsuche (`/suche`) auffindbar sein sollen. In dieser Fassung gibt es den Filter in der Ereignisliste.
