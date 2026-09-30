# Full hardware inventory: Yoga 260 (20FE)

Everything on this machine, enumerated from the running kernel, not from the
vendor's spec sheet. Where something contradicts what the docs or the tooling
assume, this file records what is actually there.

Read alongside `sensors.md` (the motion sensors in depth), `digitizer.md` (the
input side in depth), `hardware.md` (identity, panel, power topology) and
`calibration.md` (the mount-matrix check).

Machine: `LENOVO` / `20FES04T1M` / `ThinkPad Yoga 260`, BIOS `N1GETA9W`,
chassis type 31 (convertible).

## The sensor hub is split in two, and that is the key fact

All six IIO sensors hang off the Intel Sunrise Point-LP Integrated Sensor Hub
at `0000:00:13.0`, which exposes **two** HID hubs. They are not arbitrary
numbering — each holds the sensors physically built into one half of the
machine:

| HID hub | sensors on it | half |
|---|---|---|
| `001F:8086:0001.0003` | accelerometer, **two** gyroscopes, ambient light | **base** (keyboard deck) |
| `001F:8086:0001.0004` | magnetometer, hinge angle | **lid** (screen half) |

`001F:0000:0000.0005` is the hub itself.

The magnetometer and the hinge sensor sharing a hub is the tell: a lid carries
a magnetometer for cover/tablet detection and an angle sensor, while the base
carries the inertial sensors and the light sensor.

## Every IIO sensor, with its HID usage and collection

| node | driver name | HID usage | collection | hub |
|---|---|---|---|---|
| `iio:device0` | `accel_3d` | `HID-SENSOR-200073` | `.10` | base |
| `iio:device1` | `gyro_3d` | `HID-SENSOR-200076` | `.4` | base |
| `iio:device2` | `magn_3d` | `HID-SENSOR-200083` | `.14` | **lid** |
| `iio:device3` | `gyro_3d` | `HID-SENSOR-200076` | `.8` | base |
| `iio:device4` | `als` | `HID-SENSOR-200041` | `.3` | base |
| `iio:device5` | `hinge` | `HID-SENSOR-INT-020b` | `.19` | **lid** |

The hinge is the odd one out: it is not a standard HID sensor usage, it is an
Intel custom collection bound by `drivers/iio/position/hid-sensor-custom-intel-hinge.c`,
and it is a pure pass-through to the firmware.

### There are two gyroscopes, and it is not a problem

`iio:device1` and `iio:device3` share the name `gyro_3d` and the usage
`HID-SENSOR-200076`, differing only by collection (`.4` and `.8`). They return
different values simultaneously, so they are two distinct collections, not one
sensor enumerated twice.

Both live on the **base** hub, alongside the accelerometer. That settles the
question that mattered: the plugin's stillness gate is reading a base gyro, not
a lid one, so it is measuring the half of the machine that actually sits on the
desk. A lid gyro would have made every stillness reading meaningless.

`find_iio_device("gyro_3d")` resolves by name and returns the first match in
sorted order, which is `iio:device1` (collection `.4`). The 9 °/s gate was
calibrated against 283 samples of *that* device at rest (max 6.57 °/s), so the
threshold and the sensor in use match. The second collection is inventoried here
and otherwise unused.

### Sensors present and deliberately unused

- **`magn_3d`** (lid magnetometer) — unused. It exists for lid-state detection
  the OS already does another way.
- **`als`** (ambient light) — unused. The backlight is driven by the firmware
  and the desktop, not by this plugin.

## hwmon: the firmware sensor surface

Not used by this plugin, and not previously documented anywhere.

| hwmon | what it exposes |
|---|---|
| `thinkpad` | `temp1`–`temp8` (labelled), `fan1_input`, `power` — the Lenovo firmware sensors |
| `BAT0` | battery charge, energy, voltage, capacity |
| `acpitz` | ACPI thermal zones |
| `coretemp` | package and per-core temperatures |
| `pch_skylake` | PCH temperatures and power |
| `nvme` | drive temperature |
| `iwlwifi_1` | radio temperature |
| `AC` | AC adapter online state |
| `wacom_battery_1` | **pen battery**, reported by the digitizer itself |

