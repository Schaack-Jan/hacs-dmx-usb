# USB DMX V1 Design

## Goal

Build a HACS-compatible Home Assistant custom integration named **USB DMX**
(`usb_dmx`) that controls one DMX512 universe per locally attached USB
interface without Art-Net, sACN, OLA, MQTT, an add-on, or an external service.

The approved product requirements are the attached master prompt from
2026-09-16. This document records the implementation choices needed to turn
those requirements into a release-ready V1.

## Supported environment

- Home Assistant OS is the primary platform; Home Assistant Container is
  supported when the container is granted the serial device.
- Minimum Home Assistant version: `2026.9.0`.
- Initial integration version: `0.1.0`.
- Python follows the version bundled with Home Assistant 2026.9.
- `serialx==1.10.0` is the only runtime dependency. This is the version pinned
  by Home Assistant Core 2026.9.2 and provides native asyncio serial I/O.
- HACS repository type: Integration, one directory under `custom_components`.

## V1 hardware scope

V1 implements one backend: serial interfaces using the DMX USB Pro framing
protocol. The backend is selected by protocol, never by a single brand or
VID/PID. A user can always enter a serial path manually. USB discovery is only
declared for identifiers that identify a DMX product; generic FTDI, CP210x, and
CH340 identifiers are not sufficient on their own.

OpenDMX/FTDI bit-bang devices and uDMX/libusb devices are intentionally not
implemented in V1. Their dependable operation requires native USB access,
timing, or libraries that cannot be assumed inside the Home Assistant Core
container. The backend boundary keeps future implementations possible without
claiming unsupported hardware today.

## Architecture

```text
Light / Number entities
          |
          v
     DmxController ---- bytearray(512), lock, transitions, reconnect
          |
          v
       DmxBackend
          |
          v
 SerialProBackend ---- serialx async serial connection
```

### Runtime ownership

Each config entry represents one physical, single-output interface and owns one
`UsbDmxRuntime` through typed `ConfigEntry.runtime_data`. The runtime owns the
backend and controller. `async_setup_entry` connects before forwarding the
light and number platforms. `async_unload_entry` unloads platforms, optionally
sends blackout, then cancels controller tasks and closes the backend.

The controller is the only writer of the 512-slot buffer. All mutations use an
async lock. Backend errors atomically mark the controller unavailable and start
one bounded reconnect worker with exponential backoff. A successful reconnect
re-sends the complete current frame and publishes availability to entities.

### Backend contract

`DmxBackend` exposes `connect`, `disconnect`, `send_frame`, `probe`, and
`reconnect`, plus immutable capabilities. `SerialProBackend` builds protocol
packets as:

```text
0x7E | label 0x06 | payload length LE | start code 0x00 + 512 slots | 0xE7
```

It opens the path with `serialx.async_serial_for_url`, performs no blocking I/O
in the Home Assistant event loop, serializes writes, and closes idempotently.
The protocol-class backend declares hardware refresh, so normal operation sends
on change rather than running a 40 Hz Home Assistant task.

### Configuration and fixture storage

The config flow stores immutable connection data: backend, serial path, and a
stable interface identifier. A reconfigure step changes the serial path while
preserving the config entry identity. USB discovery always requires explicit
user confirmation and derives a unique ID from a device serial number when
available, otherwise from a normalized stable path.

The options flow stores mutable settings and fixtures:

- startup behavior (`restore` by default or `zero`)
- blackout on clean shutdown (`false` by default)
- fixture list containing UUID, type, name, address, minimum, and maximum

The options flow is a menu for settings plus add/edit/delete fixture actions.
It rejects addresses outside 1..512, overlapping fixtures, duplicate names in
the same entry, invalid ranges, and unknown fixture types. Completing a change
reloads the config entry so the platform set exactly matches stored fixtures.

### Entities and registry identity

V1 fixture types are:

- `dimmer`: a brightness `LightEntity`
- `raw`: a boxed `NumberEntity` with range 0..255 and step 1

All fixtures remain entities of the one physical interface device to avoid a
device-registry entry per single DMX slot. Entity unique IDs are
`<entry_id>_<fixture_uuid>`; user-visible names never determine identity.
Fixture deletion also removes its stale entity-registry entry.

Dimmer brightness is mapped through its configured minimum and maximum. Off is
always DMX 0; on without brightness restores the last non-zero brightness.
`RestoreEntity` restores Home Assistant state when startup behavior is
`restore`; the controller buffer itself always starts as deterministic zeros.
Transitions are implemented in the controller as replaceable asyncio tasks and
use the current slot value as their start.

### Availability and failure behavior

Entities are available only while their shared controller is connected. A
missing device during initial setup raises `ConfigEntryNotReady`. A runtime I/O
failure never escapes into Home Assistant: the failed write marks all entities
unavailable, closes the broken handle, and schedules reconnect without busy
polling. The current full frame is restored after reconnect.

### Diagnostics and logging

Diagnostics expose backend name, connection state, redacted device path,
channel count, fixture count, refresh mode, last successful frame timestamp,
and reconnect count. They never expose the full DMX frame. Connection state
changes are logged once; frames are not logged.

## Testing strategy

Tests use a fake backend and Home Assistant's custom-component test patterns.
Pure protocol and controller tests do not require Home Assistant or hardware.
Coverage includes packet encoding, boundary addresses, concurrent mutations,
transitions, setup/unload, config and options flows, registry identity,
restore/availability, diagnostics, send failures, and reconnect.

CI runs Ruff, pytest, Home Assistant hassfest, and HACS validation. A separate
manual hardware checklist covers discovery, channel 1 and 512, hot unplug,
replug, reload, restart, rapid sliders, and a one-hour soak test.

## Documentation and release scope

The repository includes README, INSTALLATION, SUPPORTED_ADAPTERS, MIT license,
English and German translations, HACS metadata, and GitHub Actions. Hardware is
classified as Tested, Compatible by protocol, Experimental, or Unsupported;
the Tested list remains empty until physical test evidence exists. V1 does not
claim OpenDMX, uDMX, RGB, RDM, DMX input, fixture profiles, or multi-universe
support.

## Primary references reviewed

- Home Assistant config flow: https://developers.home-assistant.io/docs/core/integration/config_flow/
- Home Assistant options flow: https://developers.home-assistant.io/docs/core/integration/options_flow/
- Home Assistant manifest and USB matching: https://developers.home-assistant.io/docs/creating_integration_manifest/
- Home Assistant setup failures: https://developers.home-assistant.io/docs/integration_setup_failures
- Home Assistant entity model: https://developers.home-assistant.io/docs/core/entity/
- Home Assistant diagnostics: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/diagnostics/
- Home Assistant serialx migration: https://developers.home-assistant.io/blog/2026/04/27/pyserial-to-serialx/
- serialx migration/API: https://raw.githubusercontent.com/puddly/serialx/refs/heads/dev/docs/how-to/pyserial-migration.md
- HACS integration repository requirements: https://www.hacs.xyz/docs/publish/integration/
- Standalone hassfest action: https://github.com/home-assistant/actions
