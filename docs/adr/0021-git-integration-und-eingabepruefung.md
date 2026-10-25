# ADR 0021: Git-Integration, sicherer Aufruf und Prüfung fremder Vault-Inhalte

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0011, ADR 0015, ADR 0016, `chronicle/sync.py` (`_run`, `_git`), `chronicle/loader.py` (`find_topic_files`, `collect_evidence`), `chronicle/store.py`

## Kontext

Heute startet `sync.py` Git über `subprocess.run([...], capture_output=True)` mit einer Argumentliste (ohne Shell). Das ist die richtige Grundform. Es gibt aber noch keine einheitliche Stelle, keine Zeitlimits, keine Umgebungskontrolle und keine Prüfung von URLs oder Branch-Namen, weil bisher nur der eigene Ordner angefasst wurde. Mit Vaults kommen Inhalte hinzu, die andere Personen geschrieben haben (und die ein Prüfer nur teilweise gesehen hat). Auch ein kompromittiertes Konto im Team kann Inhalte einspielen.

## Optionen für die Git-Anbindung

1. **Aufruf des `git`-Programms** (Shell-out, wie heute). Alle Fähigkeiten (Merge-Treiber, Signieren, SSH, Credential Helper des Betriebssystems, Teilklone). Ausgabe muss geparst werden.
2. **libgit2 (`pygit2`).** Kein Prozess. Kennt keine selbst definierten Merge-Treiber, Credential Helper und SSH-Signaturen nur eingeschränkt. Native Bibliothek als Abhängigkeit mit Binär-Wheels.
3. **Dulwich (reines Python).** Keine Abhängigkeit zu nativem Code, aber kaum Merge-Unterstützung.
4. **GitPython.** Ist ein Wrapper um Option 1 mit zusätzlicher Angriffsfläche und eigener Verwundbarkeitshistorie. Kein Gewinn.

## Entscheidung

**Option 1, in einem einzigen Modul `chronicle/git.py`.** Nirgends sonst im Code wird `git` gestartet (ein Test sucht nach `subprocess` außerhalb dieses Moduls). Das Modul erzwingt:

1. **Programm.** Der Pfad zu `git` wird beim Start einmal aufgelöst (`shutil.which`), die Mindestversion geprüft (Vorschlag 2.39) und festgehalten. Fehlt Git, sagt die App das ausdrücklich.
2. **Aufruf.** Immer eine Liste, nie `shell=True`. Immer `git -C <vault-pfad>` mit einem Pfad aus der geprüften Registry (ADR 0013). Werte aus Benutzereingaben stehen nie an der Stelle einer Option: Pfade nach `--`, Refs nach `--end-of-options` oder durch eine Prüfung gegen ein festes Muster (Branch `^chronicle/[a-z0-9._-]{1,64}/[a-z0-9._-]{1,64}$`, Keys wie in ADR 0012).
3. **Umgebung.** Eine ausdrückliche Auswahl: `PATH`, `HOME`, `SSH_AUTH_SOCK`, `LANG`/`LC_ALL=C`, dazu `GIT_TERMINAL_PROMPT=0`, `GIT_ALLOW_PROTOCOL=https:ssh`, `GIT_CONFIG_NOSYSTEM` nicht gesetzt (Credential Helper des Systems sollen funktionieren). Autor und Committer aus ADR 0014 als `GIT_AUTHOR_*`/`GIT_COMMITTER_*`. Variablen wie `GIT_DIR`, `GIT_SSH_COMMAND` oder `GIT_EXTERNAL_DIFF` aus der Umgebung der App werden nicht weitergegeben.
4. **Feste Konfiguration pro Aufruf** (`-c`): `protocol.ext.allow=never`, `protocol.file.allow=never` (Tests setzen es ausdrücklich), `core.hooksPath=<leerer Ordner>` (keine Hooks aus `init.templateDir` oder globaler Konfiguration), `core.fsmonitor=false`, `submodule.recurse=false`, `transfer.fsckObjects=true`, `fetch.fsckObjects=true`.
5. **Klonen.** `git clone --no-recurse-submodules -c core.symlinks=false -- <url> <pfad>`. Mit `core.symlinks=false` legt Git symbolische Links als gewöhnliche Dateien mit dem Linkziel als Inhalt an. Die Konfiguration wird mit `git config --local` im Klon festgeschrieben.
6. **URLs.** Erlaubt sind `https://host/pfad` und `ssh://[user@]host/pfad` sowie die SCP-Form `user@host:pfad`. Abgelehnt: `ext::`, `file:`, `git:`, `http:`, Zugangsdaten in der URL (`user:token@`), Steuerzeichen, Leerzeichen, ein führendes `-`. Eine optionale Liste erlaubter Hosts steht in der Registry (`allowed_hosts`). Fehlermeldungen und Logs entfernen `://…@` aus den Texten.
7. **Zeitlimits und Größen.** Netzwerkbefehle 60 Sekunden, lokale 10 Sekunden (einstellbar). Beim Überschreiten wird die Prozessgruppe beendet. Die Ausgabe ist auf 16 MiB begrenzt.
8. **Ausgaben parsen nur mit stabilen Formaten:** `-z`, `--porcelain`, `--name-status`, `rev-parse`. Nie die menschenlesbare Ausgabe.
9. **Keine Git-Konfiguration des Benutzers ändern.** Nur lokale Konfiguration des Klons (`--local`), nur die Einträge für Merge-Treiber (`merge.chronicle.*`, `.git/info/attributes`) und `core.symlinks`.
10. **Fehler.** Ein eigener Ausnahmetyp (`GitError`, heute `SyncError`) mit bereinigter Meldung. Die rohe Ausgabe von Git geht nur ins Protokoll auf Debug-Stufe.