`thinkpad` is the interesting one: it is a labelled eight-channel temperature
set plus a fan, exposed straight from the embedded controller, and it is the
most likely place a hinge or sensor problem would show a thermal symptom.

## Graphics, panel, and the rest

- GPU: Intel Skylake-U GT2 / HD Graphics 520, `8086:1916`
- Panel: internal `eDP-1`, 1366×768 @ 60 Hz, EDID reports 280×160 mm
- NVMe: SK hynix Gold P31, `1c5c:174a`
- Card reader: Realtek RTS522A, `10ec:522a`
- Ethernet: Intel I219-LM, `8086:156f` (`enp0s31f6`)
- WiFi: Intel 8260, `8086:24f3` (`wlp4s0`)
- Bluetooth: Intel `8087:0a2b`
- Audio: Sunrise Point-LP HD Audio, `8086:9d70`, one PCH card
- Camera: Chicony integrated camera, `04f2:b5c1`
- Fingerprint: Validity VFS7500, `138a:0090`
- Power: `AC`, `BAT0`, plus `wacom_battery_1`

None of the camera, fingerprint, audio, storage, or networking hardware is
touched by this plugin; they are recorded here so the inventory is complete.

## Input devices

| device | notes |
|---|---|
| `wacom-pen-and-multitouch-sensor-pen` | `ID_INPUT_TABLET`, see `digitizer.md` |
| `wacom-pen-and-multitouch-sensor-finger` | `ID_INPUT_TOUCHSCREEN`, see `digitizer.md` |
| `at-translated-set-2-keyboard` | the keyboard, disabled in tablet mode |
| `thinkpad-extra-buttons` | the top-row button cluster, disabled in tablet mode |
| `etps/2-elantech-trackpoint` | the red nub, disabled in tablet mode |
| `etps/2-elantech-touchpad` | disabled in tablet mode |

The digitizer is a USB HID device, `056a:5091` on `usb-0000:00:14.0-10`, and is
the *only* USB device on the bus that is not the camera, fingerprint reader, or
Bluetooth radio. It also keeps its own battery, which is why
`wacom_battery_1` exists.

## What this plugin uses, versus what is merely inventoried

| component | used for | depth of documentation |
|---|---|---|
| `accel_3d` | pose, mount matrix | full — `sensors.md` |
| `gyro_3d` `.4` | stillness gate | full — `sensors.md`, calibrated to measured noise |
| `hinge` | fold angle, mode | full — `sensors.md`, all three channels |
| digitizer, both nodes | pen and touch | full — `digitizer.md` |
| panel, machine identity | the thing being rotated | `hardware.md` |
| `gyro_3d` `.8` | **nothing** | inventoried here only |
| `magn_3d` | nothing | inventoried here only |
| `als` | nothing | inventoried here only |
| `thinkpad` hwmon | nothing | inventoried here only |
| camera, fingerprint, audio, network, storage, battery | nothing | inventoried here only |

The plugin reads three sensors out of six, and the digitizer out of the whole
input stack. Everything else is here so that the record of the machine is
complete rather than scoped to this project's dependencies.

## Reading the sensors by hand

Everything in the tables above came from these, all unprivileged except the
digitizer:

```sh
# identity
for f in sys_vendor product_name product_version board_name bios_version chassis_type; do
  echo "$f = $(cat /sys/class/dmi/id/$f)"
done

# the IIO sensors, with the HID usage that identifies them
for d in /sys/bus/iio/devices/iio:device*; do
  [ -e "$d/name" ] || continue
  echo "$(basename "$d") = $(cat "$d/name")  $(readlink -f "$d/device" | grep -oE 'HID-SENSOR-[0-9a-f]+(\.[0-9]+)?')"
done

# hwmon
for h in /sys/class/hwmon/hwmon*; do
  echo "$(cat "$h/name"): $(ls "$h" | grep -cE '^(temp|in|curr|power|fan|energy)[0-9_]') inputs"
done
```
