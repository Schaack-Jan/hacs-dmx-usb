# USB DMX: Fadezeiten und normierte Dimmer-Fader

Datum: 2026-10-09

Status: Architekturentwurf freigegeben; schriftliche Spezifikation zur Abnahme

Ziel-Branch: `feat/dimmer-fade-percentage`

Remote-Ausgangsstand: `f025a456138d2cb0884d850d5ffcf4a22e9af82a`

Ausgangs-Tree: `08485e88c9b3786e844b8cae99af081e0925dead`

## 1. Ziel und freigegebene Entscheidungen

Jeder bestehende und neue Dimmer erhält einen nativen, schreibbaren Fader mit
Wertebereich 0–1, der im bestehenden Mixer-Dashboard 0–100 % repräsentiert.
Fadezeiten lassen sich sowohl global je USB-Schnittstelle als auch individuell
pro Dimmer einstellen. Light und Fader zeigen während eines Fades den laufenden
logischen Helligkeitswert und verwenden denselben Befehlsweg.

Diese Spezifikation beschreibt den freigegebenen Umfang. Sie erlaubt noch keine
Produktivimplementierung. Nach ihrer Abnahme folgt ein detaillierter,
subagent-getriebener TDD-Implementierungsplan mit eigener Abnahme.

| ID | Anforderung |
|---|---|
| R01 | Eine globale Fade-Number je USB-Schnittstelle; keine schnittstellenübergreifende globale Einstellung. |
| R02 | Jeder Dimmer behält seine Light-Entity und erhält einen normierten Fader, eine individuelle Fade-Number und eine Fadequellen-Select-Entity. |
| R03 | Beide Fade-Numbers: 0–2 Sekunden einschließlich Grenzen, Schritt 0,01 s, Anfangswert 0 s. |
| R04 | Neue und alte Dimmer ohne Fade-Einstellungen verwenden zunächst die Quelle `global`. |
| R05 | Bei Quelle `individual` überschreibt die individuelle Zeit den globalen Standard; 0 s ist ein gültiger Override ohne Fade. |
| R06 | Globale Änderungen überschreiben keine individuellen Werte. Quellenwechsel erhalten die gespeicherte individuelle Zeit. |
| R07 | Globale Zeit, individuelle Zeit und Quellenwahl bleiben über Reload und Neustart gespeichert. |
| R08 | Fade gilt für Light-Befehle, Ein/Aus, Helligkeitsfader und Szenen, soweit diese die Light-/Number-Befehlswege aufrufen. |
| R09 | Ein ausdrücklich übergebenes Light-`transition`, einschließlich 0, hat Vorrang vor beiden konfigurierten Zeiten. |
| R10 | Faderbewegungen ersetzen laufende Fades ab dem aktuellen logischen und DMX-Zwischenwert; neuester zugelassener Befehl gewinnt. |
| R11 | Änderungen an Fadezeit oder Quelle wirken ausschließlich auf künftige Helligkeitsbefehle. Sie senden selbst kein DMX und lösen keinen Reload aus. |
| R12 | Fader: native Werte 0–1, Schritt 0,01, keine Einheit `%` auf dem normierten Wert. |
| R13 | Umrechnung entspricht dem vorgegebenen Template: `int(round(value * 255))`; Anzeige `round(brightness / 255, 2)` bei eingeschaltetem Dimmer, sonst 0. |
| R14 | Light und Fader zeigen Zwischenwerte statt sofortiger Zielwerte. Beim Ausblenden bleibt Light bis zur logischen Helligkeit 0 eingeschaltet. |
| R15 | Die Zwischenwerte sind berechnete logische Ausgabewerte, keine gemessene Lampenhelligkeit oder Hardware-Rückmeldung. |
| R16 | Startup-Verhalten `restore`/`zero`, initialer Nullframe und Shutdown-Blackout bleiben erhalten. Restore erfolgt einmal und ohne Fade. |
| R17 | Bei USB-Ausfall läuft der Software-Fade weiter; Reconnect sendet den dann aktuellen vollständigen Frame. |
| R18 | Vorhandene Light-/Raw-Unique-IDs, Fixture-UUIDs, Geräteidentität und Raw-Kanalverhalten bleiben erhalten. |
| R19 | Nutzer-Template-Helper und Dashboard-YAML werden nicht automatisch verändert, gelöscht oder übernommen. |
| R20 | Ausführung nach Planfreigabe subagent-getrieben, test-first, mit unabhängigen Reviews, Fortschrittstracking und Conventional Commits auf dem Feature-Branch. |

