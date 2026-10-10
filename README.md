# USB DMX for Home Assistant

USB DMX is a custom Home Assistant integration for controlling one local
DMX512 universe through a serial interface that implements the DMX USB Pro
protocol. It creates brightness lights, native normalized dimmer faders and
fade controls, plus number entities for raw channel values.

Version 0.1.0 has not been tested with physical hardware. Devices described as
compatible are classified by protocol only; see
[Supported adapters](SUPPORTED_ADAPTERS.md) before connecting equipment.

## V1 scope

V1 supports:

- one 512-channel universe per configured serial interface;
- the DMX USB Pro serial framing protocol at 57,600 baud;
- single-channel dimmers as Home Assistant lights and native 0–1 faders;
- persistent global and individual fade times with selectable inheritance;
- single raw channels as integer number entities from 0 through 255;
- Home Assistant UI configuration, fixture management, reconnect, diagnostics,
  English, and German.

It does not support OpenDMX, uDMX, Art-Net, sACN, OLA, MQTT, RDM, DMX input,
RGB fixture profiles, or multiple universes per interface. A USB-to-serial chip
such as FTDI, CP210x, or CH340 does not by itself identify a compatible DMX
protocol.

## How it works

Each configuration entry represents one physical interface. Light and number
entities update a shared 512-channel buffer. The integration sends a complete
DMX USB Pro frame after a channel changes. If the interface disconnects, output
entities become unavailable; fade settings remain editable. One reconnect
worker retries with increasing delays and replays the current complete frame
after reconnecting.

No add-on, external service, host package, broker, or runtime shell command is
used.

## Prerequisites

- Home Assistant 2026.9.0 or newer.
- Home Assistant OS on amd64 or aarch64 is the primary target. Home Assistant
  Container can be used when the serial device is passed into the container.
- A serial interface documented to implement the DMX USB Pro protocol.
- A correctly wired DMX512 line, preferably with galvanic isolation and proper
  termination.

## Installation

### HACS

1. Open HACS in Home Assistant.
2. Add `https://github.com/Schaack-Jan/hacs-dmx-usb` as a custom repository of
   type **Integration**.
3. Install **USB DMX** and restart Home Assistant when HACS requests it.

### Manual

Copy `custom_components/usb_dmx` into the `custom_components` directory in the
Home Assistant configuration directory, then restart Home Assistant. File
Editor, Samba share, or another supported file-management method can be used;
normal setup and operation do not require shell access.

See [Installation](INSTALLATION.md) for update, removal, and troubleshooting
steps.

## Setup and fixtures

1. Go to **Settings > Devices & services > Add integration** and select
   **USB DMX**.
2. Select a listed serial device or choose manual entry. A stable path under
   `/dev/serial/by-id` is preferred when available.
3. Confirm only interfaces known to implement the DMX USB Pro protocol.
4. Open the integration entry and add a **DMX fixture**.
5. Choose a unique address from 1 through 512 and either:
   - **Dimmer light**: Home Assistant brightness is scaled to the configured
     minimum and maximum. Off always sends zero.
   - **Raw channel**: the entity writes an unscaled integer from 0 through 255;
     fixture minimum and maximum do not change this range.

Fixtures can be renamed or moved to another free address without changing
their identity. Deleting a fixture removes its entities and clears its channel.

The integration options control startup and clean shutdown:

- **Restore saved light states** restores valid dimmer states known to Home
  Assistant; **Start at zero** leaves the initial zeroed universe unchanged.
- **Send blackout on clean shutdown** sends one zero frame before the serial
  connection closes. It cannot guarantee blackout after power loss, a host
  crash, or a removed cable.

## Native dimmer faders and fade controls

Each dimmer keeps its Light and adds three native controls on the existing USB
interface device. Each interface also has one global fade control, even before
any fixtures are added.

| Role | Native value | Purpose |
|---|---|---|
| Light | Brightness 0–255 | On/off, brightness and explicit transition |
| Level Number | 0–1, step 0.01, slider, no unit | Shared dimmer brightness fader |
| Fade time Number | 0–2 s, step 0.01, box | Saved individual duration |
| Fade source Select | `global` or `individual` | Choose the duration used by this dimmer |
| Global fade time Number | 0–2 s, step 0.01, box | Saved default for this interface only |

The three fade controls are configuration entities. Existing and new dimmers
default to `global`, with both durations initially 0 s. Individual time 0 s is
a valid immediate override. Changing the global time does not overwrite an
individual time; switching source preserves it. Settings persist across reloads
and restarts, send no DMX, and apply only to future brightness commands.

Duration priority is an explicit Light `transition` (including 0), then the
individual time when source is `individual`, otherwise the interface's global
time. Explicit Light transitions retain their 0–6553 s range. Configured fades
apply to Light on/off, native fader changes, and scenes that call these native
Light/Number paths. A new brightness command replaces a running fade from its
current output; the latest admitted command wins. Raw channels remain immediate
integer 0–255 controls and do not use fade settings.

