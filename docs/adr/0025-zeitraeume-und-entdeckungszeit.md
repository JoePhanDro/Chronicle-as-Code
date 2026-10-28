# ADR 0025: Zeiträume und Entdeckungszeit bei Events

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-28
- **Bezug:** ADR 0008 (Dateien als Quelle der Wahrheit), ADR 0018 (Index als abgeleiteter Zustand), `docs/notation.md` Abschnitt 4, `chronicle/merge.py` (ADR 0008, Punkt 3), Issues #208, #9

## Kontext

Ein Event hat heute genau einen Zeitpunkt, `t0`. Für Incident Response reicht das nicht:

- Vieles hat eine **Dauer**: ein Angreifer ist drei Tage im Netz, ein Dienst ist zwei Stunden gestört, eine Maßnahme läuft über eine Nacht. Als Punkt dargestellt, geht die Länge verloren.
- Der **tatsächliche Zeitpunkt** und die **Entdeckung** fallen auseinander. Der Einbruch geschah am 3., aufgefallen ist er am 24. Der Abstand (die Verweildauer) ist eine der wichtigsten Kennzahlen eines Vorfalls und lässt sich heute nicht ausdrücken.

Behelfe gibt es: zwei Events für Beginn und Ende, ein eigenes Event in der Phase `Identification` für die Entdeckung. Beides trägt die Beziehung nur im Text. Sortierung, Zeitleiste und Bericht kennen sie nicht.

Die Zeitzone für Eingabe und Anzeige ist bereits wählbar. Das Zeitmodell selbst (ISO 8601 mit Offset, gespeichert als UTC) bleibt, wie es ist.

## Entscheidung

1. **`t0` bleibt der tatsächliche Zeitpunkt** und der einzige Pflicht-Zeitwert. Es ist der Beginn des Events. Sortierung und Index-Schlüssel ändern sich nicht (Notation, Abschnitt „Reihenfolge“).
2. **`t1` (optional) ist das Ende eines Zeitraums.** Ein Event mit `t1` ist ein Zeitraum von `t0` bis `t1`, ohne `t1` ein Zeitpunkt. Es gilt `t1 >= t0`, Verstoß ist ein Prüffehler. Gleichheit ist erlaubt und bedeutet dasselbe wie kein `t1`.
3. **`detected_at` (optional) ist der Zeitpunkt, an dem das Event bemerkt wurde.** Es gibt keine Bedingung gegenüber `t0`: Schätzungen und Uhrenabweichungen sind real, die Prüfung soll sie nicht verbieten. Normalfall ist `detected_at >= t0`. Liegt es davor, zeigt die Anzeige einen Hinweis statt eines Fehlers.
4. **Format:** beide Felder wie `t0`: ISO 8601 mit Zeitzone, gespeichert als UTC. Ein Wert ohne Zeitzone wird abgewiesen.
5. **Alte Dateien bleiben gültig und byte-gleich.** Fehlen die Felder, schreibt Chronicle sie auch nicht (kein `t1: null`). Das Format bekommt keine neue Version, denn neue optionale Felder sind abwärtsverträglich. Ein älteres Chronicle lehnt Dateien mit den neuen Feldern ab (`extra="forbid"`). Das ist gewollt: Es soll nichts still verlieren.
6. **Abgeleitet, nicht gespeichert:** Dauer (`t1 - t0`) und Verweildauer (`detected_at - t0`). Sie entstehen bei Anzeige, im Bericht und in der API. Gespeicherte Dauerwerte würden mit den Zeitpunkten auseinanderlaufen.
7. **Keine offenen Zeiträume.** Ein noch laufendes Ereignis hat kein `t1`. Dafür gibt es den Status `in_progress`. Ein Event, dessen Ende erst später bekannt wird, bekommt `t1` bei diesem Wissensstand nachgetragen.

## Wirkung auf die Teile

