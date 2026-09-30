# The digitizer: Yoga 260 input hardware, measured

Everything here was read off this machine, not taken from the USB descriptors or
from what the hardware ought to do. Where a claim in this file contradicts
something the tooling or an older version of the docs said, this file is right
and that thing was wrong.

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

## Why the picture rotates and the input does not

The plugin rotates the panel and sets a transform on both input nodes. The panel
rotation works and is verifiable: at transform 3 the framebuffer is genuinely
768 × 1366, confirmed with `grim`.

The input side is inert, and it is not a plugin bug:

1. `hyprctl eval 'hl.device({ name = "wacom-pen-and-multitouch-sensor-pen", transform = 3 })'`
   returns `ok`.
2. `hyprctl -j devices` then still reports `transform = None` for that node.
3. That `ok` is not a silent-parse artefact. The same call with `accel` is
   rejected outright — `hl.device: unknown field 'accel'` — so Hyprland really
   is parsing `transform` and really is discarding it for these devices.

On Wayland the only mechanism that can rotate a pointer is a libinput
**calibration matrix**, and libinput applies it to device classes that have a
declared axis range and multi-touch position axes. This digitizer has neither.
A single-point device reporting `0..0` has no coordinate space for a matrix to
act within, so the transform is accepted and dropped.

Hyprland normalises the digitizer across whichever output it is bound to, so
the *stretching* to the panel follows the panel automatically. The *rotation* is
the missing half, and nothing above the evdev layer provides it here.

**Consequence: with the panel in portrait, both the pen and the finger report
positions in the panel's native landscape frame, and both are 90° out.** The
picture is correct; the glass is answering about a different orientation. The
plugin's `output` binding is what keeps the digitizer on the laptop's own panel
when a monitor is attached, and that part works.

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
*mount matrix*, which is a different property (`INPUT_PROP_ACCELEROMETER` and a
mount matrix) and has nothing to do with input rotation. It is kept because it
makes the kernel agree with the matrix this plugin already carries; it does not
fix this.

## Measuring the digitizer properly

`/tmp/opencode/capture.py` records both evdev nodes alongside the IIO motion
sensors and the current panel transform on one clock, without grabbing the
devices, so the pen and finger keep working while it runs. It needs root, since
the nodes are otherwise unreadable.

```sh
sudo python3 /tmp/opencode/capture.py --seconds 120 --out /tmp/opencode/digitizer.jsonl
```

The useful routine is: tap each screen corner in landscape, rotate to portrait,
tap each corner again, then draw with the pen. Comparing the raw `ABS_X`/`ABS_Y`
values for the same physical spot before and after the rotation shows directly
that the digitizer's numbers do not change with the panel — which is the
diagnosis above, established by measurement rather than by inference — and the
observed minima and maxima give the native range a calibration matrix would need.
