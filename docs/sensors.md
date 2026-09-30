# The sensors, and what they actually are

Everything here is either read off this machine or taken from the driver
source. Where the two disagree, the driver source is what Linux does and the
reading is what the hardware did.

## Where they come from

All of them hang off one Intel ISHTP sensor hub on `pci0000:00/0000:00:13.0`,
HID `001F:8086:0001`, driven by `hid-ishtp` + `hid_sensor_*` + `intel_ishtp_hid`:

| IIO node | `name` | driver | what it is |
|---|---|---|---|
| `iio:device0` | `accel_3d` | `hid_sensor_accel_3d` | 3D accelerometer, **in the base** |
| `iio:device1` | `gyro_3d` | `hid_sensor_gyro_3d` | gyroscope |
| `iio:device2` | `magn_3d` | `hid_sensor_magn_3d` | magnetometer, with tilt compensation |
| `iio:device3` | `gyro_3d` | `hid_sensor_gyro_3d` | second gyroscope |
| `iio:device4` | `als` | `hid_sensor_als` | ambient light |
| `iio:device5` | `hinge` | `hid_sensor_custom_intel_hinge` | hinge angle |

Nodes are found by their `name` file, never by number: probe order is not stable
across boots, and `iio:device3` being a second gyroscope is exactly the kind of
thing that shifts.

## Everything reports at 10 Hz

```
iio:device0  in_accel_sampling_frequency   10.000000   in_accel_hysteresis  0.000001
iio:device1  in_anglvel_sampling_frequency 10.000000   in_anglvel_hysteresis 0.000010
iio:device5  in_angl_sampling_frequency    10.000000   in_angl_hysteresis     1.000000
```

This one number explains most of what went wrong before.

**Polling faster than the hardware refreshes returns the same report over and
over.** The recorder originally ran at 20 Hz against a 10 Hz sensor.

**The three axis attributes are three separate files, and reading them one
after another can straddle two reports.** The result describes no instant at
all. In a capture of a machine sitting still, torn reads came out anywhere from
**0.72 g to 2.19 g** while every settled sample sat within a few hundredths of
**0.96 g**.

So the plugin reads the axes, measures the magnitude, and **throws the sample
away unless it is near one g**. Gravity is 1 g whatever the machine is doing,
which makes magnitude the right test for whether a sample is coherent — and
explicitly the *wrong* test for whether the machine is still. A rotating
accelerometer still reads 1 g. Coherence and stillness are two different
questions and need two different sensors.

`Coherent` retries three times and keeps a rejection count, because a tool that
silently discards bad input cannot be told apart from one that is not working.
The rate is in `status` and `doctor` as `accelRejectionRate`.

The loop runs at the accelerometer's own declared rate and the pose is averaged
over `poseWindowSec` (0.6 s, about six reports), so a pose rests on several
samples rather than on whichever tenth of a second the loop landed in.

## Stillness comes from the gyroscope

`in_anglvel_{x,y,z}_raw`, `in_anglvel_scale = 1.74e-7` rad/s per count.

At rest it reads about **0.3 °/s**, which is the sensor's own quantisation step
at its configured rate — resting readings of 0 to 42679 counts span 0 to
0.43 °/s. A hand turning the machine at 37 °/s is three million counts, two
orders of magnitude away. The gate is at 9 °/s, which sits in the gap with room
on both sides.

This gate is what stops the screen reacting while the machine is being carried.
An earlier version tested acceleration magnitude instead and **passed 98% of the
samples in a recording of the machine being carried through every position it
has** — because you cannot rotate your way past 1 g.

The threshold is read from `stillRotDeg` and is visible in the panel and in
`debug`.

## The hinge sensor: three numbers the firmware made

Driver: `drivers/iio/position/hid-sensor-custom-intel-hinge.c`, *"HID Sensor
INTEL Hinge"*, Intel 2020, bound to platform device **`HID-SENSOR-INT-020b`**.

It is an IIO driver, not a HID one, and it **computes nothing**. Its three
channels are the HID custom values 1, 2 and 3, labelled in that order `"hinge"`,
`"screen"` and `"keyboard"`. The whole of the report handler is:

```c
case HID_USAGE_SENSOR_DATA_FIELD_CUSTOM_VALUE(1):
case HID_USAGE_SENSOR_DATA_FIELD_CUSTOM_VALUE(2):
case HID_USAGE_SENSOR_DATA_FIELD_CUSTOM_VALUE(3):
        offset = usage_id - HID_USAGE_SENSOR_DATA_FIELD_CUSTOM_VALUE(1);
        st->scan.hinge_val[offset] = *(u32 *)raw_data;
```

A 32-bit value lifted out of the HID input report, verbatim. The angles are
worked out by the **embedded controller**; Linux only rescales them
(`hid_sensor_format_scale(HID_USAGE_SENSOR_HINGE, ...)`). Whatever the
firmware's "keyboard" angle means, it is not Linux's interpretation of it.

Measured across two very different poses:

| pose | `angl0` hinge | `angl1` screen | `angl2` keyboard |
|---|---|---|---|
| open on a desk | 104 | 103 | 359 |
| folded to a tent | 132 | 132 | 359 |

`angl1` tracks `angl0`, and `angl2` reads about zero whatever the base is doing.

**Consequences, and they matter:**

