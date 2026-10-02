# The Lenovo ThinkPad Yoga 260 (20FE) on Linux

Everything below was read off one machine rather than assumed, and the numbers
are here so somebody else with the same hardware does not have to rediscover
them. Where a reading disagrees with another reading, both are given and the
disagreement is explained.

## Identity

| | |
|---|---|
| `sys_vendor` | `LENOVO` |
| `product_name` | `20FES04T1M` |
| `product_version` | `ThinkPad Yoga 260` |
| `board_name` | `20FES04T1M` |
| `chassis_type` | `31` (convertible) |
| BIOS | `N1GETA9W` 1.88 |
| DMI modalias | `dmi:bvnLENOVO:bvrN1GETA9W(1.88):...:pvrThinkPadYoga260:...` |

`product_version` is the only field that identifies the model. `product_name`
and `board_name` are the board identifier `20FE`, which covers the whole line and
cannot separate a Yoga from anything else sharing it.

## Panel

| | |
|---|---|
| Output | `eDP-1` |
| Mode | 1366×768 @ 60.00 Hz (also 48.00) |
| EDID | InfoVision Optoelectronics (Kunshan), `0x04E5` |
| EDID physical size | 280 × 160 mm |

A true 12.5" 16:9 active area is 276.9 × 155.8 mm, so the EDID is about 1%
large. That matters only because the digitizer below is measured in millimetres
too, and it is the reason no touch calibration is needed: the digitizer is
closer to the truth than the EDID is, and the error left over is under 1%.

## Digitizer

One Wacom AES module presents **two** evdev nodes, which Hyprland exposes as a
`Tablets` entry and a `Touch` entry.

| node | Hyprland | name | absolute axes, measured |
|---|---|---|---|
| `0003:056A:5091.0002` | Tablets | `wacom-pen-and-multitouch-sensor-pen` | `ABS_X`, `ABS_Y`, `ABS_MT_SLOT`, `ABS_PROXIMITY` |
| `0003:056A:5091.0001` | Touch | `wacom-pen-and-multitouch-sensor-finger` | `ABS_X`, `ABS_Y`, `ABS_MISC`, `ABS_RY3`, `ABS_RX4`, `ABS_WIDTH` |

These are the real capability masks, read from
`/sys/class/input/eventN/device/capabilities/abs`, not an inference from the
USB descriptors. An earlier version of this file claimed pressure and tilt on
the pen and MT position axes on the finger. **Both claims were false**, and
they matter: see `docs/digitizer.md` for what the absence of `ABS_MT_POSITION_*`
and of any pressure axis actually rules out.

Size as Hyprland reports it: **275.59 × 154.94 mm**. The pen node carries
`ID_INPUT_TABLET=1` and `ID_INPUT_WIDTH_MM=275`; the finger node carries
`ID_INPUT_TOUCHSCREEN=1`.

Two things follow, and one of them is a bug waiting to happen:

1. **The names differ by one word, and the finger node comes first.** Any search
   for a pen by name that does not restrict itself to the tablet node returns
   the finger, so the pen silently never gets its transform. This plugin looks
   each up only among the kind it belongs to.
2. **The finger node has no `ABS_MT_TOUCH_MAJOR`**, only slot, position and
   tracking id. libinput decides whether a touch is a palm by comparing a
   contact's size against the pen's, so with no size there is nothing to
   compare: **palm rejection is not available on this machine**, and no amount
   of configuration will produce it. The kernel's own fuzz values for
   `ABS_MT_POSITION_X/Y` are 4 and are handed to libinput as `LIBINPUT_FUZZ_35`
   and `LIBINPUT_FUZZ_36`, which is normal and not a problem.

### libwacom has no entry for 5091

libwacom 2.19.1 ships `wacom-isdv4-5090.tablet`, whose own comment reads:

> this is for the Wacom pen + touchscreen as found in some versions of the
> Lenovo Yoga 260

It matches `usb|056a|5090`. **This machine is `056a:5091`**, which nothing
matches, so the pen falls back to generic handling: a name taken from the USB
descriptor rather than a real one, and a size derived from the digitizer's axes
rather than the panel's. `omarchy-yoga260-fold libwacom install` writes the
missing twin to `~/.config/libwacom/`, which libwacom reads *before* the system
directory, so it needs no root and is a one-line undo.

The `@isdv4-aes;` stylus layout that the 5090 entry references has no `.svg` in
Arch's libwacom 2.19.1 data directory either, so referencing it locally is as
harmless as it is in the packaged file.

