# ADR 0023: Verschlüsselung der Einträge (Optionen und Empfehlung)

- **Status:** Vorgeschlagen / **Nutzerentscheidung nötig**
- **Datum:** 2026-10-25
- **Bezug:** ADR 0011 (Datenbank bleibt für Login, Audit, Verschlüsselung), ADR 0013 (`ops.db`, Schlüssel-Metadaten), ADR 0016 (Merge-Treiber), ADR 0017 (Index), ADR 0019 (Anhänge), ADR 0020 (Löschkonzept), ADR 0021 (Prüfung), `docs/architecture/vault-git.md`

## Kontext

Der Projektinhaber möchte eine „etwaige Verschlüsselung der Einträge“ offenhalten. Ohne sie schützt Chronicle Inhalte durch den Zugriffsschutz des Hosters (Wer das Repository klonen darf, liest alles), das Betriebssystem und die Festplattenverschlüsselung des Arbeitsplatzes (ADR 0013, Konsequenzen). Verschlüsselung in Git ist **sicherheitskritisch und folgenreich**: Sie betrifft Merge, Review, Suche, Schlüsselverwaltung und Löschung. Dieses ADR beschreibt die Optionen und gibt eine Empfehlung. **Es wird keine Kryptographie selbst entworfen.** Genannt werden nur etablierte, öffentlich geprüfte Werkzeuge.

## Optionen

### (a) Keine Verschlüsselung in Git, nur Zugriffsschutz des Hosters

Der Vault liegt im Klartext in einem privaten Repository. Zugriff regelt der Hoster (ADR 0014, 0015).

### (b) Verschlüsselung im Vault (Feld- oder Dateiebene)

Etablierte Werkzeuge: **age** (einfaches Dateiverschlüsselungswerkzeug, kennt mehrere Empfänger, auch SSH-Schlüssel als Empfänger), **sops** (verschlüsselt Werte in YAML und lässt Schlüssel lesbar; Schlüssel über age, PGP oder Cloud-KMS), **git-crypt** (transparente Verschlüsselung ganzer Dateien im Repository, Schlüsselverteilung über GPG). Hier gibt es drei Spielarten:

- **(b1) Ganze Datei verschlüsselt.** Alles in `topics/**`, `evidence/**` ist Chiffretext. Maximaler Schutz, Metadaten (Key, Zeit, Phase) ebenfalls verborgen.
- **(b2) Felder verschlüsselt, Metadaten im Klartext (sops-artig).** `key`, `t0`, `phase`, `status`, `triggered_by`, `also_in` bleiben lesbar. `action`, `actor`, `system`, `details` und Anhangsnamen sind Chiffretext. Dateien bleiben YAML.
- **(b3) Nur Anhänge verschlüsselt.** Texte bleiben lesbar, Binärdateien (Screenshots, Logs) liegen als verschlüsselte Blobs. Metadaten und `sha256` im Klartext.

Der Empfängerkreis (wer entschlüsseln darf) ist die Zugriffskontrolle auf Inhaltsebene: Wer nicht Empfänger ist, kann den Klon haben und trotzdem nichts lesen.

### (c) Verschlüsselung nur lokal (at rest)

Der Vault im Klon bleibt Klartext. Geschützt werden die **lokalen** Kopien: Festplattenverschlüsselung (FileVault, LUKS) als Betriebsvoraussetzung, `CHRONICLE_HOME` mit Rechten `0700`, optional verschlüsselte SQLite-Dateien für `ops.db` und `index.db` (etabliert: SQLCipher). Schützt vor Verlust des Geräts, nicht vor dem Hoster und nicht vor anderen Teammitgliedern.

## Bewertung

