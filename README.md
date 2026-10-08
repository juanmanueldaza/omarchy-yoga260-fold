# Yoga 260 Fold

> **Lenovo ThinkPad Yoga 260 (20FE) only. It refuses every other machine.**
> Install: `omarchy plugin add https://github.com/juanmanueldaza/omarchy-yoga260-fold --enable`
> Remove: `omarchy plugin remove juanmanueldaza.yoga260-fold` plus `omarchy-yoga260-fold libwacom remove`
> Runs unsandboxed inside omarchy-shell. No root for the daemon; sudo only if you explicitly run `hwdb apply`.
> Does not ship an on-screen keyboard (optional companion below).
> A hand turn locks rotation on; restore with `rotate normal` then `lock off`.
> Installs mutable upstream HEAD, not the marketplace-reviewed commit — see "Marketplace snapshot" below.

Tablet mode and auto-rotation for the **Lenovo ThinkPad Yoga 260 (20FE)** on
Omarchy, built on the machine's hinge-angle sensor.

## The problem this exists for

The Yoga 260 folds all the way round, but it never says so. There is no
`SW_TABLET_MODE` bit on any of its input devices and no ACPI event for the fold
— the lid switch is the only switch it has. Every other convertible plugin for
Hyprland or Omarchy keys off that switch, so on this machine they either refuse
to work or fall back to rotating on every tilt, including on a lap.

What the Yoga 260 does have is an Intel ISHTP **hinge-angle sensor**, which the
kernel exposes as an IIO device with three channels:

| channel | label | meaning |
|---|---|---|
| `in_angl0` | `hinge` | the fold angle between the two halves |
| `in_angl1` | `screen` | the lid's tilt against gravity |
| `in_angl2` | `keyboard` | the base's tilt against gravity |

That is more information than a switch, not less: it separates a tent from a
flat-folded tablet, so the fold can be watched rather than inferred.

## The second half of the problem

The accelerometer in this machine is in the **base**, not the lid. A reader that
takes the base's attitude as the screen's attitude is only right while the
machine is open, and exactly wrong once it is folded — which is the only time
rotation matters. So the screen's attitude is rebuilt by turning the base's
gravity vector through the measured fold angle:

```
t = -cos(θ)·base_Y + sin(θ)·base_Z     # towards the top edge of the screen
n = -sin(θ)·base_Y - cos(θ)·base_Z     # out of the screen face
```

and the screen's right axis, which runs along the hinge, carries across
unchanged.

The accelerometer also reports **no `in_accel_mount_matrix`**, so its axes mean
nothing on their own — what "left" and "up" are is a property of how the chip is
glued into the base. This plugin carries that property as a matrix and refuses to
rotate if it is not a proper rotation, because a matrix that is a reflection
inverts the sense of every turn.

## The one thing it cannot see

An accelerometer measures gravity and nothing else. **Gravity is a fixed world
vector, and rotating a machine about the vertical leaves it exactly where it was
in the base's frame** — verified here at 0°, 20°, 40° and 70° of tilt alike: the
reading is byte-identical at every quarter turn. So yaw is not *mostly*
unknowable to an accelerometer, it is entirely unknowable, at any tilt. There is
no reading anywhere on this machine that says which way a flat screen is turned.

What the panel used to do was pretend otherwise. It answered a hardcoded
`"Landscape while open"` for any open machine — a string chosen by the lid being
open, not a measurement — so a machine spun to portrait was told it was in
landscape, the one case the sensor is least able to see. It now reports
**"Flat · cannot tell which way it is turned"** when the base is flat enough that
no screen axis means anything, and otherwise reports what it actually measured.

What it does instead is follow the turn, from the gyroscope: the rate about
gravity is integrated and a deliberate quarter turn moves the panel.

That integration is gated, and the gate is the whole design. Measured on this
machine at rest, the gyroscope reads **+0.274 deg/s about the vertical** —
open-loop integration of that is 16° a minute and a full quarter turn wrong
inside ten. So nothing is integrated unless the machine is genuinely turning: a
stationary machine reads 0.27, well inside the deadband, so the bias is never
banked. What accumulates is a sum of real turns, which cannot walk away while
the machine sits still.

It remains a *relative* estimate, measured from the last orientation the
accelerometer could establish; tilt the machine and the accelerometer takes over
and the estimate is dropped.

### Which way is portrait