The fader's native 0.5 means 50%, not 0.5%. A dashboard may multiply the state by
100 for a percentage display, but must send a value from 0 to 1. Conversion uses
`int(round(value * 255))` without first snapping service inputs to the 0.01 grid.
The display is `round(published_brightness / 255, 2)` while on, otherwise 0.

| Fader input | Brightness target | Completed fader display |
|---:|---:|---:|
| 0 | 0 (off) | 0 |
| 0.30 | 76 | 0.30 |
| 0.50 | 128 | 0.50 |
| 1 | 255 | 1 |

100% uses the fixture's configured DMX maximum, which need not be 255. Light
and fader publish calculated live logical output during a fade, not measured
lamp brightness or hardware feedback. Light remains on until exact logical zero
and then has no brightness attribute; rounding can make the fader show 0 just
before Light turns off. A 0.01 s setting is a resolution, not a 10 ms timing
guarantee. Event-loop scheduling, USB transport and the adapter limit timing;
software progress uses elapsed time rather than queuing missed frames.

## Native service and dashboard examples

Find each actual entity ID under **Settings > Devices & services > Entities**
or **Developer tools > States**. Home Assistant's registry assigns IDs; names,
translations and previous renames can affect them. Every `replace_with_*` ID
below is a placeholder, not a predicted entity ID.

This script sets both durations, selects the individual duration, moves to 50%,
then demonstrates an explicit immediate Light override:

```yaml
script:
  native_dmx_example:
    alias: Native DMX controls example
    sequence:
      - action: number.set_value
        target:
          entity_id: number.replace_with_global_fade_time_id
        data:
          value: 1
      - action: number.set_value
        target:
          entity_id: number.replace_with_individual_fade_time_id
        data:
          value: 0.5
      - action: select.select_option
        target:
          entity_id: select.replace_with_fade_source_id
        data:
          option: individual
      - action: number.set_value
        target:
          entity_id: number.replace_with_native_level_id
        data:
          value: 0.5
      - action: light.turn_off
        target:
          entity_id: light.replace_with_dimmer_light_id
        data:
          transition: 0
```

Service calls start fades without waiting for completion. In this example the
final action immediately replaces the fade and turns the dimmer off. Add an
appropriate automation delay if the previous fade should finish first.

An Entities card exposes the native fader and configuration controls:

```yaml
type: entities
title: DMX mixer
entities:
  - entity: number.replace_with_native_level_id
    name: Dimmer level (0–1)
  - entity: number.replace_with_individual_fade_time_id
  - entity: select.replace_with_fade_source_id
  - entity: number.replace_with_global_fade_time_id
```

For optional migration of template faders, see
[Manual helper migration](INSTALLATION.md#optional-manual-helper-migration).

## Automation example

Replace the placeholder with your fixture's registry entity ID:

```yaml
alias: Fade front dimmer on
triggers:
  - trigger: time
    at: "18:00:00"
actions:
  - action: light.turn_on
    target:
      entity_id: light.replace_with_dimmer_light_id
    data:
      brightness: 180
      transition: 2
```

## Availability and reconnect

An interface that cannot be opened during setup is retried by Home Assistant.
After a runtime write failure, output entities become unavailable and reconnect
attempts use delays of 1, 2, 5, 10, then 30 seconds. Further attempts remain at
30 seconds. Software fades continue while disconnected. A successful reconnect
restores the then-current complete frame. Fade configuration stays available.

## Diagnostics and logging

Download diagnostics from the USB DMX entry in **Settings > Devices &
services**. Diagnostics include connection state and counters, but redact the
serial path and exclude the DMX frame, product serial identifiers, and unique
IDs.

Use Home Assistant's **Enable debug logging** control on the integration entry
when available. For a persistent logger setting, add this to
`configuration.yaml` and restart Home Assistant:

```yaml
logger:
  logs:
    custom_components.usb_dmx: debug
```

Frames and channel contents are not logged. Disable debug logging after
collecting the relevant events.

## Removal

If the connected equipment should go dark, enable the clean-shutdown blackout
option or turn fixtures off before removal. Delete the USB DMX configuration
entry from **Settings > Devices & services**, then uninstall the repository in
HACS if it is no longer needed. A clean-shutdown blackout is best effort and
must not be treated as an electrical safety disconnect.

## Safety

DMX data cabling is low voltage, but dimmer packs and fixtures can switch
hazardous 230 V mains power. Mains installation and servicing must be performed
by a qualified person under applicable local rules. Disconnect and verify
mains power before wiring. Prefer a correctly certified, galvanically isolated
DMX interface; this project does not certify hardware or provide electrical
isolation.

## Support and license

Report integration defects through the
[GitHub issue tracker](https://github.com/Schaack-Jan/hacs-dmx-usb/issues).
Include Home Assistant and integration versions, adapter documentation, a
redacted diagnostic download, and relevant logs. Do not publish serial paths or
product serial numbers.

USB DMX is distributed under the [MIT License](LICENSE).