| Kriterium | (a) nur Hoster | (b1) Datei | (b2) Feld, Metadaten offen | (b3) nur Anhänge | (c) lokal |
|---|---|---|---|---|---|
| **Merge-Treiber** (ADR 0016) | unverändert, feldweise | Feldmerge unmöglich, nur Datei gegen Datei | Feldmerge nur auf Klartext-Metadaten. Verschlüsselte Felder: Konflikt oder Entschlüsseln im Treiber (Treiber braucht Schlüssel, ist Code mit Schlüsselzugriff). Bei sops-artigen Formaten gibt es zusätzlich eine Prüfsumme über die Datei, die bei jeder parallelen Änderung kollidiert (vor einer Entscheidung am Prototyp prüfen) | unverändert für Texte | unverändert |
| **Review im PR** | Diff lesbar | Diff nicht lesbar, Review unmöglich auf dem Hoster | Metadaten-Diff lesbar, Inhalte nicht. Prüfer müssen lokal entschlüsseln und vergleichen. Der Hoster-Diff (und die CODEOWNERS-Prüfung) sieht nur Chiffretext | Texte lesbar, Anhänge nicht | unverändert |
| **Suche und Index** | Index aus Klartext | Index nur lokal nach Entschlüsselung, Cache enthält Klartext (dann braucht er Schutz (c)) | wie b1 für verschlüsselte Felder | Texte durchsuchbar | unverändert |
| **Vault-CI** | prüft alles | kann Inhalte nicht prüfen (kein Schlüssel auf dem Runner, und keine Geheimnisse dort, ADR 0021 Punkt 26) | prüft Metadaten und Struktur, nicht die Inhalte (z. B. nicht `check --secrets` auf `details`) | prüft Texte, Anhänge nicht | unverändert |
| **Schlüsselverwaltung** | keine | Schlüssel je Person, Empfängerliste im Vault, Verteilung, Sicherung | wie b1 | wie b1 | Passwort des Datenträgers |
| **Rotation und Entzug** | Zugriff beim Hoster entziehen | Empfänger entfernen und neu verschlüsseln. **Die Historie behält alte Chiffretexte**, die ein entzogener Empfänger (mit Klon oder Schlüssel) weiter lesen kann | wie b1 | wie b1 | Schlüsselwechsel der Datenbank |
| **DSGVO-Löschkonzept** (ADR 0020) | Bereinigung der Historie nötig | **Crypto-Shredding** ist möglich: Wird der Datenschlüssel vernichtet, sind die Chiffretexte unlesbar. Das gilt nur, wenn die Schlüsselkopien in allen Klonen und der Historie entfernt sind. Der Schlüssel ist klein, die Bereinigung der Historie deshalb leichter als bei Anhängen | wie b1, aber nur für die verschlüsselten Felder. Klartext-Metadaten (Key, Zeit) bleiben | wie b1 für Anhänge | nicht wirksam (Git bleibt Klartext) |
| **Verfügbarkeit bei Vorfällen** | hoch | Schlüsselverlust oder abwesende Empfänger blockieren Zugriff (Notfallschlüssel nötig); Entschlüsselung braucht Werkzeug am Arbeitsplatz | wie b1 | geringere Wirkung | hoch |
| **Komplexität und Fehlerquote** | gering | hoch | sehr hoch (zwei Zustände, Merge, Schlüssel) | mittel | gering bis mittel |
| **Schutz gegen Hoster** | nein | ja (Metadaten ebenfalls) | teilweise (Metadaten sichtbar) | teilweise | nein |
| **Schutz bei verlorenem Gerät** | nur mit Festplattenverschlüsselung | ja | ja | ja | ja |

## Entscheidung (Empfehlung, zur Bestätigung)

