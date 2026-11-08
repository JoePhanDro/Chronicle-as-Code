# ADR 0030: IOC-Export als CSV und STIX 2.1

- **Status:** Vorgeschlagen (zu bestätigen)
- **Datum:** 2026-11-08
- **Bezug:** ADR 0029 (getypte Entities an Events), ADR 0021 (sensible Daten/Löschung), Issues #259, #210, #98

## Kontext

Mit #210 trägt jedes Event getypte Entities (ADR 0029). Was fehlt: der Export — Analysten kopieren IOCs heute per Hand aus der Doku in Tickets, SIEM oder Blocklists (Issue #259).

## Entscheidung

1. **Zwei Formate, beide immer verfügbar:** CSV fürs Ticketing/Blocklists, STIX fürs SIEM. Das STIX-Format ist **STIX 2.1** (gepinnt), damit externe Tools es sicher lesen können.
2. **Umfang:** pro Topic (`/topics/{key}/iocs`) oder global, dazu optionaler Zeitbereich (`since`/`until`, UTC-Tage auf `t0`). Entities werden nach `(type, value)` **dedupliziert**; jede Entity trägt `first_seen`/`last_seen` (t0-Bereich der Events, die sie tragen), die Event-Keys als Quelle und die Topics.
3. **CSV:** Header `type,value,first_seen,last_seen,events,topics`; Events und Topics als `;`-getrennte Liste in der Zelle. UTF-8, Dateiname `iocs-<topic>.csv` bzw. `iocs-all.csv`. **Anti-CSV-Injection:** Jede Textzelle, die mit `=`, `+`, `-`, `@`, Tab oder CR beginnt, bekommt ein führendes Apostroph (`'`) — sonst würde Excel/LibreOffice den Wert als Formel ausführen (Entity-Werte wie `user` können attacker-gewählt sein; GitHub Advanced Security, PR #271). Das Apostroph ist reines Anzeige-Hardening, der gespeicherte Wert bleibt unverändert.
4. **STIX 2.1:** ein `bundle` mit einem `indicator` pro Entity (`spec_version: "2.1"`, `pattern_type: "stix"`). Deterministische IDs über `uuid5("chronicle:ioc:<type>:<value>")` — ein Re-Export derselben Entity ergibt dieselbe ID. Pattern-Mapping: `ip` → `ipv4-addr`/`ipv6-addr` (nach Version), `hash` → `file:hashes.md5|sha1|sha256|sha512` (nach Länge), `domain`/`host` → `domain-name`, `url` → `url`, `email` → `email-addr`, `user` → `user-account:display_name`. Quelle und Zeitraum in Custom Properties (`x_chronicle_events`, `x_chronicle_topics`, `x_chronicle_first_seen`, `x_chronicle_last_seen`); `created`/`valid_from` = `first_seen`. **Keine Sighting-Objekte** — ein Sighting pro Event wäre Rauschen; die Quelle steht in den Custom Properties.
5. **Rolle:** analyst und admin (Writer), nicht nur admin wie die Berichte. **Jeder Export landet im Audit-Log** (`ioc_export`) — mit Format, Umfang und Anzahl, **nie mit den IOC-Werten**.
6. **Download-Header:** `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`.
7. **Defangter Import** ist durch ADR 0029 abgedeckt (Normalisierung beim Speichern); der Export enthält die aufgelösten Werte.
8. **Löschung (ADR 0021, #98):** Der Export ist eine Kopie der Daten im Arbeitsstand — wer die Datei hat, sieht die IOCs. Bereinigung einer versehentlich geteilten Datei folgt ADR 0021 Punkt 10, die IOCs selbst liegen wie alle Vorfallsdaten im Vault.

## Wirkung auf die Teile

| Teil | Änderung |
|---|---|
| Neu | `chronicle/ioc_export.py`: collect/dedupe, CSV, STIX 2.1, Audit |
| API | `GET /api/v1/iocs?format=csv\|stix` und `GET /api/v1/topics/{key}/iocs?format=…` (+ `since`/`until`), Writer-Rollen |
| Seiten | Downloads `/iocs?format=…` und `/topics/{key}/iocs?format=…`; Links auf der Topic-Seite und global auf der Berichte-Seite |
| Doku | README (de/en) |
