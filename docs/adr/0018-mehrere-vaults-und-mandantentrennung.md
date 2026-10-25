# ADR 0018: Mehrere Vaults und Mandantentrennung

- **Status:** Vorgeschlagen (zu bestätigen). Nach Review **zurückgestellt**: Der Standard ist „eine Installation, ein Vault“ (Registry mit einem Eintrag). Die Punkte 2 bis 4 (aktiver Vault, URL-Präfix) werden erst umgesetzt, wenn der Bedarf besteht, und dann nach Issue #127 (Router aufteilen).
- **Datum:** 2026-10-25
- **Bezug:** ADR 0001 (offener Punkt Mandantenfähigkeit), ADR 0011, ADR 0013, ADR 0017

## Kontext

Mehrere Einsatzorte (Unternehmen) sollen mit derselben Installation arbeiten können, etwa wenn eine Person für mehrere Kunden tätig ist. Die Daten der Einsatzorte dürfen sich nicht vermischen. ADR 0001 ließ die Mandantenfähigkeit offen.

## Entscheidung

1. **Ein Vault ist ein Mandant.** Er hat ein eigenes Repository, eigene Rechte, eigene Freigabe, einen eigenen Index (ADR 0017) und eine eigene **lokale ID** (ADR 0013). Die Manifest-ID ist nur ein Etikett (ADR 0012) und trennt nichts.
2. **Die Registry kennt mehrere Vaults** (ADR 0013). Die Oberfläche arbeitet immer in **genau einem** aktiven Vault. Das Umschalten ist eine ausdrückliche Handlung. Es gibt keine Ansicht, Suche oder Verknüpfung über Vaults hinweg.
3. **Keine Verweise über Vault-Grenzen.** `triggered_by`, `also_in` und Topic-Keys gelten innerhalb des Vaults. Event-Keys dürfen in verschiedenen Vaults gleich sein.
4. **Der aktive Vault steht in der Sitzung und in jeder Anfrage.** URL-Präfix `/v/<local-id>/…` für Oberfläche und API (`/api/v1/v/<vault-id>/…`), damit ein Lesezeichen oder ein zweiter Browser-Tab nie in einem anderen Vault schreibt als gedacht. Ein Schreibvorgang trägt die Vault-`id` und scheitert bei Abweichung.
5. **Eigene Dateien je Vault:** Index, Sperrdatei (`.chronicle.lock` im Vault), Arbeits-Branch. Der Audit-Eintrag trägt `vault_id`.
6. **Zugriff.** Wer das Repository nicht klonen darf, hat den Vault nicht. Die Anwendung ergänzt keine eigene Berechtigung je Vault (ADR 0014). Rollen sind je Installation. Wer mehrere Einsatzorte mit unterschiedlich vertrauenswürdigen Personen auf **einer** Installation betreibt, braucht getrennte Installationen (getrennte `CHRONICLE_HOME`) oder getrennte Betriebssystem-Benutzer. Das ist die Grenze dieses Modells.
7. **Gemeinsame Teile pro Installation:** Konten und Passwörter (`ops.db`), Lesesperre, Einstellungen der Oberfläche. Diese enthalten keine Vault-Inhalte.

## Konsequenzen

- Der Schutz zwischen Mandanten ist der des Betriebssystems und des Hosters, nicht der der Anwendung. Ein Fehler in der Anwendung kann Daten zweier Vaults im selben Prozess vermischen. Maßnahmen: Pfade immer über das Vault-Objekt bilden, in Tests Mandanten-Verwechslung prüfen (Plan Teil 9, Abnahme).
- Sitzungen sind je Installation. Ein gültiger Login berechtigt in der App für alle registrierten Vaults.
- Das URL-Präfix ist eine größere Änderung in Routen und Vorlagen (`pages.py`, `api.py`). Sie ist in einem eigenen Teil ausgeplant (Plan Teil 9).

## Offene Punkte

- Rollen je Vault statt je Installation (nötig erst für den Server-Modus, ADR 0014).