## 2. Grenzen und Kompatibilität

- Mindestversion Home Assistant bleibt `2026.9.0`; Entwicklungs-/Testbasis ist
  weiterhin `homeassistant==2026.9.2` mit Python `>=3.14.2` und dem bestehenden
  Lockfile. Diese Erweiterung benötigt keine neue externe Laufzeitabhängigkeit.
- Backend, DMX-USB-Pro-Protokoll, Single-Universe-Modell und Hardwareclaims
  bleiben unverändert. Der Controller ist weiterhin der einzige DMX-Schreiber.
- Raw-Fixtures behalten ihre Number 0–255, Schritt 1, unmittelbare Writes und
  bestehende Identität. Die Fade-Controls verändern Raw-Kanäle nicht.
- Eine gemeinsame effektive Zeit gilt für Einblenden, Ausblenden und Änderungen
  zwischen zwei Helligkeiten. Getrennte Fade-in-/Fade-out-Zeiten sind nicht Teil
  dieses Umfangs.
- Der Wertebereich 0–2 s betrifft die neuen Fade-Einstellungen. Bestehende
  explizite Light-Transitions behalten den bisherigen akzeptierten Bereich
  0–6553 s und dieselbe direkte Typ-/Endlichkeitsvalidierung.
- Die Konfigurationsauflösung 0,01 s ist keine 10-ms-Echtzeitgarantie.
  Event-Loop-Scheduling, Transport und Gerät begrenzen die tatsächliche Ausgabe.
- Keine Änderungen an Repository-Sichtbarkeit, Topics, Branch-Regeln,
  Berechtigungen oder Releases innerhalb dieses Features.

## 3. Entities und gespeicherte Daten

| Entity | Eigentümer | Native Eigenschaften | Unique-ID-Regel |
|---|---|---|---|
| Bestehende Light | Fixture-Subentry | Brightness 0–255, Transition | Unverändert: `<entry_id>_<fixture_uuid>` |
| Helligkeitsfader | Fixture-Subentry | Number 0–1, Schritt 0,01, Slider, ohne Einheit | `<entry_id>_<fixture_uuid>_level` |
| Individuelle Fadezeit | Fixture-Subentry | Number 0–2 s, Schritt 0,01, Box, Dauer | `<entry_id>_<fixture_uuid>_fade_time` |
| Fadequelle | Fixture-Subentry | Select mit stabilen Optionen `global`, `individual` | `<entry_id>_<fixture_uuid>_fade_source` |
| Globale Fadezeit | Parent-Config-Entry | Number 0–2 s, Schritt 0,01, Box, Dauer | `<entry_id>_global_fade_time` |

Alle Entities gehören zum vorhandenen parent-owned USB-Interface-Device.
Neue Controls sind standardmäßig aktiviert. Die globale Fadezeit ist auch ohne
Dimmer als Parent-Einstellung vorhanden. Individuelle Zeit und Quellenwahl sind
Konfigurations-Entities; der Helligkeitsfader ist eine reguläre Steuer-Entity.
Entity-Anzeigenamen erhalten rollenbezogene EN/DE-Übersetzungen; vom Nutzer
vergebene Fixture-Namen bleiben erhalten. Tatsächliche `entity_id`s sind von
Home Assistants Registry abhängig, nicht vertraglich aus Namen ableitbar.