Both paths now answer in **orientation names** and hand them to the same
`transform_for` that applies the panel's **mapping** setting. They did not used
to: the flat path added a quarter turn straight onto the transform in force,
which had already had `mapping` folded into it, so it was doing arithmetic in a
different space from the accelerometer's. The two then disagreed — landscape
came out as portrait — and the mapping was bypassed entirely.

Which way a turn reads is the one value here that cannot be derived, because this
gyroscope sits at ~0.2 deg/s at rest and so never shows a turn to watch. It
depends on the sign of the base's X axis, which is what `calibrate` settles. It
is therefore `yawSign`, deliberately separate from `mapping`: reusing `mapping`
here would fix the flat case and break every other one.

```bash
$fold setting yawSign -1     # if the screen turns the wrong way while flat
```

## Install

```bash
omarchy plugin add https://github.com/juanmanueldaza/omarchy-yoga260-fold --enable
```

Or, from a checkout, copy it into place and enable it:

```bash
cp -r . ~/.config/omarchy/plugins/juanmanueldaza.yoga260-fold
omarchy shell shell rescanPlugins   # if it is already running
omarchy bar move juanmanueldaza.yoga260-fold --section right
```

Then give the pen its proper libwacom entry, which this machine is missing:

```bash
~/.config/omarchy/plugins/juanmanueldaza.yoga260-fold/bin/omarchy-yoga260-fold libwacom install
```

Remove the whole thing with `omarchy plugin remove juanmanueldaza.yoga260-fold` and
`omarchy-yoga260-fold libwacom remove`.

Optional: an on-screen keyboard. This plugin drives one, it does not ship one —
the Fold panel's keyboard button only appears when a keyboard plugin is installed:

```bash
omarchy plugin add https://github.com/abdxdev/omarchy-onscreen-keyboard --enable
```

## Marketplace snapshot

Marketplace verification covers the exact approved commit. `omarchy plugin add`
and `omarchy plugin update` pull the branch HEAD, which the marketplace shows as
`Update unverified` until a newer commit is verified — the installed code and
the reviewed snapshot can differ, so check the listing before trusting an update.

## What it does

- **Follows the machine, in every pose it can see.** Rotate it and the screen
  turns; the finger sensor and the pen follow it, and both are bound to the
  laptop's own panel so an external monitor does not stretch the pen across two
  screens. A machine flat on a desk is the exception, and is described above:
  its in-plane rotation is invisible to the accelerometer and is followed from
  the gyroscope instead.
- **Knows three poses, not two.** A 360 convertible is a laptop, a tent and a
  tablet, and the fold is what tells them apart:

  | fold | pose | keyboard |
  |---|---|---|
  | under 190° | book, lid open for use | on |
  | 190–270° | tablet, folded back | off |
  | 270–340° | tent, propped on its own lid | on |
  | 340° and over | stand, treated as tablet | off |

  The screen follows the machine in all three. An earlier version held it at
  landscape whenever the lid was open, on the reasoning that a laptop on knees
  should not flip; on this machine that rule ate almost the whole session,
  because a 260 convertible is held open far more often than it is folded.

  The 190 boundary is the one that gates input, and it is a **band, not a
  line**: leave book at 190 (`bookExitDeg`), come back to it at 170
  (`bookReleaseDeg`), and keep whatever you were in between. The EC does not
  switch at 190 and release at 190 either — it engages partway through with
  hysteresis of its own, which is why convertible documentation puts the cut-out
  near 225 — so a bare comparison on one number chatters on a lid left near the
   edge. The 270 and 340 boundaries keep the guide's exact angles for entry,
   and an engaged mode rides `modeHystDeg` (10°) past them before it lets go:
   a tent propped near 340 flexes under hand load and bare comparisons
   fluttered tablet/tent, taking the keyboard with it.

  On top of that, the mode is only recomputed while the machine is still: one
  being carried keeps the mode it last settled in, and a keyboard that switches
  itself off and on again is a keyboard you cannot type on.

  Note that a tent normally *stays* landscape, and that is correct: propped as
  an A, the screen faces the viewer across the tent, so it reads landscape on
  its own. The arrangement that wants portrait is the whole tent turned ninety
  degrees in the plane.
- **Tablet mode locks the keyboard off.** The keyboard, the TrackPoint, the
  extra-buttons cluster and the touchpad are all useless folded flat, so they
  are switched off and put back when the machine opens — but never while the
  session is locked, and never on a reading it is unsure of.
