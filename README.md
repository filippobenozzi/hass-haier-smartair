# Haier AC Bridge for Home Assistant

Integrazione custom per **Home Assistant** che controlla i climatizzatori Haier tramite bridge locale compatibile con SmartAir2.

## Ringraziamenti

Questa integrazione e stata resa possibile dallo straordinario lavoro di [fastfend](https://github.com/fastfend), da cui ho preso ispirazione.

## Supporto piattaforma

Questo progetto e **solo per Home Assistant**.

## Funzionalita

- Rilevamento automatico dei dispositivi dal bridge
- Entita `climate` per ogni AC
- Modalita HVAC: `off`, `cool`, `heat`, `auto`, `fan_only`, `dry`
- Controllo temperatura target
- Controllo velocita ventola (`low`, `medium`, `high`, `auto`)
- Lettura temperatura e umidita correnti
- Swing combinato (`BOTH`) oppure swing separato (`INDIVIDUAL`)
- Switch opzionali: `Health Mode`, `Dry Mode`, `RightLeft Swing`, `UpDown Swing`
- Polling configurabile

## Installazione (HACS)

1. Apri HACS in Home Assistant.
2. Vai su **Integrations**.
3. Menu in alto a destra -> **Custom repositories**.
4. Aggiungi questo repository come categoria **Integration**.
5. Installa **Haier AC Bridge**.
6. Riavvia Home Assistant.

## Configurazione

1. Vai in **Settings -> Devices & Services -> Add Integration**.
2. Cerca **Haier AC Bridge**.
3. Inserisci:
   - `host` (IP del bridge)
   - `token`
   - opzioni comportamentali (`polling`, `use_fan_mode`, `use_dry_mode`, `health_mode_type`, `swing_type`, nomi personalizzati)

## Struttura integrazione

- Dominio: `haier_ac_bridge`
- Percorso: `custom_components/haier_ac_bridge`
- Setup: config flow UI (senza YAML obbligatorio)

## Note

- Il bridge deve essere raggiungibile in LAN sulla porta `10000`.
- Se il token e errato, l'integrazione non completa il setup.
