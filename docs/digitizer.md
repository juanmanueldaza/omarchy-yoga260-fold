# The digitizer: Yoga 260 input hardware, measured

Everything here was read off this machine, not taken from the USB descriptors or
from what the hardware ought to do. Where a claim in this file contradicts
something the tooling or an older version of the docs said, this file is right
and that thing was wrong. The rotation question below was the one place this
file was the wrong one, and it says so where it happens.

The companion files are `sensors.md` for the IIO motion sensors, `hardware.md`
for the whole machine, and `calibration.md` for the guided mount-matrix check.

## One module, two nodes

A single Wacom AES module, USB `056a:5091`, on `usb-0000:00:14.0-10`, exposing
two evdev nodes that libinput classifies differently:

| node | Hyprland section | libinput class | udev |
|---|---|---|---|
| `/dev/input/event5` | `touch` | single-point touchscreen | `ID_INPUT_TOUCHSCREEN=1` |
| `/dev/input/event6` | `tablets` | tablet tool | `ID_INPUT_TABLET=1` |

Both carry `LIBINPUT_DEVICE_GROUP=3/56a/5091:usb-0000:00:14.0-10` and
`ID_INPUT_WIDTH_MM=275`, `ID_INPUT_HEIGHT_MM=154` (Hyprland reports
275.59 × 154.94 mm). One physical digitizer, one coordinate space, two very
different device classes — and the difference is the whole reason input
rotation behaves the way it does.

## The absolute axes, as the kernel reports them

From `/sys/class/input/eventN/device/capabilities/abs`:

```
event5 (finger)  260800000000003   ABS_X ABS_Y ABS_MISC ABS_RY3 ABS_RX4 ABS_WIDTH
event6 (pen)     10001000003       ABS_X ABS_Y ABS_MT_SLOT ABS_PROXIMITY
```

What is **absent**, and this is the important part:

- **No `ABS_MT_POSITION_X` or `ABS_MT_POSITION_Y` on either node.** Position
  arrives as plain `ABS_X` / `ABS_Y` — single-point, no per-contact axes. There
  is no multi-touch to speak of.
- **No pressure axis on either node.** No `ABS_PRESSURE`, no `ABS_MT_PRESSURE`.
- **No tilt axis on either node.** No `ABS_TILT_X` / `ABS_TILT_Y`.
- **No `ABS_MT_TRACKING_ID` on the finger**, and no `ABS_MT_TOUCH_MAJOR`.
- The finger's `ABS_MISC`, `ABS_RY3`, `ABS_RX4`, `ABS_WIDTH` are not position
  axes and nothing in this plugin reads them.

An earlier version of `hardware.md` and `sensors.md` claimed the pen had
"pressure, tilt" and the finger had "MT slot/position/tracking id". Both were
invented from the USB descriptors. They are corrected, and the corrections are
not cosmetic — see the consequences below.

## What that rules out, concretely

- **Palm rejection is impossible.** With no `ABS_MT_TOUCH_MAJOR` there is no
  contact-size signal to reject a palm against, and no tracking id to correlate
  contacts.
- **No pressure or tilt sensitivity.** The pen is a position-only pointer here.
  Nothing that depends on pressure — line width, eraser-by-reversal, hover
  height from tilt — is available.
- **Rotation of the input is not supported by either device class.** See below.

## The axis range is degenerate

`EVIOCGABS` on every code returns `minimum == maximum == 0` for both nodes. The
evdev layer has no usable declared range for the position axes, so libinput has
no interval to normalise against and no interval within which a calibration
matrix could keep transformed coordinates in bounds.

Reading the nodes at all requires root:

```sh
sudo python3 - <<'PY'
import fcntl, struct, os
for ev in ("/dev/input/event5", "/dev/input/event6"):
    fd = os.open(ev, os.O_RDONLY)
    for axis, label in ((0, "ABS_X"), (1, "ABS_Y")):
        b = bytearray(24)
        fcntl.ioctl(fd, 0x80184520 + axis, bytes(b))
        val, mn, mx = struct.unpack("iii", bytes(b)[:12])
        print(ev, label, "min", mn, "max", mx)
    os.close(fd)
PY
```

## The picture rotates, and the input follows — settled

**This was recorded here as an unresolved contradiction between this file and
`hardware.md`/`sensors.md`. It is resolved, and the evidence was an artefact.**

The plugin rotates the panel and sets a transform on both input nodes. The panel
rotation works and is verifiable: at transform 3 the framebuffer is genuinely
768 × 1366, confirmed with `grim`.

### The measurement that settled it, and why it was worthless

This file previously argued, from three observations, that Hyprland accepts the
transform and drops it. Observations 1 and 3 were sound:

1. `hyprctl eval 'hl.device({ name = "wacom-pen-and-multitouch-sensor-pen",
   transform = 3 })'` returns `ok`.
2. `hyprctl -j devices` then still reports `transform = None`.
3. The same call with a bogus field is rejected outright —
   `hl.device: unknown field 'accel'` — so Hyprland *is* parsing `transform`.

**Observation 2 is meaningless.** Hyprland never emits a `transform` key for a
tablet or a touch device at all. In v0.56.2 the serialiser writes a fixed
two-key object and nothing else:

```cpp
// src/debug/HyprCtl.cpp:822-829
for (auto const& d : g_pInputManager->m_tablets) {
    result += std::format(
        R"#(    {{
    "address": "0x{:x}",
    "name": "{}"
}},)#",
        rc<uintptr_t>(d.get()), escapeJSONStrings(d->m_hlName));
}
```