- **Rotation lock** from the bar, so reading in bed does not spin the screen.
  Turning the screen by hand engages it, the way a tablet does, and says so.
- **Holds still.** Nothing turns while the gyroscope says the machine is being
  moved, a new pose has to beat the current one by a margin, and the decision
  has to hold before the screen moves. A hinge reading that disagrees with
  itself stops the screen rather than guessing.
- **Heals after a config reload.** `hyprctl reload` rebuilds the monitor rules
  and straightens a turned panel; the transform is re-asserted on a timer.

### How a turn is decided, and how fast

The response budget is **0.45 s**: one 0.1 s poll to notice, plus a 0.35 s
settle window the decision has to survive.

Two earlier designs were much slower, and both are worth not repeating:

- **Averaging the pose is wrong here.** The pose used to be an average over the
  last 0.6 s, which lags the machine by that much and — because the decision is
  derived from the average — restarts the settle clock every time the average
  crosses a boundary on its way to a new pose. A recording caught the
  orientation changing six times during one movement, so the screen never moved
  at all. The pose is now the current sample; the window is kept only to say
  how long things have been steady.
- **A tie is not indecision.** A small margin between the best and second-best
  axis used to be read as "cannot decide", and the screen was held where it
  was, forever. But a machine held at forty-five degrees to its own screen axes
  is a perfectly ordinary posture, and that is exactly what a tie looks like.
  A tie now means *keep the incumbent* — hysteresis, in the sense it is
  actually meant in.

The decision path reads the panel's settings once per change rather than
parsing `shell.json` on every pass, because it sits directly in the path of
the thing that has to feel instant.

## What it will not do

- It refuses to touch anything that is not a Yoga 260. `product_version` has to
  say so; the board prefix cannot tell a Yoga from anything else.
- It does not use `iio-sensor-proxy`. Reading the three IIO nodes directly needs
  no package, and a proxy that applies an identity mount matrix to a sensor with
  no mount matrix of its own reports the chip's axes rather than the machine's.
- It cannot give a folded machine its lock screen back an on-screen keyboard.
  Opening the lid restores the keyboard at once.

## The command

```bash
fold=~/.config/omarchy/plugins/juanmanueldaza.yoga260-fold/bin/omarchy-yoga260-fold

$fold doctor        # what this machine actually has, read-only
$fold status        # the state the bar shows, as JSON
$fold debug         # live raw and derived values, while you fold it
$fold self-test     # the arithmetic, with no hardware involved
$fold calibrate     # guided, human-verified calibration
$fold rotate next   # turn it by hand (not in book mode; --force overrides)
$fold lock toggle
$fold mapping standard
$fold keyboard toggle   # the on-screen keyboard
$fold libwacom status
$fold hwdb show     # the systemd sensor entry, not applied
$fold hwdb apply    # and write it, which wants sudo
$fold hwdb remove   # undo the above: delete the entry, which wants sudo
$fold record        # every sensor reading to a JSONL file, live
$fold analyze       # summarise what `record` wrote
$fold daemon        # the watcher itself; the shell starts this for you
```

`doctor` and `self-test` are the two worth running first. `self-test` checks the
matrix, the hinge rotation and the pose classification in closed form, so it is
also the check to run on a machine that is *not* a Yoga 260.

## Calibration

Two things cannot be derived and have to be confirmed by a person: the sign of
the base's X axis, and that the digitizer's transform is really being applied.
`calibrate` walks through both, and checks the mount matrix against the hinge
sensor's independent tilt channels on the way.

If the screen turns the wrong way, the panel's **Settings** offers the four
mappings as Standard, Upright, Flat and 180. Those are panel labels; the command
takes the values underneath them, and rejects the labels:

| panel label | command value |
|---|---|
| Standard | `standard` |
| Upright | `portrait-swapped` |
| Flat | `landscape-swapped` |
| 180 | `rotated-180` |

`$fold mapping upright` is an error. See
[`docs/calibration.md`](docs/calibration.md).

## Layout

```
manifest.json                     the plugin contract
Service.qml                       mounts the daemon once, owns the state
Fold.qml                          the bar button and its panel
bin/omarchy-yoga260-fold          the daemon and CLI, Python 3, no dependencies
tests/                            328 tests + 16 subtests; the arithmetic is the point
docs/hardware.md                  this machine, in the detail it deserves
docs/calibration.md               how to confirm it by hand
```

## License

Apache-2.0. See [LICENSE](LICENSE).
