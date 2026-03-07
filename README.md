# Haier AC Bridge for Home Assistant

A custom **Home Assistant** integration to control Haier air conditioners through:
- a local SmartAir2-compatible Android bridge, or
- an experimental direct LAN mode inspired by `bstuff/haier-ac-remote`.

## Acknowledgements

This integration was made possible by the outstanding work of [fastfend](https://github.com/fastfend), which inspired this project.

## Platform Support

This project is **Home Assistant only**.

## Features

- Bridge mode (`host` + `token`) compatible with fastfend HaierACBridge app
- Direct mode (`host` + `mac`) experimental, no Android bridge app
- One `climate` entity per AC
- HVAC modes: `off`, `cool`, `heat`, `auto`, `fan_only`, `dry`
- Target temperature control
- Fan speed control (`low`, `medium`, `high`, `auto`)
- Current temperature and humidity reporting
- Combined swing (`BOTH`) or separate swing controls (`INDIVIDUAL`)
- Optional switches: `Health Mode`, `Dry Mode`, `RightLeft Swing`, `UpDown Swing`
- Configurable polling interval

## Installation (HACS)

1. Open HACS in Home Assistant.
2. Go to **Integrations**.
3. Open the top-right menu and select **Custom repositories**.
4. Add this repository URL with category **Integration**.
5. Install **Haier AC Bridge**.
6. Restart Home Assistant.

## Configuration

1. Go to **Settings -> Devices & Services -> Add Integration**.
2. Search for **Haier AC Bridge**.
3. Select connection mode and enter:
   - `Bridge`: `host` (bridge IP) + `token`
   - `Direct (Experimental)`: `host` (AC IP) + `mac` (12 hex chars)
   - optional behavior settings (`polling`, `use_fan_mode`, `use_dry_mode`, `health_mode_type`, `swing_type`, custom names)

## Integration Details

- Domain: `haier_ac_bridge`
- Path: `custom_components/haier_ac_bridge`
- Setup method: UI config flow (no mandatory YAML)

## Notes

- Bridge mode requires the Android bridge app reachable on port `10000`.
- Direct mode uses TCP port `56800` and can be firmware-dependent.
