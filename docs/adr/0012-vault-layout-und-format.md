# ADR 0012: Vault-Layout und Dateiformat, ein Event eine Datei

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0002 (Events und Topics), ADR 0008, `docs/notation.md`, `chronicle/models.py`, `chronicle/merge.py`
- **Ändert:** `docs/notation.md`, Abschnitte 2, 3 und 5a (Format 1). Format 0 (heutige Topic-Dateien) bleibt lesbar (ADR 0022).

## Kontext

Heute steht ein Topic mit all seinen Events und `refs` in **einer** YAML-Datei. Unter Zusammenarbeit wirkt das an drei Stellen schlecht:

1. **Zusammenführen auf dem Server.** Der Merge-Treiber auf Feldebene (`chronicle/merge.py`) läuft nur lokal. GitHub führt Pull Requests mit eigenem Merge zusammen und kennt keine selbst definierten Treiber. Zwei Pull Requests, die je ein Event an dieselbe Topic-Datei anhängen, kollidieren dort als Textkonflikt. Das ist der häufigste Fall überhaupt.
2. **Prüfung.** Eine Änderung an einem Event zeigt im Diff eine Datei, in der auch alle anderen Events stehen. Review, `git blame` und `git log` pro Event sind umständlich.
3. **Doppelte Keys** zwischen zwei Dateien erzeugen keinen Git-Konflikt (ADR 0008, Punkt 5), sondern fallen erst in der CI auf.

## Optionen

1. **Topic-Datei beibehalten** (Format 0). Keine Migration. Die Konflikte bleiben.
2. **Ein Event eine Datei, Topic als eigene Datei.** Hinzufügen und Ändern verschiedener Events berührt nie dieselbe Datei. Mehr Dateien: 10.000 Events sind 10.000 Dateien, für Git unkritisch. Die Weboberfläche von GitHub kürzt Ordnerlisten ab etwa 1.000 Einträgen. Sie ist aber nicht der Arbeitsweg.
3. **Anderes Format (JSON, TOML, Markdown mit Front Matter).** YAML ist eingeführt, kommentierbar und hat Round-Trip-Werkzeug (`ruamel.yaml`). Markdown mit Front Matter wäre für `details` angenehm, ist aber ein zweiter Parser.

## Entscheidung

**Option 2, Format YAML.** Layout von Format 1:

```
<vault>/
  vault.yaml                      Manifest (Pflicht)
  topics/
    <topic-key>/
      topic.yaml                  das Topic
      events/
        <event-key>.yaml          genau ein Event, dessen Heimat dieses Topic ist
  evidence/
    <event-key>/<dateiname>       Anhänge (ADR 0019)
  .github/                        CODEOWNERS, Workflow, PR-Vorlage (aus der Vorlage)
  .gitignore                      .chronicle.lock, *.tmp
```

Regeln:

1. **Die Heimat steht im Pfad.** Das Event gehört zum Topic, in dessen Ordner es liegt. Es gibt kein Feld `topic` in der Event-Datei, also keinen Widerspruch zwischen Pfad und Inhalt. Ein Wechsel der Heimat ist ein `git mv`, das die Anwendung ausführt.
2. **Dateiname gleich Key.** `events/<key>.yaml`, und `key` steht zusätzlich in der Datei. Beides muss übereinstimmen (Prüfregel). Das Muster des Keys (`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`) schließt Pfadtricks aus. Dateisysteme ohne Unterscheidung von Groß- und Kleinschreibung (macOS, Windows) behandeln `Ev-1` und `ev-1` als dieselbe Datei. Die Prüfung meldet Keys, die sich nur in der Schreibweise unterscheiden, als Fehler.
3. **Weitere Topics stehen am Event.** Das Feld `also_in` in der Event-Datei listet die Keys weiterer Topics. Es ersetzt `refs:` in der Topic-Datei. Wer ein Event einem Topic zuordnet, ändert nur die Event-Datei. Zwei Personen, die verschiedene Events verknüpfen, berühren verschiedene Dateien.
4. **Die Topic-Datei enthält nur Topic-Felder:** `key`, `title`, `kind`, `summary`. Der Ordnername ist der Key.
5. **Die Felder der Events** sind gleich wie in Format 0 (`t0`, `phase`, `actor`, `system`, `action`, `status`, `triggered_by`, `details`, `evidence`). `evidence[].sha256` wird Pflicht (ADR 0019).
6. **Manifest `vault.yaml`:**
   ```yaml
   format: 1                     # Pflicht, ganze Zahl
   id: 6f1c0c7e-...              # Pflicht, UUID, Etikett (siehe unten)
   name: "Beispiel GmbH"         # Pflicht, Anzeigetext
   limits:                       # optional, siehe unten
     evidence_total_mib: 200
   ```
   - **Die `id` im Manifest ist nur ein Etikett.** Sie steht im Vault und ist damit vom Vault kontrollierbar: Eine Kopie, ein Fork oder ein Template trägt dieselbe `id`, ein böswilliger Vault kann eine fremde `id` behaupten. Die Anwendung vergibt deshalb beim Eintragen eine **lokale Installations-ID** (ADR 0013). Sie ist Schlüssel für Cache, Audit und URL. Zwei Registry-Einträge mit gleicher Manifest-`id` werden abgelehnt (Hinweis auf Kopie oder Fork). `vault init` erzeugt immer eine neue `id`.
   - **`name` ist reiner Anzeigetext**, Bidi- und Steuerzeichen werden abgelehnt. In der Oberfläche gilt der Name aus der Registry, nicht der aus dem Manifest.
   - **`limits` darf die Obergrenzen der Anwendung nur senken**, nie anheben. Die harten Obergrenzen (Dateigrößen, Anzahl, Tiefe, ADR 0021) stehen im Code der App. Alles, was Verhalten auf dem Arbeitsplatz verändert (Workflow, Speicherart der Anhänge), kommt aus der lokalen Registry und nicht aus dem Manifest (ADR 0015, Punkt 16).