1. **Zunächst (a) und (c).** Der Vault liegt im Klartext in einem privaten Repository. Der Zugriffsschutz kommt vom Hoster (ADR 0014, 0015, GitHub als Standard, ADR 0011). **Festplattenverschlüsselung des Arbeitsplatzes ist Betriebsvoraussetzung** (FileVault, LUKS). `CHRONICLE_HOME` und `cache/` bleiben `0700`. SQLCipher für `ops.db` und `index.db` ist eine Option ohne Eile, weil `ops.db` nur Hashes enthält und der Index verwerfbar ist. Wer sie will, bekommt sie in einem eigenen Teil (Plan Teil 22, nur nach Entscheidung).
2. **(b) nur bei konkretem Bedarf**, zum Beispiel: Nicht alle Mitarbeitenden eines Vaults dürfen alle Topics lesen (Git kennt kein Leserecht pro Pfad, siehe ADR 0012 zu `also_in`), oder der Hoster gilt als nicht vertrauenswürdig genug für die Inhalte, oder ein Vertrag verlangt es. In diesem Fall:
   - **(b2) auf Dateiebene** je Event-Datei oder je Topic mit **Klartext-Metadaten** ist die bevorzugte Form (Review der Struktur, Merge der Metadaten, Prüfung von Beziehungen bleiben möglich), mit **age** als Werkzeug und **SSH-Schlüsseln der Personen als Empfänger**, damit keine neuen Schlüssel verteilt werden müssen. Der Empfängerkreis steht pro Topic in einer Klartext-Datei im Vault und wird per Pull Request geändert (das ist die Zugriffskontrolle).
   - **Keine eigene Konstruktion.** Insbesondere keine eigene Auswahl von Algorithmen, Betriebsarten oder Schlüsselableitung. Nur age (oder sops mit age) mit Standardeinstellungen.
   - **Datenschlüssel je Topic** (age-verschlüsselt an die Empfänger), damit Crypto-Shredding je Topic möglich ist.
   - Der **Merge-Treiber** entschlüsselt nicht. Konflikt bei gleichzeitiger Änderung derselben verschlüsselten Datei, Auflösung lokal durch einen Empfänger.
   - Die Vault-CI prüft nur Metadaten. Die Geheimnisprüfung (ADR 0020) läuft lokal vor dem Verschlüsseln.
3. **(b1) wird nicht empfohlen**: Sie nimmt den Pull Requests ihren Sinn (Review im Hoster) und dem Merge-Treiber die Grundlage.
4. **Schlüsselmaterial gehört nicht in `ops.db`.** `ops.db` hält höchstens Metadaten (Schlüsselkennung, Fingerabdruck der Empfänger, Datum der letzten Rotation). Private Schlüssel bleiben dort, wo die Person sie ohnehin hat (SSH-Agent, Schlüsselbund des Betriebssystems). Die Anwendung speichert keine Passphrasen.
5. **Rotation und Entzug sind ehrlich zu beschreiben:** Das Entfernen eines Empfängers schützt neue Inhalte. Für Inhalte, die der Entzogene schon kannte oder die in seinem Klon liegen, hilft nur der Wechsel des Datenschlüssels und die Bereinigung der Historie (ADR 0020, Punkt 10). Dieser Satz steht in der Anleitung, damit niemand glaubt, ein Entzug lösche Wissen.

## Konsequenzen

- Mit (a)+(c) ändert sich am Plan nichts außer der Betriebsvoraussetzung (Festplattenverschlüsselung) in der Anleitung und einer Prüfung durch `chronicle vault doctor`, wenn das Betriebssystem es zeigt (FileVault-Status; optional).
- Wird (b2) gewählt, entsteht ein neues Format-Feld im Manifest (`encryption: age`, nur als lokale Entscheidung des Vaults, ADR 0012, Punkt 6), eine Änderung des Merge-Treibers, des Index (Entschlüsseln beim Aufbau) und der CI. Das sind mehrere Teile und ein neues Format (ADR 0022), nicht ein Zusatz.
- Anhänge in Git (ADR 0019) sind der größte Datenabfluss bei (a). Wenn Verschlüsselung gewünscht ist, ist (b3) oft der kleinste sinnvolle Schritt.
- Die Datenbank bleibt vorgesehen für Verschlüsselungs-Metadaten (ADR 0011), aber nie für Schlüssel.

## Offene Punkte (Nutzerfrage 11)

- **Gibt es einen konkreten Bedarf für (b)?** Wenn ja: Wer darf welche Topics lesen, und gilt der Hoster als nicht vertrauenswürdig? Ohne konkreten Bedarf gilt die Empfehlung (a)+(c).
- Ob SQLCipher für `ops.db` und `index.db` gewünscht ist (native Abhängigkeit, ein Nutzen vor allem bei Gerätediebstahl ohne Festplattenverschlüsselung).
- Verhalten von sops bei parallelen Änderungen und Rauschen im Diff ist vor einer Entscheidung an einem Prototyp zu prüfen.