### Prüfung von Inhalten aus dem Vault (Eingabe ist nicht vertrauenswürdig)

Alles, was aus einem Vault gelesen wird, gilt als Eingabe von außen, auch wenn ein Prüfer sie gesehen hat. Die gemeinsame Prüfung (`chronicle check`, Schreibpfad, Index, Merge-Treiber) erzwingt:

11. **Keine symbolischen Links und keine Submodule.** `check` listet `git ls-tree -r` und weist Modus `120000` (Link) und `160000` (Submodul) sowie `.gitmodules` ab. `find_topic_files` und `collect_evidence` weisen Links im Dateisystem mit `lstat` ab. Heute folgt `rglob` Dateilinks und `resolve()` lässt Ziele im Vault zu. Ein Link auf `~/.ssh/id_ed25519` unter dem Namen `x.yaml` würde gelesen und sein Inhalt könnte in einer Fehlermeldung erscheinen. Das ist eine Lücke im Ist-Zustand und der erste Plan-Teil.
12. **Größenbegrenzung vor dem Lesen:** Event- und Topic-Datei höchstens 256 KiB, `vault.yaml` 16 KiB, Gesamtzahl der Dateien unter `topics/` höchstens 200.000. Die Größe wird mit `stat` geprüft, bevor gelesen wird.
13. **YAML ohne Aliase, Anker, Tags und ohne doppelte Schlüssel.** PyYAML nimmt bei doppelten Schlüsseln stillschweigend den letzten, ein Prüfer sähe im Diff den ersten Wert. Das ist ein Mittel zur Täuschung bei der Freigabe. Der Lader baut dafür einen strikten Parser (Ereignisstrom prüfen) und lehnt alles andere mit Dateiname und Zeile ab. Aliase gestatten außerdem Vervielfachung (Billion-Laughs).
14. **Zeichen.** Steuerzeichen (außer Zeilenumbruch und Tab) und Bidi-Steuerzeichen (U+202A bis U+202E, U+2066 bis U+2069) sind in allen Textfeldern verboten. Sie täuschen im Diff eine andere Reihenfolge vor.
15. **Pfade.** Dateiname und Ordner entsprechen dem Key-Muster (ADR 0012). Anhangspfade enthalten kein `..`, kein Laufwerk, keinen führenden `/` und müssen unter `evidence/` liegen (Format 1). Der Schreibpfad löst Pfade nie aus Eingabe zusammen, ohne `is_relative_to` zu prüfen.
16. **Ausgabe.** Markdown wird bereinigt (`nh3`, `html: False`, wie heute). Anhänge werden mit `Content-Security-Policy: sandbox` und `nosniff` ausgeliefert (ADR 0004). Fehlermeldungen zeigen Dateiname und Feld, nicht ungeprüfte Dateiinhalte über eine Zeile hinaus.

### Der Merge-Treiber ist eine Ausführung von Code aus der Konfiguration

17. `merge.chronicle.driver` zeigt auf `sys.executable -m chronicle.merge`. Das Kommando kommt aus der App, nie aus dem Vault. Eine `.gitattributes` im Vault darf `merge=chronicle` nennen, aber kein anderes Programm festlegen, weil Treiber nur aus der lokalen Konfiguration kommen. Die App prüft, dass `.gitattributes` im Vault keine `filter=`- oder `diff=`-Zeilen mit eigenen Programmen nennt (sie bleiben ohne lokale Konfiguration wirkungslos). Der Treiber liest Eingaben mit denselben Grenzen wie `check`.

## Vault-CI

18. Die Prüfung im Vault läuft als **wiederverwendbarer Workflow** des Code-Repositorys, **per Commit-SHA angeheftet**, mit `on: pull_request` (nicht `pull_request_target`), `permissions: contents: read` und ohne Secrets. Sie installiert die Abhängigkeiten aus `requirements.txt` des Code-Repositorys (nicht aus dem Vault) und führt `chronicle check --secrets <vault>` aus. Ein Pull Request aus einem Fork bekommt damit weder Schreibrechte noch Geheimnisse.

## Konsequenzen

- Eine Abhängigkeit weniger (kein libgit2), aber die Pflicht, Aufrufe sauber zu halten. Der Test „kein `subprocess` außerhalb von `git.py`“ verhindert Abkürzungen.
- `core.symlinks=false` ist für Klone, die die App anlegt, gesetzt. Bei `vault add` auf einen vorhandenen Klon wird es nachträglich gesetzt (`git config --local`), und die Prüfung nach Punkt 11 gilt unabhängig davon.
- Die Mindestversion von Git ist ein Betriebsdetail der Installation.
- Windows ist nicht Ziel (ADR 0013).

## Offene Punkte

- Mindestversion von Git (Vorschlag 2.39).
- Ob `allowed_hosts` Pflicht werden soll. Vorläufig optional.