- `angl0` is the fold angle, and it is the only channel this plugin acts on.
- `angl1` and `angl2` are reported and never trusted. An earlier version used
  them as an independent check on the accelerometer, which meant comparing the
  accelerometer against a channel that never moves — a figure that grows with
  base tilt (12.5° flat, 40.8° with the base propped at 42°) until it blocks
  the screen outright.
- Their internal consistency, `angl0` against `wrap360(angl1 - angl2)`, is still
  checked, because it is free. It is a check on the **firmware's arithmetic**,
  not on the machine's attitude, and the tolerance is deliberately loose at
  150°: the three channels have been seen 131° apart mid-fold, and a real fold
  must not be stopped by that.
- All three update at 10 Hz, so **a fold that takes less than a tenth of a second
  can be stepped over entirely.**

## The accelerometer has no mount matrix

`in_accel_mount_matrix` does not exist on this device, and there is no hwdb
entry for it. `60-sensor.rules` *does* build a lookup key for it — `udevadm
test` produces exactly:

```
sensor:modalias:platform:HID-SENSOR-200073:dmi:bvnLENOVO:bvrN1GETA9W_1.88_:...:pvrThinkPadYoga260:...
```

— and `60-sensor.hwdb` has 158 `ACCEL_MOUNT_MATRIX` entries, none of them for
any Lenovo Yoga 260. Userspace gets raw chip axes with no relationship to the
screen.

Measured on a desk, machine flat, keyboard up: `(0, -9.22, -1.29)` m/s², with
`in_accel_scale = 0.000009806`. World up is out of the keyboard deck, which
puts the base's Z along the sensor's −Y and the base's Y along the sensor's −Z.
The third axis follows from handedness: with the first two fixed, only
`base_X = -sensor_X` gives a right-handed frame, so the matrix has determinant
exactly +1.

```
ACCEL_MOUNT_MATRIX = -1, 0, 0; 0, 0, -1; 0, -1, 0
```

The daemon **refuses to rotate if the matrix stops being a proper rotation**,
because a reflection inverts every turn and is otherwise indistinguishable from
a correct answer until the screen is upside down.

The resting magnitude is 0.96 g rather than 1.000 g — a 4% scale offset in the
sensor, which the normalisation cancels and the coherence band is wide enough to
absorb.

## How the screen's attitude is rebuilt

The accelerometer is in the **base**; the screen is in the lid. In book mode
those are near enough the same, and folded they are 180° apart, which is exactly
when rotation matters. So the base's gravity vector is turned through the
measured fold angle:

```
t = -cos(θ)·base_Y + sin(θ)·base_Z     # towards the top edge of the screen
n = -sin(θ)·base_Y - cos(θ)·base_Z     # out of the screen face
```

The hinge runs along the base's X axis, so the screen's X carries across
unchanged. Full derivation and the endpoint checks are in the `lid_vector`
docstring, and `test_an_open_laptop_reads_as_normal` is the one test worth not
skipping: a sign error there does not look wrong, it just quietly calls an open
laptop upside down.

## Digitizer

The full machine enumeration, including the two gyroscopes and the two sensor hubs, is in `docs/inventory.md`.

One Wacom AES module, USB `056a:5091`, presenting two evdev nodes that Hyprland
reports as a `Tablets` entry and a `Touch` entry:

| node | Hyprland | absolute axes, measured |
|---|---|---|
| `0003:056A:5091.0002` | Tablets | `ABS_X`, `ABS_Y`, `ABS_MT_SLOT`, `ABS_PROXIMITY` |
| `0003:056A:5091.0001` | Touch | `ABS_X`, `ABS_Y`, `ABS_MISC`, `ABS_RY3`, `ABS_RX4`, `ABS_WIDTH` |

Neither node has `ABS_MT_POSITION_X/Y`, neither has a pressure axis, and the
finger has no `ABS_MT_TRACKING_ID`. See `docs/digitizer.md`.

**libwacom 2.19.1 has no entry for 5091.** It ships `wacom-isdv4-5090.tablet`,
whose own comment reads *"this is for the Wacom pen + touchscreen as found in
some versions of the Lenovo Yoga 260"* — that is the 5090 half of the same
hardware pair, and this machine is the 5091 half. `libwacom install` writes the
missing twin to `~/.config/libwacom/`, which libwacom reads before the system
directory. It needs no root. **It takes effect when the digitizer is next
opened**, so after a logout; until then Hyprland still reports the raw USB
descriptor name and the size derived from the digitizer's own axes.

The finger node has **no `ABS_MT_TOUCH_MAJOR`**, only slot, position and
tracking id. libinput decides palm-versus-pen by comparing a contact's size
against the pen's, so with no size there is nothing to compare and **palm
rejection is not available on this machine**. No configuration produces it.

## Sources

- `drivers/iio/position/hid-sensor-custom-intel-hinge.c` (Linux, Intel 2020)
- `drivers/hid/hid-sensor-hub.c`, `drivers/hid/hid-sensor-custom.c`
- `Documentation/hid/` — the HID sensor hub and its report format
- `/usr/lib/udev/hwdb.d/60-sensor.hwdb` and `60-sensor.rules` — the mount-matrix
  keys, and the 158 entries that do not include this machine
- `wayland.freedesktop.org/libinput` — calibration is listed under **Tablets**,
  which is why the pen follows the screen on Hyprland 0.56.2
- The `Hw` section of [`../README.md`](../README.md) for what all this means in
  practice
