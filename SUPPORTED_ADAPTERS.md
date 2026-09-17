# USB DMX adapter status

USB DMX V1 implements the DMX USB Pro serial protocol only. The categories
below describe project evidence, not electrical certification.

## Tested

None. No physical adapter or DMX fixture was available during implementation,
and no hardware test listed below was executed.

An adapter will move into this category only after a reproducible physical test
record identifies the exact product and firmware and completes the relevant
manual checklist.

## Compatible by protocol

Serial interfaces whose manufacturer documentation explicitly states that they
implement the DMX USB Pro protocol, including label 6 output frames at 57,600
baud, are compatible by protocol. This is a software-protocol classification,
not a claim that any particular product has been physically tested here.

Use a stable serial path when available. Product documentation must establish
protocol compatibility; the FTDI, CP210x, or CH340 bridge identity alone does
not.

## Experimental

None in V1. Undocumented clones and adapters that only claim generic
"USB-to-DMX" or serial operation are not promoted to experimental support.
They may be tried through manual path entry only when the operator has separate
evidence that they implement DMX USB Pro framing, but results remain unverified.

## Unsupported

- **OpenDMX / FTDI bit-bang interfaces:** these depend on host-timed serial or
  bit-bang output. Home Assistant's event loop and Home Assistant OS container
  are not a dependable DMX timing source for this design.
- **uDMX / libusb interfaces:** these require native USB access and dependencies
  that V1 does not install or assume inside Home Assistant OS.
- **Generic FTDI, CP210x, or CH340 adapters:** a USB-serial bridge identifies a
  transport chip, not the DMX USB Pro application protocol.
- **Art-Net, sACN, OLA, MQTT, and add-on or external-service bridges:** these are
  outside the local serial backend implemented in V1.
- **RDM, DMX input, RGB fixture profiles, multi-slot fixtures, and multiple
  universes per interface:** these functions are outside V1 scope.

## Manual hardware validation checklist

None of these checks were executed for this release. Record the exact adapter,
firmware, Home Assistant version, architecture, fixtures, cabling, and results
when running them.

- [ ] Verify DMX channel 1 at values 0, 1, 127, and 255.
- [ ] Verify DMX channel 512 at values 0, 1, 127, and 255.
- [ ] Change four channels simultaneously and confirm that all four remain
      correct.
- [ ] Restart Home Assistant and verify the selected startup behavior.
- [ ] Reload the integration and confirm entity availability and output.
- [ ] Unplug the USB interface during operation, verify unavailable entities,
      reconnect it, and confirm recovery plus current-frame replay.
- [ ] Move a light brightness slider rapidly and verify stable final output
      without stale transitions.
- [ ] Run changing and steady output for one hour and monitor availability,
      logs, CPU use, and final channel values.
- [ ] Perform a clean integration unload and a clean Home Assistant shutdown;
      verify normal close behavior and, separately, the optional blackout.

## Electrical safety

DMX data is low voltage, but connected dimmer packs and fixtures may switch
hazardous 230 V mains power. A qualified person must install and service mains
equipment under local electrical rules. Isolate and verify mains power before
wiring or opening equipment.

Prefer a correctly certified, galvanically isolated DMX interface and proper
DMX cabling and termination. Galvanic isolation reduces fault and ground-loop
risk but does not replace safe mains installation. This project has not tested
or certified any adapter, fixture, dimmer, cable, or isolation barrier.
