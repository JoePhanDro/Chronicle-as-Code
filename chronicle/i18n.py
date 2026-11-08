"""Sprache der Oberfläche (issue #220): de (Standard, Texte in den Templates) und en.

Pro Betrachter, als Cookie `chronicle.lang` (wie Design und Dichte im localStorage, aber serverseitig
lesbar, weil die Templates serverseitig gerendert werden). Ohne Cookie ist die Oberfläche deutsch;
die Sprache wechselt explizit über den Einstellungsdialog.

Die Templates rufen `t('Deutscher Text')` — der deutsche Text ist der Schlüssel. Das englische
Katalog EN mappt Schlüssel auf Englisch; fehlt ein Eintrag, fällt die Ausgabe auf Deutsch zurück.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import Request

LANGUAGES = ("de", "en")
LANG_COOKIE = "chronicle.lang"
DEFAULT_LANG = "de"
LANG_MAX_AGE = 60 * 60 * 24 * 365

current_lang: ContextVar[str] = ContextVar("chronicle_lang", default=DEFAULT_LANG)

# English catalog: German source text (the template key) -> English. Unlisted keys stay German.
EN: dict[str, str] = {
    # base.html: navigation and settings
    "Ansichten": "Views",
    "Ereignisse": "Events",
    "Topics": "Topics",
    "Berichte": "Reports",
    "Neu": "New",
    "Ereignis": "Event",
    "Daten": "Data",
    "Abgleichen": "Sync",
    "Verwaltung": "Admin",
    "Benutzer": "Users",
    "Audit-Log": "Audit log",
    "Konto": "Account",
    "Einstellungen": "Settings",
    "Zum Inhalt springen": "Skip to content",
    "Hauptmenü": "Main menu",
    "Abmelden": "Sign out",
    "Passwort": "Password",
    "Passwort ändern": "Change password",
    "Sprache / Language": "Language",
    "deutsch": "Deutsch",
    "English": "English",
    "Dichte der Tabellen": "Table density",
    "normal": "normal",
    "kompakt": "compact",
    "Design": "Theme",
    "wie das Betriebssystem": "as the operating system",
    "hell": "light",
    "dunkel": "dark",
    "Zeitzone": "Time zone",
    "lokale Zeit des Browsers": "browser local time",
    "Schließen": "Close",
    "Sperre": "Lock",
    "Sperre aufheben": "Remove lock",
    "Sperre einschalten": "Enable lock",
    "Neues Sperr-Passwort (mind. 12 Zeichen)": "New lock password (min. 12 characters)",
    "Sperr-Passwort (mind. 12 Zeichen)": "Lock password (min. 12 characters)",
    "Die Ansicht ist gesperrt. Ohne Konto liest man sie nur nach Eingabe des Sperr-Passworts. Dieses Passwort gilt für alle.":
        "The view is locked. Without an account you can only read it after entering the lock password. This password applies to everyone.",
    "Die Ansicht ist nicht gesperrt. Ohne Konto kann man sie nicht lesen.":
        "The view is not locked. Without an account you cannot read it.",
    "Neu laden": "Reload",
    "Die Topic-Dateien sind neu eingelesen.": "The topic files have been re-read.",
    "Topic-Dateien neu einlesen, etwa nach Änderungen im Editor oder durch Git":
        "Re-read the topic files, e.g. after changes in the editor or via Git",
    "Die Einstellungen gelten für diesen Browser. Graph und Timeline greifen beim nächsten Laden des Topics. Tastenkürzel stehen unter":
        "These settings apply to this browser. Graph and timeline take effect the next time the topic loads. Shortcuts are under",
    "Die Sprache wechselt die gesamte Oberfläche beim nächsten Laden.":
        "The language switches the whole interface on the next load.",
    "Helles Design aktivieren": "Activate light theme",
    "Suchen": "Search",
    "Suchen (/)": "Search (/)",
    "Suchen in Ereignissen, Akteuren und Systemen": "Search events, actors and systems",
    # auth pages
    "Anmeldung": "Sign in",
    "Anmelden": "Sign in",
    "Benutzername": "Username",
    "Ansicht entsperren": "Unlock view",
    "Ohne Konto lesen Sie nach Eingabe des Sperr-Passworts.": "Without an account you read it after entering the lock password.",
    "Sperr-Passwort": "Lock password",
    "Entsperren": "Unlock",
    "Erster Start": "First start",
    "Es gibt noch kein Konto. Den Einmal-Code findest du im Log des Servers": "There is no account yet. You find the one-time code in the server log",
    "Einmal-Code aus dem Log": "One-time code from the log",
    "Benutzername des Admins": "Admin username",
    "Passwort (mindestens": "Password (at least",
    "Zeichen)": "characters)",
    "Admin anlegen": "Create admin",
    "Aktuelles Passwort": "Current password",
    "Neues Passwort (mindestens 12 Zeichen)": "New password (at least 12 characters)",
    "Dein Passwort wurde vergeben. Bitte lege jetzt ein eigenes fest, bevor du weitermachst.":
        "Your password was assigned. Please set your own before continuing.",
    "Alle anderen Sitzungen werden beendet. Diese bleibt angemeldet.": "All other sessions are ended. This one stays signed in.",
    "Speichern": "Save",
    # common pages
    "Nicht gefunden": "Not found",
    "Zurück zu den Ereignissen": "Back to the events",
    "Zurück zu den Berichten": "Back to the reports",
    "Zurück": "Back",
    "Fehler": "Error",
    "Keine Berechtigung": "No permission",
    "Für diese Aktion fehlt die Rolle, oder das Formular ist abgelaufen. Seite neu laden und erneut versuchen.":
        "This action lacks the role, or the form has expired. Reload the page and try again.",
    "Suche": "Search",
    "Hilfe": "Help",
    "Neu laden nicht möglich": "Reload not possible",
    "Es wurde nichts übernommen. Die Anzeige zeigt weiter den Stand von vorher. Nach der Korrektur in den Dateien noch einmal neu laden.":
        "Nothing was applied. The view still shows the previous state. After fixing the files, reload again.",
    # home
    "Ereignisse und Topics festhalten, Belege zuordnen und Berichte erstellen.":
        "Record events and topics, attach evidence and create reports.",
    "Ansehen": "Browse",
    "Zeitlich geordnetes Protokoll aller Vorfälle": "Chronological log of all incidents",
    "Vorfälle und Projekte, denen Ereignisse zugeordnet sind": "Incidents and projects that events belong to",
    "HTML und PDF zu einem Topic, auch für Lessons Learned": "HTML and PDF for a topic, also for lessons learned",
    "Neu anlegen": "Create new",
    "Neues Ereignis": "New event",
    "Eine neue Aktion im Protokoll erfassen": "Record a new action in the log",
    "Neues Topic": "New topic",
    "Einen Vorfall oder ein Projekt anlegen": "Create an incident or a project",
    # search
    "Suchbegriff": "Search term",
    "Ereignisse, Akteure oder Systeme suchen": "Search events, actors or systems",
    "Gib einen Suchbegriff ein. Gesucht wird in Aktion, Details, Akteur und System der Events sowie in den Namen der Akteure und Systeme.":
        "Enter a search term. It searches action, details, actor and system of the events as well as the names of actors and systems.",
    "Akteure": "Actors",
    "Systeme": "Systems",
    "Keine Ereignisse gefunden.": "No events found.",
    "Kein Akteur passt.": "No actor matches.",
    "Kein System passt.": "No system matches.",
    "Alle": "All",
    "Treffer anzeigen": "show all hits",
    # events list
    "gefunden": "found",
    "im Topic": "in topic",
    "im System": "in system",
    "mit Technik": "with technique",
    "zeitlich sortiert": "sorted by time",
    "Neues Event": "New event",
    "Suche in Aktion, Details, Tags und Kommentaren": "Search in action, details, tags and comments",
    "z. B. Webshell": "e.g. webshell",
    "Topic": "Topic",
    "alle": "all",
    "Phase": "Phase",
    "Status": "Status",
    "Akteur": "Actor",
    "ATT&CK-Technik": "ATT&CK technique",
    "Erkennung (Regel-ID)": "Detection (rule ID)",
    "Regel-ID der Erkennung": "Rule ID of the detection",
    "Tag (exakt)": "Tag (exact)",
    "zu-prüfen": "to-review",
    "Von (UTC)": "From (UTC)",
    "Bis (UTC)": "Until (UTC)",
    "Gespeicherter Filter (Preset aus presets/)": "Saved filter (preset from presets/)",
    "— ohne Preset —": "— no preset —",
    "Filtern": "Filter",
    "Filter zurücksetzen": "Reset filter",
    "Preset-Name (Datei presets/<name>.yaml)": "Preset name (file presets/<name>.yaml)",
    "z. b. t1059-offen": "e.g. t1059-open",
    "Anzeigename (optional)": "Display name (optional)",
    "T1059 in offenen Incidents": "T1059 in open incidents",
    "Aktuellen Filter als Preset speichern": "Save current filter as preset",
    "Zeit": "Time",
    "Aktion": "Action",
    "Seite": "Page",
    "von": "of",
    "Weiter": "Next",
    # event page and form
    "Anhänge": "Attachments",
    "Dateien (PNG, JPEG, GIF, WebP, PDF, TXT/LOG, je max. 10 MiB)": "Files (PNG, JPEG, GIF, WebP, PDF, TXT/LOG, max. 10 MiB each)",
    "Typ": "Type",
    "Anlegen": "Create",
    "Abbrechen": "Cancel",
    "Bearbeiten": "Edit",
    "Phase:": "Phase:",
    "Status:": "Status:",
    "System": "System",
    "ATT&CK-Techniken": "ATT&CK techniques",
    "Tags": "Tags",
    "Erkannt durch": "Detected by",
    "Beschreibung": "Description",
    "Notizen und Kommentare": "Notes and comments",
    "Noch keine Notizen.": "No notes yet.",
    "Neue Notiz (Markdown)": "New note (Markdown)",
    "Notiz hinzufügen": "Add note",
    "Evidence": "Evidence",
    "Inhalt fehlt. Bitte den Import erneut ausführen.": "Content missing. Please run the import again.",
    "Entfernen": "Remove",
    "Evidence hinzufügen": "Add evidence",
    "Evidence hinzufügen schließen": "Close add evidence",
    "Datei (PNG, JPEG, GIF, WebP, PDF, TXT/LOG, max. 10 MiB)": "File (PNG, JPEG, GIF, WebP, PDF, TXT/LOG, max. 10 MiB)",
    "Beschreibung (optional)": "Description (optional)",
    "Hochladen": "Upload",
    "Weitere Felder bearbeiten · Event löschen": "Edit more fields · Delete event",
    "Bearbeiten schließen": "Close editing",
    "Event löschen": "Delete event",
    "Was ist passiert?": "What happened?",
    "Key": "Key",
    "Wird beim Speichern automatisch vergeben": "Assigned automatically when saving",
    "Key (eindeutig)": "Key (unique)",
    "Zeitpunkt": "Time",
    "Datum": "Date",
    "Stunde": "Hour",
    "Minute": "Minute",
    "Zeitzone der Eingabe": "Time zone of the input",
    "Beteiligte": "Participants",
    "System (optional)": "System (optional)",
    "ATT&CK": "ATT&CK",
    "Techniken (optional, mit Komma getrennt)": "Techniques (optional, comma separated)",
    "Freie Tags (mit Komma getrennt)": "Free tags (comma separated)",
    "zu-prüfen, lateral-movement": "to-review, lateral-movement",
    "Erkennung": "Detection",
    "Welche Regel bzw. welcher Alert im SIEM/EDR zu diesem Event geführt hat (leer lassen, wenn unbekannt). Mindestens Regel-ID oder Alert-ID.":
        "Which rule or alert in the SIEM/EDR led to this event (leave empty if unknown). At least rule ID or alert ID.",
    "Regel-ID": "Rule ID",
    "Regelname": "Rule name",
    "Regelstand": "Rule version",
    "Quelle (detektierendes System)": "Source (detecting system)",
    "Alert-ID / Link": "Alert ID / link",
    "z. B. Sigma-UUID oder SIEM-Regelnummer": "e.g. Sigma UUID or SIEM rule number",
    "z. B. Webshell Upload": "e.g. Webshell upload",
    "Version oder Commit der Regel": "Version or commit of the rule",
    "z. B. Defender, Elastic": "e.g. Defender, Elastic",
    "z. B. alert-12345 oder URL": "e.g. alert-12345 or URL",
    "Verknüpfungen": "Links",
    "Ausgelöst durch (beliebig viele)": "Caused by (any number)",
    "Events filtern": "Filter events",
    "Events filtern: Aktion oder Key": "Filter events: action or key",
    "Noch keine anderen Events.": "No other events yet.",
    "Kein Event gefunden.": "No event found.",
    "Heimat-Topic": "Home topic",
    "Zugehörige Topics": "Linked topics",
    "Details": "Details",
    "Beschreibung (Markdown)": "Description (Markdown)",
    # topics
    "offene zuerst, darin das neueste Event zuerst": "open first, newest event first within",
    "Topics filtern": "Filter topics",
    "Titel, Key oder Art": "Title, key or kind",
    "Meldepflicht überfällig": "Reporting deadline overdue",
    "Frist läuft ab": "Deadline approaching",
    "Titel": "Title",
    "Art": "Kind",
    "Zusammenfassung": "Summary",
    "Der Key wird beim Speichern automatisch vergeben und danach auf der Seite des Topics angezeigt.":
        "The key is assigned automatically when saving and shown on the topic page afterwards.",
    "verknüpft": "linked",
    "Lösen": "Unlink",
    "zuletzt": "last",
    "Im Ereignisprotokoll anzeigen": "Show in the event log",
    "Event aus anderem Topic verknüpfen": "Link event from another topic",
    "Noch keine Events in diesem Topic": "No events in this topic yet",
    "Events legen die Zeitleiste und den Graphen an. Ein Event hat dieses Topic als Heimat oder ist mit ihm verknüpft.":
        "Events build the timeline and the graph. An event has this topic as its home or is linked to it.",
    "Erstes Event anlegen": "Create first event",
    "Events legen Analysten und Admins an.": "Analysts and admins create events.",
    "Zeitleiste und Zusammenhänge": "Timeline and relations",
    "In neuem Fenster öffnen": "Open in new window",
    "Zeitleiste überspringen": "Skip timeline",
    "Meldepflicht": "Reporting obligation",
    "Kenntnisnahme:": "Known since:",
    "Fristen als Anzeige, es wird nichts automatisch versendet.": "Deadlines are shown only, nothing is sent automatically.",
    "überfällig": "overdue",
    "läuft ab": "due soon",
    "läuft": "running",
    "keine Frist konfiguriert": "no deadline configured",
    "PDF erzeugen": "Generate PDF",
    "Topic bearbeiten": "Edit topic",
    "Key (nicht änderbar)": "Key (cannot be changed)",
    "Event aus einem anderen Topic verknüpfen": "Link event from another topic",
    "Liste filtern": "Filter list",
    "Aktion oder Key": "Action or key",
    "Verknüpfen": "Link",
    "Topic löschen": "Delete topic",
    "Entfernt das Topic. Verknüpfte Events bleiben in ihren Heimat-Topics erhalten.":
        "Deletes the topic. Linked events stay in their home topics.",
    # reporting fields
    "Meldepflicht (optional)": "Reporting obligation (optional)",
    "Rechtsgrundlage": "Legal basis",
    "— keine —": "— none —",
    "Kenntnisnahme (UTC)": "Known since (UTC)",
    "Fristen ab Kenntnisnahme (Richtwerte: NIS2 24 h / 72 h / 1 Monat, BSIG 24 h, DORA 4 h / 24 h / 1 Monat, DSGVO 72 h; pro Topic in der YAML-Datei anpassbar). Nur Anzeige und PDF-Berichte — es wird nichts versendet.":
        "Deadlines after knowledge (reference values: NIS2 24 h / 72 h / 1 month, BSIG 24 h, DORA 4 h / 24 h / 1 month, GDPR 72 h; adjustable per topic in the YAML file). Display and PDF reports only — nothing is sent.",
    "Frühwarnung": "Early warning",
    "Meldung": "Notification",
    "Abschlussbericht": "Final report",
    # notifications (issue #258)
    "Benachrichtigungen": "Notifications",
    "Webhook-Signatur gesetzt (CHRONICLE_WEBHOOK_SECRET)": "Webhook signature set (CHRONICLE_WEBHOOK_SECRET)",
    "CHRONICLE_WEBHOOK_SECRET fehlt — Webhook bleibt deaktiviert": "CHRONICLE_WEBHOOK_SECRET missing — webhook stays disabled",
    "SMTP konfiguriert": "SMTP configured",
    "CHRONICLE_SMTP_HOST fehlt — E-Mail bleibt deaktiviert": "CHRONICLE_SMTP_HOST missing — e-mail stays disabled",
    "Die Secrets liegen in der Umgebung, nicht in der Topic-Datei. Jede Zustellung und jeder Fehlschlag steht ohne Inhalt im Audit-Log; gedrosselte Meldungen ebenfalls (Rate-Limit).":
        "The secrets live in the environment, not in the topic file. Every delivery and every failure is logged in the audit log without content; rate-limited messages too.",
    "Webhook-URL (https; http nur localhost)": "Webhook URL (https; http only for localhost)",
    "Webhook auslösen bei": "Trigger webhook on",
    "E-Mail-Empfänger (mit Komma getrennt)": "E-mail recipients (comma separated)",
    "E-Mail senden bei": "Send e-mail on",
    "Benachrichtigungen speichern": "Save notifications",
    "Test-Benachrichtigung senden": "Send test notification",
    "wird im Audit-Log protokolliert": "is logged in the audit log",
    "Test-Benachrichtigung wurde versendet (Details im Audit-Log).": "Test notification was sent (details in the audit log).",
    "Test-Benachrichtigung fehlgeschlagen — Grund im Audit-Log.": "Test notification failed — reason in the audit log.",
    "Für dieses Topic sind keine Benachrichtigungen konfiguriert.": "No notifications configured for this topic.",
    "Statusänderung": "Status change",
    "Neues Evidence": "New evidence",
    "Regel-Treffer": "Rule match",
    # incident classification (issue #225)
    "Incident-Klassifikation (optional)": "Incident classification (optional)",
    "Incident-Klassifikation": "Incident classification",
    "Schweregrad": "Severity",
    "Schweregrad:": "Severity:",
    "niedrig": "low",
    "mittel": "medium",
    "hoch": "high",
    "kritisch": "critical",
    "TLP (Traffic Light Protocol)": "TLP (Traffic Light Protocol)",
    "Betroffene Personen (geschätzt)": "Affected persons (estimated)",
    "Auswirkungen (Freitext)": "Impact (free text)",
    "Auswirkungen": "Impact",
    "Betroffene Systeme (mit Komma getrennt)": "Affected systems (comma separated)",
    "Betroffene Systeme": "Affected systems",
    "Datenkategorien (mit Komma getrennt)": "Data categories (comma separated)",
    "Datenkategorien": "Data categories",
    "Ursache (Grundursache)": "Cause (root cause)",
    "Ursache": "Cause",
    "Maßnahmen (Eindämmung)": "Measures (containment)",
    "Maßnahmen": "Measures",
    # entities / IOCs (issue #210)
    "Entities / IOCs": "Entities / IOCs",
    "Eine pro Zeile als „Typ: Wert“, z. B. ip: 203.0.113.7": "One per line as “type: value”, e.g. ip: 203.0.113.7",
    "Typen: ip, hash, domain, url, user, host, email. Defangte Werte (hxxp, [.]) werden beim Speichern aufgelöst; die Suche findet sie über den echten Wert.":
        "Types: ip, hash, domain, url, user, host, email. Defanged values (hxxp, [.]) are resolved when saving; search finds them by the real value.",
    "Entity / IOC (Wert)": "Entity / IOC (value)",
    # reports
    "HTML-Bericht": "HTML report",
    "PDF technisch": "Technical PDF",
    "PDF Management": "Management PDF",
    "Noch keine Topics.": "No topics yet.",
    # admin
    "Rolle und Status": "Role and status",
    "muss beim Login geändert werden": "must be changed at login",
    "neues Passwort": "new password",
    "Setzen": "Set",
    "Neues Konto": "New account",
    "Startpasswort (mind. 12 Zeichen)": "Initial password (min. 12 characters)",
    "Rolle": "Role",
    "Das Startpasswort muss der neue Nutzer beim ersten Login ändern.":
        "The new user must change the initial password at first login.",
    "ab Datum": "from date",
    "Zeit (UTC)": "Time (UTC)",
    "Ziel": "Target",
    "Detail": "Detail",
    "Keine Einträge.": "No entries.",
    # sync
    "Vergleicht die Topic-Dateien mit dem Stand des gemeinsamen Git-Remotes. Erst die Vorschau prüfen, dann abgleichen. Dabei wird lokal committet, der Remote übernommen und hochgeladen.":
        "Compares the topic files with the state of the shared Git remote. Check the preview first, then sync. This commits locally, takes the remote and uploads.",
    "Ergebnis": "Result",
    "dieser Stand": "this side",
    "Stand des Remotes": "remote side",
    "Die Datei gibt es auf dieser Seite nicht.": "The file does not exist on this side.",
    "Welcher Stand soll gelten?": "Which side should win?",
    "Diese Kopie behalten": "Keep this copy",
    "Stand des Remotes übernehmen": "Take the remote side",
    "Die gewählte Datei ersetzt die andere vollständig. Danach wird geprüft und hochgeladen; Git behält den früheren Stand.":
        "The chosen file fully replaces the other. Afterwards it is checked and uploaded; Git keeps the previous state.",
    "Auswahl übernehmen und abgleichen": "Apply choice and sync",
    "Der zusammengeführte Stand besteht die Prüfung nicht. Die Zusammenführung ist rückgängig gemacht, nichts wurde hochgeladen.":
        "The merged state fails the check. The merge was rolled back, nothing was uploaded.",
    "Prüfung": "Check",
    "Die Dateien bestehen die Prüfung nicht. Bitte zuerst beheben, dann ist die Vorschau verfügbar.":
        "The files fail the check. Fix them first, then the preview is available.",
    "Vorschau": "Preview",
    "Keine Änderungen gegenüber dem Remote.": "No changes compared to the remote.",
    # help
    "Tastenkürzel": "Keyboard shortcuts",
    "Suchfeld in der Kopfzeile öffnen. Funktioniert nicht, solange man in einem Eingabefeld schreibt.":
        "Open the search field in the header. Does not work while typing in an input field.",
    "Geöffnetes Menü oder den Einstellungsdialog schließen.": "Close the open menu or the settings dialog.",
    "Auf einem Menü-Knopf das Menü öffnen, im offenen Menü zwischen den Einträgen wechseln. Am Ende geht es von vorn los.":
        "On a menu button open the menu, in an open menu move between entries. At the end it starts over.",
    "Events können freie Tags und eigene Notizen enthalten. Tags lassen sich in der Eventliste filtern; die Volltextsuche findet auch Tags und Kommentare.":
        "Events can have free tags and own notes. Tags can be filtered in the event list; the full-text search also finds tags and comments.",
    "Der Dialog": "The dialog",
    "in der Kopfzeile gilt für diesen Browser. Er ist keine Zugriffsregel: Rechte bestimmt der Server.":
        "in the header applies to this browser. It is not an access rule: the server decides permissions.",
    "wie das Betriebssystem, hell oder dunkel. Der Schalter in der Kopfzeile wechselt zwischen hell und dunkel.":
        "as the operating system, light or dark. The toggle in the header switches between light and dark.",
    # dashboard JS
    "Kein Event ausgewählt": "No event selected",
    "Wähle ein Event in der Timeline oder einen Knoten im Graphen.": "Select an event in the timeline or a node in the graph.",
    "↑ ausgelöst durch:": "↑ caused by:",
    "↓ löst aus:": "↓ triggers:",
    "Notizen": "Notes",
    "Erkannt durch:": "Detected by:",
}


def language_of(request: Request) -> str:
    """The language of this request: the cookie when set, otherwise German. The switch is explicit
    (settings dialog), there is no Accept-Language auto-detection."""
    cookie = request.cookies.get(LANG_COOKIE)
    return cookie if cookie in LANGUAGES else DEFAULT_LANG


def t(key: str) -> str:
    """The translation of a German source text in the current language; German when unlisted."""
    if current_lang.get() == "en":
        return EN.get(key, key)
    return key
