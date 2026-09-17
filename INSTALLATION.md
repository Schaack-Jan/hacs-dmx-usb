# Installing USB DMX

This guide is for Home Assistant users. Home Assistant OS is the primary
environment, and routine installation and setup require no shell access.

## Before installation

Confirm all of the following:

- Home Assistant is version 2026.9.0 or newer.
- The adapter documentation explicitly states that it implements the DMX USB
  Pro serial protocol. A generic FTDI, CP210x, or CH340 USB-serial description
  is not enough.
- The adapter is connected to the Home Assistant host. Home Assistant
  Container users must also expose that serial device to the container.
- The DMX line is correctly wired and terminated. Galvanic isolation is
  strongly recommended.

No physical adapter has been validated by this project yet. Review
[Supported adapters](SUPPORTED_ADAPTERS.md).

## Install with HACS

1. Open **HACS** in Home Assistant.
2. Open the custom repositories dialog.
3. Enter `https://github.com/Schaack-Jan/hacs-dmx-usb` and select the
   **Integration** category.
4. Select **USB DMX**, choose **Download**, and restart Home Assistant if HACS
   requests it.
5. Continue with **Configure the interface** below.

## Install manually

1. Download the repository source for the version you intend to install.
2. Using File Editor, Samba share, or another supported file-management
   method, copy the `usb_dmx` directory into
   `<Home Assistant configuration>/custom_components/`.
3. Confirm the resulting directory is
   `<Home Assistant configuration>/custom_components/usb_dmx/` and contains
   `manifest.json`.
4. Restart Home Assistant.

Manual installation needs file access but does not require a terminal command.

## Configure the interface

1. Go to **Settings > Devices & services > Add integration**.
2. Search for and select **USB DMX**.
3. Select a serial device or choose manual path entry. Prefer a stable
   `/dev/serial/by-id` path when one is available.
4. Confirm setup only when the interface is documented to use the DMX USB Pro
   protocol.

The integration opens the selected path before saving it. A successful open
only proves that the serial device is accessible; it does not prove physical
DMX protocol compatibility.

## Add fixtures

1. Open the USB DMX integration entry.
2. Add a **DMX fixture**.
3. Enter a name and a unique address from 1 through 512.
4. Choose **Dimmer light** for Home Assistant brightness scaling or **Raw
   channel** for direct values from 0 through 255.
5. For a dimmer, set minimum and maximum output from 0 through 255. The minimum
   must not exceed the maximum. Off always writes zero.

Use the fixture's reconfigure control to change its name, type, address, or
dimmer range. Remove the fixture from the integration entry to remove its
entity.

## Configure startup and shutdown

Open the integration entry's options:

- **Restore saved light states** applies valid dimmer states retained by Home
  Assistant. **Start at zero** keeps the initially zeroed universe.
- **Send blackout on clean shutdown** sends a zero frame during an orderly
  reload, removal, or shutdown before closing the interface.

Blackout is best effort. It cannot be sent after power loss, a host crash, or
when the cable is already disconnected, and it is not an electrical safety
disconnect.

## Update

For HACS installations, open the USB DMX repository in HACS, install the
offered update, and restart Home Assistant if requested. Review release notes
before updating.

For manual installations, back up the Home Assistant configuration, replace
the complete `custom_components/usb_dmx` directory with the files from the new
version, and restart Home Assistant. Do not mix files from different versions.

## Remove

1. If appropriate for the connected equipment, turn fixtures off or enable
   clean-shutdown blackout.
2. In **Settings > Devices & services**, delete the USB DMX configuration
   entry.
3. If installed with HACS, uninstall USB DMX from HACS. For a manual install,
   remove only the `custom_components/usb_dmx` directory using the same file
   management method used for installation.
4. Restart Home Assistant if prompted.

## Troubleshooting

### USB DMX is not shown when adding an integration

Confirm the directory name is exactly `usb_dmx`, `manifest.json` is directly
inside it, Home Assistant meets the minimum version, and Home Assistant was
restarted after installation. Check **Settings > System > Logs** for loading
errors.

### The serial device is not listed

Reconnect the adapter and reload the USB DMX entry. Home Assistant Container
users must verify that the device is exposed to the container. Use manual path
entry only with a path that belongs to the intended adapter.

### Setup reports that it cannot connect

Check that the selected path still exists, no other integration or process is
using the serial device, and the Home Assistant host can access it. Prefer the
stable `/dev/serial/by-id` path if available.

### Setup succeeds but DMX equipment does not react

Opening a serial port does not identify its protocol. Confirm that the
interface documentation specifies DMX USB Pro framing, then check DMX polarity,
cable type, termination, fixture start address, and fixture mode. Do not infer
compatibility from the USB bridge chip alone.

### Entities become unavailable

The integration marks all entities unavailable after a transport error and
retries automatically. Check the cable and adapter, wait for reconnect, and
reload the integration if needed. The current complete frame is replayed after
a successful reconnect.

### Collect diagnostics and logs

From **Settings > Devices & services**, open the USB DMX entry and download
diagnostics. The device path and identifiers are redacted and frame contents
are excluded. Enable debug logging from the integration entry while reproducing
the problem, then disable it. Include the redacted diagnostics and relevant
logs in a [GitHub issue](https://github.com/Schaack-Jan/hacs-dmx-usb/issues).