Die globale Zeit wird in `entry.options` unter `global_fade_time` gespeichert.
Individuelle Zeit und Quelle liegen in den Daten des zugehörigen Fixture-
Subentries unter `fade_time` und `fade_source`. Werte werden JSON-kompatibel
gespeichert; Zeiten werden auf Hundertstelsekunden normalisiert. Fehlende neue
Felder bedeuten 0 s beziehungsweise `global`, ohne bestehende UUIDs zu ändern.

Für Fade-Einstellungen werden endliche numerische Werte zwischen 0 und 2
akzeptiert und auf zwei Dezimalstellen normalisiert. Bool, NaN, Unendlich,
Strings und Werte außerhalb des Bereichs werden vor jeder Änderung abgewiesen.
Die Quelle muss exakt `global` oder `individual` sein. Ungültige vorhandene
Speicherdaten werden kontrolliert als Konfigurationsfehler behandelt; sie
dürfen nicht unbemerkt eine beliebige Fadezeit erzeugen.

Settings-Setter verwenden die HA-Config-Entry-/Subentry-Update-APIs und mergen
die aktuellen gespeicherten Daten. Anschließend aktualisieren sie den gemeinsamen
Laufzeitzustand. Unbekannte Optionsfelder und alle Fixture-Felder bleiben
erhalten. Fixture-Reconfigure muss die neuen Felder ausdrücklich übernehmen;
der bisherige Sechs-Felder-Neuaufbau darf Overrides nicht verwerfen.

Normale Fade-Settings-Änderungen benötigen keinen Update-Listener und keinen
Integrations-Reload. Der vorhandene Optionsflow für Startup/Blackout kann seine
bestehende Reload-Semantik behalten. Bei gleichzeitig laufenden Einstellungen
und Fixture-Flows verhindern aktuelle Daten-Merges und gemeinsame
Schreibkoordination verlorene Updates oder das Wiederanlegen gelöschter Kinder.

## 4. Gemeinsamer Dimmer-Laufzeitzustand

Eine Laufzeitinstanz je Dimmer-UUID wird vor dem Forwarding der Plattformen
angelegt. Light, Helligkeitsfader und Fade-Controls referenzieren dieselbe
Instanz, statt einander über Home-Assistant-Serviceaufrufe anzusteuern.

Die Instanz hält mindestens:

- aktuelle logische Helligkeit als kontinuierlichen Wert 0–255;
- angeforderten Zielwert 0–255;
- separat gemerkte letzte eingeschaltete Helligkeit;
- individuelle Fadezeit und Quellenwahl sowie Zugriff auf die globale Zeit;
- eine gemeinsame faire Befehls-/Removal-Koordination mit terminalem
  Löschzustand;
- eine Generationskennung für Übergänge und entfernbare Zustandsabonnements;
- einen einmaligen, plattformreihenfolgeunabhängigen Restore-Status.

Die logische Helligkeit darf nicht aus DMX zurückgerechnet werden:
konfigurierte Min-/Max-Werte, Rundung und identische Grenzen machen diese
Umkehrung mehrdeutig. Der gespeicherte DMX-Frame und der logische Wert bleiben
deshalb getrennte Größen, deren Fade-Fortschritt dieselbe Zeitbasis verwendet.

Light ohne explizite Helligkeit schaltet auf die zuletzt angeforderte positive
Helligkeit oder, wenn keine existiert, auf 255. Ausblenden verändert diese
Merkinformation nicht. Ein neuer positiver Zielwert aktualisiert sie; kleine
positive normierte Eingaben, die zu Brightness 0 runden, gelten als Aus.

## 5. Befehlsweg und Fortschritt

1. Eingabe strikt validieren und den gemeinsamen Fixture-Lock betreten.
   Nach terminaler Entfernung darf kein Begleit-Entity einen Befehl zulassen.