The `touch` branch at `:845-852` is identical. `grep transform` over that file
finds the word only in `monitorsRequest`. So `transform = None` here was a
Python `.get()` on a key that does not exist, reported as `None` — the same as a
genuinely unset field, and carrying no information at all. Confirmed live: with
a transform set on the pen, `hyprctl -j devices` returns
`{"address": ..., "name": ...}` and nothing more.

### What actually happens, from the source

The transform does reach libinput, for both nodes:

- `hl.device` stores the field as config keyed by device name —
  `src/config/lua/bindings/LuaBindingsConfigRules.cpp:1093`
- which trips `REFRESH_INPUT_DEVICES` —
  `src/config/supplementary/propRefresher/PropRefresher.cpp:63`
- which re-runs `setTabletConfigs()` for the pen —
  `src/managers/input/InputManager.cpp:2027-2030`:

```cpp
const int ROTATION = std::clamp(Config::mgr()->getDeviceInt(NAME, "transform", "input:tablet:transform"), -1, 7);
Log::logger->log(Log::DEBUG, "Setting calibration matrix for device {}", NAME);
if (ROTATION > -1)
    libinput_device_config_calibration_set_matrix(LIBINPUTDEV, MATRICES[ROTATION]);
```

and `setTouchDeviceConfigs()` for the finger, `:1979-1984`, with the same call
behind a `libinput_device_config_calibration_has_matrix()` capability check.
Both `input:tablet:transform` and `input:touchdevice:transform` exist in
v0.56.2, `Int`, default `0`, min 0 max 6 (`src/config/values/ConfigValues.cpp:345,362`).

`MATRICES` is an eight-entry rotation table (`src/managers/input/InputManager.hpp:67-81`),
so the mapping is a genuine quarter-turn matrix, not a no-op.

**The earlier claim that libinput applies calibration only where there are
multi-touch position axes and a declared range was wrong.** libinput 1.31.3's own
header lists `libinput_device_config_calibration_set_matrix()` under *both*
`Touchscreens` and `Tablets`:

```
- Touchscreens:
   - libinput_device_config_calibration_set_matrix()
- Tablets:
   - libinput_device_config_calibration_set_matrix()
```

### What is still genuinely unknown

One bit, and it is only about the finger: whether
`libinput_device_config_calibration_has_matrix()` returns true for *this*
Wacom touchscreen. The pen's path is unconditional. The compositor exposes no way
to find out — the field it would report is never emitted, and the DEBUG line
that would reveal it is not reachable because `debug:disable_logs` is `true`.

So: **the pen transform is applied.** Whether the *finger* rotates is the only
thing left, and step 2 of [`calibration.md`](calibration.md) — tap each corner,
turn, tap again — is the procedure that answers it. It is no longer a question
about the pen.

What is **not** in dispute is the half that Hyprland gives for free: it
normalises the digitizer across whichever output it is bound to, so the
*stretching* to the panel follows the panel automatically. It is the *rotation*
half that step 2 of [`calibration.md`](calibration.md) is for, and after the
above it is a question about the finger only. That procedure is how it gets
settled: fold the machine flat, tap each screen corner, turn it and tap again,
and see whether the input follows. Do the same with the pen to confirm the half
that is now settled. If you follow it on the old reading in `hardware.md`, it
will tell you your pen is broken when it is not.

## The route that would work, and why it is not wired up

A libinput calibration matrix can be applied outside the compositor, by udev,
with the `LIBINPUT_CALIBRATION` hwdb property. It is a 9-element affine matrix
on the raw absolute axes. That is the only mechanism left.

It has not been done here, for two reasons that are about correctness rather
than effort:

- **The matrix has to be in raw axis units, and the declared range is 0..0.** The
  real native values have to be *observed* while the pen and finger are used,
  not assumed. Assuming 1366 × 768 and being wrong produces a digitizer that is
  worse than one that does not rotate, and it cannot be undone from inside a
  session.
- **It needs root and it re-opens the device.** `systemd-hwdb update` plus
  `udevadm trigger` on the two nodes drops input briefly and needs Hyprland to
  re-acquire them.

`hwdb show` and `hwdb apply` in this plugin write a udev entry for the IIO
*mount matrix*, which is a different property (`ACCEL_MOUNT_MATRIX` and
`ACCEL_LOCATION=base`, under a `sensor:modalias:platform:HID-SENSOR-200073` key)
and has nothing to do with input rotation. It is kept because it makes the kernel
agree with the matrix this plugin already carries; it does not fix this.

## Measuring the digitizer properly

[`tools/capture-digitizer.py`](../tools/capture-digitizer.py) records both evdev
nodes alongside the IIO motion sensors and the current panel transform on one
clock. It finds the nodes by USB id and by udev property rather than by event
number, uses nothing outside the standard library, and **does not grab the
devices** — no `EVIOCGRAB`, no exclusive open — so the pen and the finger keep
working while it runs, which is the only way to collect this data at all. It
needs root, because the nodes are `crw------- root root`.

```sh
sudo ./tools/capture-digitizer.py --seconds 120 --out /tmp/digitizer.jsonl
```

The useful routine is: tap each screen corner in landscape, rotate to portrait,
tap each corner again, then draw with the pen. Each line is one sample carrying
the two nodes' current `ABS_X`/`ABS_Y`, the accelerometer, the gyroscope, the
hinge's three channels and the transform the panel is being given, so every tap
has the posture and the orientation it happened in attached to it.

What it settles is the thing the two readings above disagree about: whether the
raw numbers for the same physical spot change when the panel turns. Comparing
the same corner before and after the turn either shows the axes following the
panel — the `hardware.md` reading — or shows them unchanged and answering about
the machine's native landscape frame. The observed minima and maxima also give
the native range that a calibration matrix would need, which is the input
[`hwdb`'s `LIBINPUT_CALIBRATION` route](#the-route-that-would-work-and-why-it-is-not-wired-up)
is still missing.
