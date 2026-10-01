# Calibration

Two things about this machine cannot be worked out from inside the software, and
both are settled by a person at the keyboard. Everything else is checked
automatically and the daemon refuses to act when a check fails.

```bash
fold=~/.config/omarchy/plugins/estrocondoso.yoga260-fold/bin/omarchy-yoga260-fold
$fold calibrate
```

## What is checked without you

**The machine is a Yoga 260.** `product_version` has to say so.

**The matrix is a proper rotation.** Determinant +1, rows orthonormal. A
reflection inverts the sense of every turn and is otherwise indistinguishable
from a correct answer until the screen is upside down.

**The hinge sensor agrees with itself.** The driver's fold channel and the
difference of its two tilt channels are computed separately; when they part
company the reading is treated as bad and nothing turns. On this machine they
have agreed to 0.0°.

**The two sensors' disagreement is reported, not enforced.** The accelerometer
and the hinge hub estimate each half's tilt independently; the residual is
shown in the panel, and past 25° it is also written into `status` as a note —
never used to stop a rotation, because `angl2` is static on this machine, so
the figure rises with base tilt and says nothing about whether the screen
should turn. See the note at the end of
[`hardware.md`](hardware.md) about the ~13° this sits at on a desk.

**The machine is still.** Below 0.8 g of measured acceleration is movement, not
attitude, and a moving machine holds its last reading.

## What you check

### 1. The mount matrix

Lie the machine flat on a known-flat surface, lid open, and hold still. The
command averages 40 samples and prints three things:

```
base up       : (+0.000, +0.232, +0.973)
lid up        : (...)
hinge         : fold 104°  screen 103°  base 359°
base tilt     : accelerometer 13.5°   hinge sensor 1.0°
```

`base up` should be very nearly `(0, 0, 1)` for a machine lying flat. The
`base tilt` line is the real test, and it is the one that catches a wrong sign on
the base's X axis: the residual between the two independent estimates has to be
under about 12° or the command stops and says so.

If it is off, work out the correction and write it in:

```bash
$fold calibrate --write M00 M01 M02 M10 M11 M12 M20 M21 M22   # the whole matrix
```

A row is the base's axis written in terms of the sensor's, so `-1, 0, 0` means
"the base's right is the sensor's left"; the nine numbers are the three rows,
top to bottom. The command refuses a matrix whose determinant is not +1, and
writes it into `shell.json` as `mountMatrix`, replacing whatever was stored
there. The daemon reads it back with the same determinant check and falls back
to the shipped matrix if it does not hold.

### 2. The digitizer

This is the part no amount of reading code can settle, because whether a tap
lands where you touched is not something the software can observe.

Fold the machine flat, turn it so the screen is upright, then:

- tap each of the four corners of the panel
- do the same with the pen

A tap landing more than a finger's width from the corner, or a pen stroke that
does not follow the tip, means the transform is not reaching the digitizer. The
panel turning without the input following means the display half is applied and
the input half is not.

The panel and both digitizer nodes are turned by one call each, all three with
the same number:

```bash
hyprctl eval 'hl.monitor({ output = "eDP-1", mode = "1366x768@60", position = "0x0", scale = 1, transform = 1 })'
hyprctl eval 'hl.device({ name = "wacom-pen-and-multitouch-sensor-finger", transform = 1, output = "eDP-1" })'
hyprctl eval 'hl.device({ name = "wacom-pen-and-multitouch-sensor-pen",   transform = 1, output = "eDP-1" })'
```

Note that the two nodes need turning **separately**. They are one physical
digitizer read out twice, Hyprland treats them as two devices, and the pen will
not follow the panel on its own.

`$fold rotate right --force` does all three and puts it back. The `--force` is
needed while the machine is open: in book mode the panel is deliberately held
at landscape, and without it the command declines instead of turning the
screen and having the daemon straighten it a second later.

## If the screen turns the wrong way

The panel's **Settings** section, or the command:

```bash
$fold mapping standard            # correct for this model
$fold mapping portrait-swapped    # the two upright positions are mirrored
$fold mapping landscape-swapped   # the two flat positions are mirrored
$fold mapping rotated-180         # everything is upside down
```

One of the four covers every possible mounting error, because the four
orientations resolve into two pairs and a mounting can only mirror a pair or
invert the lot. If your machine needs anything but `standard`, the mount matrix
in [`hardware.md`](hardware.md) is wrong and the fix belongs there — for this
model it is not, so a problem here means something else changed.

## Watching it work

```bash
$fold debug
```

Streams the fold angle, both tilt channels, the derived screen vector, the
chosen pose and whether the machine is still, updating only when something
changes. Fold it, turn it, and watch which way each column goes. This is the
fastest way to see what a threshold is doing while you move the machine.