7. **Unbekannte Felder bleiben Fehler** (striktes Schema), in allen drei Dateiarten. `chronicle schema` gibt je ein JSON-Schema für Topic- und Event-Datei aus.
8. **Reihenfolge:** Events werden nach `t0`, dann `key` sortiert angezeigt. Die Dateien selbst haben keine Reihenfolge. `ruamel.yaml` bewahrt Kommentare und Layout beim Ändern, wie heute.
9. **Prüfregeln** (Erweiterung von `docs/notation.md`, Abschnitt 7): Dateiname gleich Key, Ordner `topics/<key>/` gleich `topic.key`, Event-Keys eindeutig im Vault (ohne Beachtung der Schreibweise), `also_in` und `triggered_by` lösen auf, kein Kreis in `triggered_by`, keine symbolischen Links und Submodule, keine unbekannten Dateien unter `topics/`, **keine unbekannten Dateien oder Ordner in der Vault-Wurzel** (erlaubt: `vault.yaml`, `topics/`, `evidence/`, `.github/`, `.gitignore`, `.gitattributes`, `README.md`, `docs/`, `.chronicle-secrets-allow`; sonst könnte eine `yaml.py` in der Wurzel ausgeführt werden, ADR 0021, Punkt 17), Dateigrößen begrenzt und strenger YAML-Vorabscan (ADR 0021). Keys, die sich nur in der Schreibweise unterscheiden, werden auch **lokal** geprüft, nicht nur in der CI (Dateisysteme von macOS und Windows).

Beispiel `topics/inc-2026-042/events/exploit-2026-10-14.yaml`:

```yaml
key: exploit-2026-10-14
t0: 2026-10-14T08:40:00+02:00
phase: Exploit
actor: Angreifer (extern)
system: wp-portal-01
action: "Remote Code Execution erfolgreich"
status: in_progress
triggered_by: [cve-2026-xxxx-veroeffentlicht]
also_in: [cve-2026-xxxx]
evidence:
  - file: evidence/exploit-2026-10-14/waf-block.png
    type: screenshot
    sha256: "…"
```

## Konsequenzen

- Ein Pull Request, der ein Event hinzufügt oder ändert, ist ein kleiner Diff in einer Datei. Der häufige Fall (zwei Personen legen verschiedene Events an) ergibt auf GitHub keinen Konflikt mehr.
- Zwei Personen, die denselben Key neu anlegen: im selben Topic ein Add/Add-Konflikt auf dem Pfad, in verschiedenen Topics fängt die Pflichtprüfung den Duplikat-Key vor dem Merge. Voraussetzung ist, dass die Prüfung als erforderlicher Status eingerichtet ist (ADR 0015). Zwei PRs, die je für sich grün sind, aber zusammen einen Duplikat-Key ergeben, fängt erst die Einstellung „Branch muss aktuell sein“ oder eine Merge Queue (ADR 0015).
- **`also_in` umgeht CODEOWNERS des Ziel-Topics.** Wer ein Event in ein fremdes Topic einhängt, ändert nur die Event-Datei im eigenen Topic-Ordner, und der Besitzer des Ziel-Topics wird nicht automatisch gefragt. Das ist eine Grenze des Modells: **Vertraulichkeit und Besitz gelten pro Vault, nicht pro Topic.** Die Vault-CI meldet jede Änderung an `also_in` als Hinweis mit Ziel-Topic und dessen Code-Owner, damit der Prüfer sie hinzuziehen kann. Erzwingen kann die CI das nicht, weil sie ohne Token die Freigaben nicht abfragen darf (ADR 0021, Punkt 26).
- Nach einem Wechsel der Heimat ändern sich die Anhangs-Pfade nicht, weil sie unter `evidence/<event-key>/` liegen und nicht unter dem Topic. Nur die Event-Datei zieht um.
- Der Merge-Treiber vereinfacht sich: Es gibt nur noch ein Event je Datei und das Topic, keine Listen von Events mehr (ADR 0016).
- Index und Sync, die heute Dateien pro Topic annehmen (`source_files`, `event_files`, `topic_files`), arbeiten je Event-Datei. Das passt zum inkrementellen Index (#125).
- Eine Änderung schreibt eine kleine Datei und nicht die Datei mit allen Events (Kern von Issue #125).
- Vaults in Format 0 funktionieren weiter, bis sie migriert sind (ADR 0022).
- Ein Topic ohne Events ist erlaubt (nur `topic.yaml`).

## Offene Punkte

- Ob `also_in` besser `topics` heißen soll. Der Name steht hier nur, damit das Beispiel lesbar ist.
- Ob Events mit sehr langem `details` in eine zweite Datei (`<key>.md`) ausgelagert werden. Bis zum Bedarf: nein.