2. Effektive Dauer beim Zulassen des Befehls einmal festhalten:
   explizites Light-`transition`, sonst individuelle Zeit bei `individual`,
   sonst globale Zeit.
3. Aktuellen logischen Wert und aktuellen DMX-Framewert erfassen; eine laufende
   Transition ersetzen und ihre alten Fortschrittsmeldungen entwerten.
4. Bei Dauer 0 direkt auf das Ziel setzen. Sonst startet der Controller genau
   einen ersetzbaren Transition-Task für diese Adresse.
5. Fortschritt aus monoton verstrichener Zeit bestimmen. Logische Helligkeit
   und physischer DMX-Wert interpolieren getrennt zwischen ihren jeweiligen
   Start-/Zielwerten, aber mit identischem Fortschritt.
6. Bei Abschluss beide Ziele exakt erreichen. Ein überholter oder abgebrochener
   Task darf weder Fortschritt noch einen finalen Wert nachträglich publizieren.

Die physische DMX-Interpolation bleibt linear zwischen aktuellen DMX-Bytes,
wie im bisherigen Controller. Das Umrechnen jedes logischen Zwischenschritts
durch den Min-/Max-Bereich würde insbesondere am Übergang Aus/Minimum das
bisherige Verhalten verändern und gehört nicht zu diesem Design.

Fortschrittsmeldungen erfolgen auch bei unverändertem gerundetem DMX-Byte,
damit beispielsweise bei `minimum == maximum` die logische Anzeige nicht
stehenbleibt. Der Controller bleibt Task- und Frame-Eigentümer; es gibt keinen
zweiten unabhängigen Timer pro Entity. Die bestehende Zielrate von ungefähr
40 Fortschrittsschritten pro Sekunde bleibt eine Obergrenze, kein Echtzeitversprechen.
Zeitbasierte Berechnung verhindert die kumulative Verlängerung durch wiederholte
Sleep-plus-Write-Schritte; langsame Writes werden nicht durch Frame-Bursts
kompensiert. Endpunkt und Abschlussmeldung müssen immer gesendet werden.

HA-State-Updates sind ereignisgesteuert und werden pro Entity auf tatsächlich
geänderte sichtbare Werte dedupliziert. Keine 40-Hz-Updates an unveränderte
Fade-Controls, keine blockierende Arbeit in Listenern, keine Frame-Logs.

## 6. Exakte Fader- und Light-Semantik

Der Helligkeitsfader ist ein Number ohne Einheit, nicht ein Sensor und nicht
eine Number mit dem irreführenden nativen Wert 0,50 %. Das Dashboard kann den
normierten Wert mit 100 multiplizieren und damit 50 % anzeigen.

Schreibwerte sind endliche numerische Eingaben von 0 bis 1. Bool und Strings
sind ungültig. Wie beim vorgegebenen Template können Serviceaufrufe auch
Zwischenwerte außerhalb des UI-Schrittrasters liefern; diese werden nicht
vorab auf 0,01 quantisiert. Erst `int(round(value * 255))` bestimmt Brightness.
Python-/Jinja-Standardrundung wird beibehalten, beispielsweise:

| Eingabe | Light-Brightness-Ziel | Anzeige nach Abschluss |
|---:|---:|---:|
| 0 | 0 / Aus | 0 |
| 0,30 | 76 | 0,30 |
| 0,50 | 128 | 0,50 |
| 1 | 255 | 1 |

Die bestehende Zielabbildung auf DMX bleibt erhalten:
Brightness 0 ergibt DMX 0; Brightness 1–255 wird deterministisch in den
konfigurierten Minimum-/Maximum-Bereich abgebildet. 100 % bedeutet daher die
konfigurierte Maximalhelligkeit, nicht zwingend DMX 255.