## Sensors

All from the Intel ISHTP sensor hub on `pci0000:00/0000:00:13.0`, HID
`001F:8086:0001`, driver `hid-ishtp` + `hid_sensor_*`.

| IIO node | name | what |
|---|---|---|
| `iio:device0` | `accel_3d` | accelerometer, **in the base** |
| `iio:device1` | `gyro_3d` | gyroscope |
| `iio:device2` | `magn_3d` | magnetometer, with tilt compensation |
| `iio:device3` | `gyro_3d` | second gyroscope |
| `iio:device4` | `als` | ambient light |
| `iio:device5` | `hinge` | **hinge angle**, `hid_sensor_custom_intel_hinge` |

Nodes are found by their `name` file, never by number: probe order is not stable
across boots.

### The accelerometer has no mount matrix

`in_accel_mount_matrix` does not exist on this device, and there is no hwdb
entry for it either. `60-sensor.rules` *does* build a lookup key for it —
`udevadm test` on this machine produces exactly:

```
sensor:modalias:platform:HID-SENSOR-200073:dmi:bvnLENOVO:bvrN1GETA9W_1.88_:...:pvrThinkPadYoga260:...
```

— and `60-sensor.hwdb` has 158 `ACCEL_MOUNT_MATRIX` entries, none of them for
any Lenovo Yoga 260. So userspace gets raw chip axes with no relationship to the
screen.

Measured on a desk, machine flat, keyboard up: `(0, -9.22, -1.29)` m/s², with
`in_accel_scale = 0.000009806`. World up is out of the keyboard deck, which puts
the base's Z along the sensor's −Y and the base's Y along the sensor's −Z. The
third axis follows from handedness: with the first two fixed, only
`base_X = -sensor_X` makes the frame right-handed, so the matrix has determinant
exactly +1.

```
ACCEL_MOUNT_MATRIX = -1, 0, 0; 0, 0, -1; 0, -1, 0
```

A single flat reading cannot confirm the sign of that third axis on its own,
which is what `calibrate` is for. The daemon refuses to rotate if the matrix
stops being a proper rotation, because a reflection inverts every turn and looks
plausible until it is too late.

### The hinge sensor

`in_angl_scale = 0.017453293`, which is one degree in radians, so a channel is
one count per degree. The kernel reports radians whatever the unit, so this
plugin converts rather than assuming.

With the machine flat on a desk and the lid open:

| channel | reading |
|---|---|
| `in_angl0` (hinge) | 104° |
| `in_angl1` (screen) | 103° |
| `in_angl2` (keyboard) | 359° |

`wrap360(angl1 - angl2) = wrap360(103 - 359) = 104°`, which matches `angl0`
**exactly**. That equality is the plugin's free consistency check: the driver's
own fold channel and the difference of its two tilt channels are computed
separately, so when they agree the reading is trustworthy. They have agreed at
0.0° on every reading taken.

`in_angl1` is measured from vertical (90° = upright) and `in_angl2` from flat
(0° = flat), so the two channels do not share a reference and their difference
is the only thing meaningful between them — which is exactly what the fold angle
is.

**The tilt channels are not a second opinion on the accelerometer.** Measured
across two very different poses:

| pose | `angl0` | `angl1` | `angl2` |
|---|---|---|---|
| open on a desk | 104 | 103 | 359 |
| folded to a tent | 132 | 132 | 359 |

`angl1` tracks `angl0` and `angl2` reads ~0 whatever the base is doing. So an
earlier version of this plugin, which treated the two as independent and refused
to rotate when they disagreed, was comparing the accelerometer against a channel
that never moves: the difference grew with base tilt (12.5° flat, 40.8° with the
base propped at 42°) and would eventually have stopped the screen for good. The
figure is reported now and never acted on. The only real gates are the machine
fingerprint, the mount-matrix determinant, the hinge's own arithmetic and
stillness; the book-mode lockout belongs to the `rotate` command alone, because
the screen follows the machine in book mode too.

### A disagreement worth knowing about

The accelerometer says the base is tilted **13.5°** from flat on that desk; the
hinge hub's `angl2` says **1°**. The 13.5° carries straight through into the
screen's tilt (89.4° against 103°), so it is one discrepancy, not two.