| Teil | Änderung |
|---|---|
| Modell, Schema, Notation | zwei optionale Felder, die Prüfung nach 2. Das JSON-Schema (`schema/timeline.schema.json`) und `docs/notation.md` werden im selben Schritt angepasst. |
| Dateien schreiben (`store.py`) | Felder nur schreiben, wenn gesetzt. Beim Ändern bleibt der Rest der Datei unverändert. |
| Index | zwei nullable Spalten in `events`, ergänzt von `db._ensure_column` wie bei früheren Erweiterungen. Es gibt keine Datenmigration und keinen Neuaufbau: Vor dem Update kann keine Datei die Felder enthalten, NULL ist also für alle vorhandenen Zeilen richtig. |
| Merge-Treiber | keine Codeänderung: `EVENT_FIELDS` folgt dem Modell, die Felder werden je Feld zusammengeführt. Ändern beide Seiten dasselbe Feld verschieden, ist das ein Konflikt wie sonst. Ein Ergebnis, das `t1 < t0` verletzt, scheitert an der Prüfung nach dem Merge, der Abgleich macht ihn rückgängig und nennt das Problem. |
| Formular, Detailansicht | im Formular zwei optionale Felder „Ende“ und „Entdeckt“ mit derselben Zeitzone wie die Eingabe von `t0`. Die Detailansicht nennt Dauer und Verweildauer. |
| Zeitleiste | Events mit `t1` erscheinen als Balken (`end` in vis-timeline), alle anderen wie bisher als Punkt. `detected_at` wird in dieser Fassung nicht in der Zeitleiste gezeichnet. |
| Liste, Suche, Filter | Datumsfilter und Sortierung gelten weiter für `t0`. Ein Filter nach Überschneidung mit einem Zeitraum ist nicht Teil dieser Fassung. |
| Bericht (PDF), Export, API | zeigen `t1`, `detected_at`, Dauer und Verweildauer, wenn gesetzt. Die API gibt die Felder immer aus (`null`, wenn leer). |
| Import (`importer`) | liest die Felder aus den Dateien wie die übrigen. |

## Vorgehen in Schritten

Jeder Schritt ist ein eigener Pull Request, die Tests bleiben grün.

1. **Dieses ADR.** Keine Codeänderung.
2. **Daten:** Modell mit Prüfung, JSON-Schema, Notation, Schreiben ohne leere Felder, Index-Spalten (`_ensure_column`), Export. Tests: Alt-Dateien byte-gleich, `t1 < t0` abgewiesen, Zeitzone Pflicht, Merge verschiedener neuer Felder ohne Konflikt.
3. **Eingabe und Anzeige:** Formular, Detailansicht, API.
4. **Zeitleiste und Bericht:** Balken in der Zeitleiste (mit Browser-Test), Dauer und Verweildauer im PDF.

## Alternativen

- **Dauer statt `t1` speichern.** Verworfen: Eine Dauer ohne Ende lässt sich bei Zeitumstellungen und Zeitzonen falsch lesen, und das Ende ist das, was Analysten wissen und lesen.
- **Beginn und Ende als zwei Events.** Das bleibt möglich, trägt aber die Zusammengehörigkeit nur im Text. Zeitleiste und Bericht können daraus keinen Zeitraum machen.
- **Entdeckung als eigenes Event der Phase `Identification`.** Bleibt für die Entdeckung als Handlung richtig (wer hat es gesehen, womit). Das neue Feld beantwortet eine andere Frage: wann wurde **dieses** Event bemerkt, und wie groß ist der Abstand.
- **`t0` zum Beginn und Pflicht-`t1` machen.** Verworfen: Alle bestehenden Dateien würden ungültig, und die meisten Events sind Punkte.

## Konsequenzen

- Zeiträume und Verweildauer sind prüfbar und im Bericht lesbar, ohne die Notation für bestehende Dateien zu ändern.
- Dateien mit den neuen Feldern lassen sich von älteren Versionen nicht lesen. Die App sollte vor dem Einsatz in allen Kopien aktualisiert sein, sonst scheitert dort die Prüfung.
- Wer `t1` später ändert, erzeugt einen normalen Datei-Commit. Die Änderung ist in Git und im Audit-Log sichtbar.

## Offen

- **Namen:** `t1` passt zu `t0`, `detected_at` folgt dem Stil der englischen Feldnamen. Alternativ `t_end` und `t_detected`. Zu bestätigen.
- Ob die Entdeckungszeit in der Zeitleiste ein eigener Marker sein soll (z. B. Linie vom Ereignis zur Entdeckung).
- Ob ein Filter „überschneidet Zeitraum“ gebraucht wird, sobald Zeiträume in Gebrauch sind.
- Ob die Prüfung bei `detected_at < t0` mehr als einen Hinweis in der Anzeige leisten soll (z. B. eine Warnung in `check`).