Light ist eingeschaltet, solange die kontinuierliche logische Helligkeit
größer als 0 ist; nicht der gerundete Anzeigewert entscheidet über Ein/Aus.
Die veröffentlichte Light-Brightness wird auf einen Integer gerundet und bei
noch positiver logischer Helligkeit auf mindestens 1 begrenzt. Erst der exakte
logische Endwert 0 schaltet Light aus. Der Helligkeitsfader liest diese
veröffentlichte Brightness gemäß der freigegebenen Template-Semantik.
Aufgrund der Rundung auf zwei Dezimalstellen kann der Fader bereits 0 anzeigen,
während eine sehr geringe positive Helligkeit noch ausgeblendet wird; auch das
entspricht dem Template und darf Light nicht vorzeitig ausschalten.
Dies ist eine bewusste Änderung der bisherigen optimistischen Ziel-/Off-Anzeige;
die entsprechenden bisherigen Transition-State-Tests werden ersetzt, nicht
pauschal gelöscht. Ohne Fade bleiben erfolgreiche State-Änderungen unmittelbar.

## 7. Restore, Unload, Entfernung und Ausfall

Fade-Einstellungen sind Konfiguration und werden immer gespeichert, unabhängig
von Startup-Verhalten `restore` oder `zero`. Ihre Initialisierung darf keinen
Helligkeitswrite auslösen.

Helligkeit wird einmal zentral entsprechend der bestehenden Startup-Option
wiederhergestellt. Vorhandene gespeicherte Light-Zustände bleiben die primäre
Rückwärtskompatibilitätsquelle. Merkinformation für die letzte eingeschaltete
Helligkeit wird zusätzlich über HA-konforme Restore-Extra-Daten gesichert,
weil die echte Light-State-Darstellung bei Aus kein Brightness-Attribut liefert.
Companion-Entities dürfen gemeinsame Daten bereitstellen, aber keine unabhängigen,
konkurrierenden Restore-Writes ausführen. Beim deaktivierten Light muss ein
aktiver Helligkeitsfader denselben einmaligen Restore ermöglichen; deaktivierte
Begleit-Entities verändern die Funktionsfähigkeit des Dimmerzustands nicht.
Sind alle Helligkeits-Controls deaktiviert, wird kein Restore-Ausgabewrite
durch reine Fade-Konfigurations-Entities angestoßen.

Beim echten Löschen eines Fixtures gewinnt ein gemeinsamer terminaler
Löschzustand über alle Companion-Befehle. Laufende Befehle werden geordnet
beendet, die Transition wird abgebrochen und der Kanal abschließend auf 0
gesetzt. Wartende oder später eintreffende Befehle dürfen den Kanal nicht
wiederbeleben. Cleanup ist idempotent; jeder Listener und Timer wird entfernt.
Geschwister und das parent-owned Interface-Device bleiben erhalten.

Beim normalen Unload werden ebenfalls alle Companion-Befehle gestoppt und
Abonnements entfernt, aber das Entfernen einer Entity ist kein Fixture-Löschen.
Die bisherige Shutdown-Blackout-Option entscheidet weiterhin über den finalen
DMX-Blackout. Typ-, Adress- und Bereichsänderungen per Reconfigure behalten
UUIDs, behandeln obsolet gewordene Companion-Registry-Einträge kontrolliert
und dürfen keine alten Transition-Tasks an einer nicht mehr zugeordneten Adresse
zurücklassen.

Bei USB-Ausfall bleibt der Dimmerzustand gemeinsam konsistent und alle
ausgabebezogenen Entities spiegeln die bestehende Unverfügbarkeit. Der
Software-Fade läuft weiter. Der bestehende Reconnect-Worker spielt den zu
diesem Zeitpunkt aktuellen vollständigen Frame ab; kein Restart beim ursprünglichen
Fade-Ziel und kein zweiter Reconnect-Worker.

## 8. Architekturgrenzen und betroffene Dateien