It is almost certainly the hinge's base reference rather than a fault: gravity
is gravity, and a 13.5° tilt of a laptop on a desk is not a thing. It does not
corrupt anything, because the only thing the plugin *needs* from the hinge
sensor is the fold angle, and that channel is self-consistent to 0.0°. It
inflates the residual figure the panel shows. Past 25° that figure also becomes
a note in `status` and nothing more: two pieces of hardware describing
different machines is worth saying out loud, but `angl2` is static here, so the
number rises with base tilt and says nothing about whether the screen should
turn.

If `calibrate` is run on a known-flat surface and the accelerometer still
disagrees by more than about 12°, the mount matrix wants correcting and
`calibrate --write` takes the whole matrix back — nine numbers, one call.

## Fold angles measured on this machine

| pose | `in_angl0` |
|---|---|
| lid open on a desk | 104° |
| lid open, leaning further back | 132° |

The thresholds the plugin acts on are the official Lenovo Yoga 260 ranges, not
anything derived from those two readings: book below 190°, tablet 190°–270°,
tent 270°–340°, stand 340°–360° and treated as tablet. What a fully flat fold
reads is the one number still to be measured.

The 190 crossing carries a release threshold below it, `bookReleaseDeg`
(default 170), because the EC does not switch at 190 and release at 190 — it
engages partway through the band with hysteresis of its own. Dell's convertible
documentation puts the default keyboard cut-out near 225 for the same reason.
So a lid resting at 189/191 keeps whatever mode it already had instead of taking
the keyboard off and on. The 270 and 340 boundaries gate no input, so they stay
exact. See `docs/windows.md` for why this, and for the rest of what Windows did
that this plugin does not and cannot.

## No tablet-mode switch

```
B: SW=1        # Lid Switch, and nothing else
```

That single bit is `SW_LID`. There is no `SW_TABLET_MODE` anywhere, and no ACPI
notification for the fold. The lid switch is exposed to Hyprland as a Switch
Device, and the ThinkPad extra-buttons cluster as another.

This is the whole reason the general-purpose plugins do not work here, and the
reason this one is written against one model.

## Other things this machine has

- `charge_control_end_threshold` and `charge_control_start_threshold` are both
  writable, so battery conservation works: `charge_control_end_threshold` is
  currently 100.
- The Elantech touchpad is a real touchpad to Hyprland, not a pointer —
  `ID_INPUT_TOUCHPAD=1`, `ID_INPUT_TOUCHPAD_INTEGRATION=internal`, and every
  touchpad-only device key is accepted for it — even though `hyprctl devices`
  lists it under `mice`. So `input.touchpad` settings do apply. There is no
  `touchpads` category in that output, and no need for one.
- The tablet-mode lock switches off exactly **two** devices:
  `at-translated-set-2-keyboard` and `thinkpad-extra-buttons`. That is the
  `keyboardNames` list, and it is the whole list — `set_devices` iterates it and
  nothing else. The separate Hyprland Keyboard devices for the **screen, power
  button and sleep button** are never touched, folded or not. Whether that is
  deliberate or an omission is not something the code records.
- `hl.device` accepts `sensitivity`, `enabled`, `transform` and `output`. It does
  **not** accept `active`; the bar's highlight is presentation, not device state.
- `hyprctl keyword` is refused by the Lua config (`keyword can't work with
  non-legacy parsers. Use eval.`), so everything here goes through
  `hyprctl eval`.
- Hyprland 0.56.2 does apply `transform` to the **pen**: `setTabletConfigs()`
  calls `libinput_device_config_calibration_set_matrix`, and libinput 1.31 lists
  calibration under both "Tablets" **and** "Touchscreens". Older reports that
  stylus transforms are unsupported are out of date. **This was contradicted by
  an earlier version of [`digitizer.md`](digitizer.md)**, on the strength of
  `hyprctl -j devices` reporting `transform = None` for the pen. That field is
  never emitted for tablets — it is a fixed two-key object at
  `src/debug/HyprCtl.cpp:822-829` — so the reading was of an absent key and
  proved nothing. The transform is applied; whether the *finger* rotates depends
  on a libinput capability the compositor does not report back, and step 2 of
  [`calibration.md`](calibration.md) is the procedure for that.

## Environment

| | |
|---|---|
| Kernel | 7.2.5-3-omarchy |
| Hyprland | 0.56.2 |
| libinput | 1.31.3 |
| libwacom | 2.19.1 |
| Omarchy | 4.0.x, Lua Hyprland config |
| Python | 3.14 |
