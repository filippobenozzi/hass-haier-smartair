# Haier AC Bridge for Home Assistant

A custom **Home Assistant** integration to control Haier air conditioners through a local SmartAir2-compatible bridge.

## Acknowledgements

This integration was made possible by the outstanding work of [fastfend](https://github.com/fastfend), which inspired this project.

## Platform Support

This project is **Home Assistant only**.

## Features

- Automatic device discovery from the bridge
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
3. Enter:
   - `host` (bridge IP address)
   - `token`
   - optional behavior settings (`polling`, `use_fan_mode`, `use_dry_mode`, `health_mode_type`, `swing_type`, custom names)

## Integration Details

- Domain: `haier_ac_bridge`
- Path: `custom_components/haier_ac_bridge`
- Setup method: UI config flow (no mandatory YAML)

## Notes

- The bridge must be reachable on your local network at port `10000`.
- If the token is invalid, setup will fail.