| Bereich | Verantwortung |
|---|---|
| `controller.py` | Monotone Transition-Zeitbasis, Fortschrittsmeldung, Generation/Cancellation, ausschließliche Frame-Schreibhoheit. |
| Neues fokussiertes Dimmer-Runtime-Modul | Gemeinsame logische Zustände, Ziel-/Merkinformation, Commands, Settings-Zugriff, Restore-/Removal-Koordination. |
| `__init__.py` | Gemeinsame Runtime vor Plattformsetup anlegen, Select-Plattform hinzufügen, kompletter Unload. |
| `models.py`, `const.py`, `config_flow.py` | Persistente Fade-Felder, Defaults, Validierung, Reconfigure-Erhalt und vorhandene Options-Kompatibilität. |
| `entity.py`, `light.py`, `number.py`, neues `select.py` | Rollenbezogene IDs, gemeinsame Runtime, native Entities, Eigentümerschaft, Listener-Cleanup und Live-Anzeige. |
| Tests, EN/DE-Übersetzungen, README/INSTALLATION | Nachweise, Rollenbezeichnungen, Wertebereiche, Beispiele und optionale Helper-Migration. |

Eine dedizierte Settings-Koordination kapselt aktuelle Daten-Merges und
Konfigurationszugriffe; die Implementierungsplanung bestimmt ihre konkrete
Dateigrenze ohne neue technische Produktanforderungen. Keine allgemeine
Coordinator-/Backend-Neuentwicklung oder unrelated Refactorings.

## 9. Abnahme- und Regressionsnachweise

Der spätere Plan muss jede Anforderung R01–R20 einer überprüfbaren Aufgabe
zuordnen. Mindestens folgende automatisierten Nachweise gehören dazu:

- Entities für bestehende/neue Dimmer; Eltern-/Subentry-Zuordnung, stabile
  Light-/Raw-IDs, rollenbezogene Companion-IDs und Interface-Isolation.
- Grenzen 0/2 s, 0,01-s-Einstellung, ungültige numerische Typen/NaN/Inf,
  exakte Prozent-Endpunkte, Halbwert-Rundung, kleine und off-grid Eingaben.
- Global-/Individual-Vererbung, expliziter individueller 0-s-Override,
  Quellenwechsel, Persistenz nach Reload/Neustart, parallele Settings-Änderungen,
  keine Reloads oder DMX-Writes durch Settings-Setter.
- Reconfigure erhält Fade-Felder und alle bestehenden Optionen; andere
  USB-Interfaces und Raw-Fixtures bleiben unverändert.
- Live-Zwischenwerte in Light und Number, Fade-off bis exakt 0 einschließlich
  Helligkeit 1 und Zwischenwerten unter 0,5, letzte positive
  Zielhelligkeit, explizite Transition-Priorität und sofortiger 0-s-Pfad.
- Fader-Sturm/wechselnde Light- und Number-Befehle, abgebrochene Generationen,
  letzte zulässige Eingabe gewinnt und keine verwaisten Tasks.
- Min-/Max-Sonderfälle einschließlich 0/0, positiver identischer Grenzen und
  eingeschränkter Bereiche; logische Meldungen trotz unverändertem DMX-Byte.
- Monotone Zeitbasis unter langsamen Backend-Writes, exakte Endpunkte,
  kurze 0,01-s-Fades und ausbleibende Burst-Kompensation.
- Einmaliger Restore trotz Plattformreihenfolge, echte Off-Extra-Daten,
  deaktivierte Light-/Number-Companions, beschädigte Restore-Daten,
  Startup-Zero und keine konkurrierenden Restore-Ausgaben.
- Löschen während Fade oder wartender Companion-Befehle, finaler Nullwrite,
  Idempotenz, Listener-Cleanup, Geschwistererhalt, Typ-/Adresswechsel.
- USB-Ausfall während Fade, Offline-Fortschritt, Reconnect-Replay,
  Unload ohne unerwünschten Blackout und unveränderte Startup-Sicherheit.
