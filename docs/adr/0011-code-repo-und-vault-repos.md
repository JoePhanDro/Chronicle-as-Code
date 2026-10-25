# ADR 0011: Code-Repository und Vault-Repositories, Dateimodus als einziger Schreibpfad

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-10-25
- **Bezug:** ADR 0008 (Dateien als Quelle der Wahrheit), ADR 0009 (CLI), ADR 0010 (Betriebsdaten), Issue #123 (zwei Schreibpfade), `docs/architecture/vault-git.md`
- **Ändert:** ADR 0008, Punkte 3 und 7 (gemeinsamer Stand, Mehrbenutzerbetrieb). Entscheidet die Frage aus #123 mit Variante A.

## Kontext

„As code“ heißt für dieses Projekt: in Git. Die Programmlogik liegt in diesem Repository. Die Inhalte (Incidents, Vorfälle, Besonderheiten, Events) gehören zu einem Einsatzort, etwa einem Unternehmen, und sollen dort von mehreren Personen gepflegt werden. Änderungen sollen gegenseitig geprüft und freigegeben werden, so wie bei Code.

Heute liegt ein Beispieldatenordner (`data/`) im Code-Repository. ADR 0008 erlaubt, den Datenordner als eigenes Git-Repository zu führen, und `chronicle/sync.py` setzt genau das voraus (der Datenordner muss die Wurzel seines Repositorys sein). Der Code kennt aber noch einen zweiten, vollständigen Schreibpfad über die Datenbank (zehn Verzweigungen `if data_dir is not None` in `repository.py`, Issue #123). Ohne Datenordner landen Änderungen nie in Git.

## Optionen

1. **Alles in einem Repository** (Code und Daten). Einfach, aber Fachleute ohne Entwicklerrechte bekämen Zugriff auf den Code und umgekehrt. Mehrere Einsatzorte würden sich ein Repository teilen, die Rechte ließen sich nicht trennen.
2. **Ein Vault-Repository je Einsatzort, Code getrennt.** Rechte, Freigaberegeln und Historie gelten pro Einsatzort. Der Code wird einmal gepflegt.
3. **Ein Vault-Repository für alle Einsatzorte, Ordner je Einsatzort.** Lesen lässt sich auf GitHub nicht pro Pfad beschränken. Ein Klon enthielte alle Daten.

## Entscheidung

1. **Zwei Arten von Repositories.**
   - Das **Code-Repository** (dieses Repository) enthält die Anwendung, die CLI, das Schema, die Dokumentation, eine Vorlage für Vaults und ein Beispiel-Vault (ersetzt später `data/`). Es enthält keine echten Inhalte.
   - Ein **Vault** ist ein eigenes Git-Repository je Einsatzort. Es enthält ausschließlich Inhalte: Topics, Events, Anhänge und ein Manifest (`vault.yaml`, ADR 0012). Die Wurzel des Vaults ist der Datenordner im Sinne von ADR 0008.
2. **Option 2 gilt.** Ein Vault ist eine Vertrauens- und Rechtegrenze (ADR 0018).
3. **Die Event-Wahrheit liegt ausschließlich in den Vault-Dateien. Der Dateimodus ist der einzige Schreibpfad für Inhalte (Variante A aus #123).** Entfallen ist **nur der zweite Schreibpfad für Topics, Events und Anhänge über die Datenbank** (der „Datenbankmodus ohne Datenordner“, einschließlich Grabsteinen und gespeicherter Anhangs-Bytes). Die Anwendung startet nur mit einem Vault.
   **Die Datenbank bleibt vorgesehen** (Entscheidung des Projektinhabers), mit klar getrennten Aufgaben:
   - `ops.db` im Zustandsordner: Konten (Login), Sitzungen, Login-Sperren, Einstellungen, **Audit-Log** und etwaige **Metadaten zu Schlüsseln** für eine Verschlüsselung (ADR 0013, ADR 0023). Sie ist Betrieb, nicht Inhalt, und liegt nie im Vault.
   - `index.db` im Cache: abgeleiteter Index der Vault-Inhalte, jederzeit neu aufbaubar (ADR 0017).
   Was ein Ereignis ist und wie es sich ändert, steht ausschließlich im Vault.
4. **Code- und Vault-Repository sind nur über das Format gekoppelt.** Der Vault nennt seine Formatnummer im Manifest, die Anwendung kennt die Formate, die sie lesen kann (ADR 0022).
5. **Der Vault trägt seine Prüfung mit sich.** Ein Workflow im Vault ruft die wiederverwendbare Prüfung aus dem Code-Repository auf (ADR 0015, ADR 0021). Er enthält keine eigene Logik.
6. **GitHub ist der Standard-Hoster** (Entscheidung des Projektinhabers): Linkvorlage für Pull Requests, importierbares Ruleset, `chronicle vault doctor` über `gh`, Vorlage-Repository und Prüf-Workflow auf GitHub Actions sind GitHub-Voreinstellungen. **Der Kern bleibt host-neutral** und benötigt nur das Git-Protokoll (`fetch`, `push`). Andere Hoster (GitLab, Gitea) sind „unterstützt, nicht Standard“: Wer sie nutzt, ersetzt die Schicht aus Linkvorlage, Regelwerk, Doktor und CI selbst. Diese Schicht ist ein Erweiterungspunkt (eine kleine Schnittstelle mit der GitHub-Umsetzung als einziger mitgelieferter).

## Konsequenzen

- Ein neuer Einsatzort braucht ein neues Repository aus der Vorlage (`chronicle vault init`, Plan Teil 11). Das ist gewollt: Ein Einsatzort ohne eigenes Repository ist ein Einsatzort ohne eigene Freigabe.
- Code-Releases und Vault-Formate laufen getrennt. Dafür braucht es eine Formatversion und Migrationen (ADR 0022).
- Das Entfernen des zweiten Schreibpfads ändert viele Tests und ist ein eigener, vorgezogener Schritt (Plan Teil 2a bis 2c). Danach gibt es pro Änderung einen Pfad statt zwei. Die Datenbank selbst bleibt (Konten, Audit, Schlüssel-Metadaten, Index).
- ADR 0008 Punkt 7 („je Person eine Instanz“) bleibt der Normalfall (Arbeitsplatz-Modus). Ein Server für mehrere Personen ist ein Sonderfall mit eigener Schwäche bei der Autorenschaft (ADR 0014) und gehört nicht zum ersten Ausbau.
- `data/` im Code-Repository wird zum Beispiel-Vault. Der Workflow `topic-files.yml` prüft dann das Beispiel und nicht mehr „den Datenordner“.

## Offene Punkte

- Ob Betreiber mehrerer Einsatzorte eine Template-Organisation auf GitHub nutzen oder `chronicle vault init` genügt. Beides ist möglich.