- Native Light-/Number-Serviceaufrufe und Scene-Aufrufe in der realen
  HA-Testumgebung, EN/DE-Parität sowie korrekte UI-Units/Select-Optionswerte.

Nach Produktivimplementierung müssen Gesamtsuite, Ruff-Lint/-Format,
Lockfile-Kohärenz, Hassfest und HACS validiert werden. Die genaue Ausgangszahl
der Tests wird frisch ermittelt; frühere Ergebniszahlen gelten nicht als
aktueller Nachweis. GitHub-Workflows werden anhand ihrer realen Ergebnisse
geprüft, nicht ausschließlich aus lokalen Läufen abgeleitet.

Manuelle Hardware-Abnahme: mehrere gleichzeitig bewegte Fader, Ein/Aus-Fades,
globale und individuelle Zeiten, USB-Unplug/Replug, Neustart/Reload,
0,01-s-/2-s-Einstellungen sowie ein längerer Dauerbetrieb. Ohne angeschlossene
Hardware werden diese Punkte ausdrücklich als nicht physisch verifiziert
ausgewiesen.

## 10. Ausführung, Grenzen der Freigabe und Dokumentation

Nach Abnahme dieser Datei wird der detaillierte Plan geschrieben. Erst dessen
Abnahme erlaubt Produktivimplementierung. Die gewählte Ausführungsmethode ist
bereits `subagent-driven` und darf nicht stillschweigend gewechselt werden.

Je unabhängig testbarem Arbeitspaket: frischer Implementierungs-Subagent,
RED-Nachweis, minimale GREEN-Implementierung, unabhängige Anforderungs- und
Qualitätsreviews, relevante frische Verifikation und Conventional Commit.
Fortschritts-Checkboxen dokumentieren erledigte Aufgaben, Befunde und
Nachbesserungen; abschließend erfolgt eine Whole-Branch-Review.

Alle zukünftigen Commits bleiben auf `feat/dimmer-fade-percentage`; kein
direkter Main-Push, Force-Push, Merge oder Release ohne gesonderten Auftrag.
Die ursprünglichen Nutzer-Uploads und fremde Änderungen bleiben unberührt.

Die Benutzerdokumentation erklärt neue Entity-Rollen, das 0–1-/0–100-%-
Verhältnis, die Fade-Priorität, die neue Live-Light-Anzeige und die Grenzen der
Zeitgenauigkeit. Zur optionalen Migration bestehender Template-Fader werden
neue native Entity-IDs manuell im Dashboard ersetzt und anschließend nicht mehr
benötigte Helper erst nach Nutzerentscheidung entfernt. Es wird keine
Identitätsgleichheit mit `number.dmx_lampe_links_fader` behauptet.

## 11. Fachliche Grundlage

Geprüft wurden der Ausgangscode und die installierte HA-2026.9.2-Testbasis,
insbesondere Number-Servicevalidierung, Light-State-Darstellung,
Config-Entry-/Subentry-Updates, Optionsflow-Reload und Restore-Extra-Daten.
Das vom Nutzer gelieferte Template definiert die verbindliche Rundungssemantik.

- [HA Number-Entity-Dokumentation](https://developers.home-assistant.io/docs/core/entity/number/)
- [HA Light-Entity-Dokumentation](https://developers.home-assistant.io/docs/core/entity/light/)
- [HA Select-Entity-Dokumentation](https://developers.home-assistant.io/docs/core/entity/select/)
- [HA Config-Entry-Dokumentation](https://developers.home-assistant.io/docs/config_entries_index/)
- [HA Optionsflow-Dokumentation](https://developers.home-assistant.io/docs/core/integration/options_flow/)
- [HA Restore-State-Implementierung](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/helpers/restore_state.py)

Die Implementierungsplanung prüft konkrete HA-API-Signaturen erneut an der
festgesetzten Entwicklungsbasis. Die öffentlichen Dokumentationslinks dienen
als Referenzen, nicht als Freigabe für ein unangekündigtes Versionsupgrade.
