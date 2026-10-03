# How the Yoga 260 worked on Windows, and what to steal

Everything here is reconstructed from Lenovo's own user guide, the Intel ISH
driver stack, and the Linux kernel mailing-list threads where the Windows
behaviour is described by the people who had to reimplement it. Nothing here
is guessed from the USB descriptors. Where Linux disagrees with Windows, both
are stated and the disagreement is explained.

Companion files: `sensors.md` (the IIO motion sensors), `hardware.md` (the
machine), `digitizer.md` (input), `calibration.md` (mount-matrix check),
`inventory.md` (full enumeration), `root_buffer.md` (root-triggered buffer
implementation guide).

## The four modes, per Lenovo

From the official Yoga 260 User Guide (`yoga260_ug_en.pdf`, pp. 23-27):

| mode     | fold angle | keyboard | what it is |
|----------|------------|----------|------------|
| notebook | 0-190      | on       | laptop mode |
| tablet   | 190-270    | off      | folded back, screen facing out |
| tent     | 270-340    | on       | propped in a Λ shape |
| stand    | 340-360    | off      | nearly fully folded back, treated as tablet |

Stand is tablet with a different posture. Tent keeps the keyboard on. The
cut at 190 is the only one that matters for input: past it the keys must go
dead. This plugin's `bookExitDeg=190`, `tentEnterDeg=270`, `tentExitDeg=340`
are those ranges, not tuned values.

The physical part is the "lift-and-lock" keyboard: folding past ~190 sinks
the keys into the deck so they cannot be pressed. The EC then also disables
them electrically. On Windows both happened together, so there was no window
where a folded keyboard typed.

## The stack Windows used

```
EC firmware (fold angle, lift-and-lock)
  -> Intel ISH (PCI 8086:9D35, Sunrise Point-LP)
    -> Intel Integrated Sensor Solution driver v3.0.20.3157 (23 Aug 2016)
      -> HID sensor hub (two collections: base + lid)
        -> Windows Sensor Framework (interrupt-driven input reports)
          -> auto-rotation + tablet-mode + OSK popup
```

Key facts:

- The sensor hub is PCI `8086:9D35` (Sunrise Point-LP ISH). Lenovo's driver
  package is `n1gsh11w.exe` / v3.0.20.3157. Without it Device Manager shows
  no "Sensors" node and auto-rotate dies entirely (Dell/Intel KB threads
  confirm the same failure shape: missing Sensors = no rotation).
- The hub exposes the same six sensors Linux sees (accel, 2x gyro, magn,
  ALS, hinge). Windows reads them as HID input reports delivered on change,
  not by polling sysfs files.
- The hinge sensor is Intel custom usage `HID-SENSOR-INT-020b`, three
  32-bit channels: hinge (fold), screen tilt, keyboard tilt. The EC computes
  the angles; the driver only rescales. Same on both OSes.
- Touch firmware is separate: `N1GGF09W` (VID 056A, PID 5090/5091/5092).
  Finger touch and pen are different firmware paths, which is why one can
  break while the other works.

## HingeAngleService: the piece Linux never got

From the kernel threads (`intel-hid: add support for SW_TABLET_MODE`,
Dec 2020; `iio: hid-sensors: Add hinge sensor driver`, Dec 2020):

- Many 360 hinges use two accelerometers and a Windows userspace process
  called **HingeAngleService**: it reads both accelerometers, computes the
  lid/base angle, and calls back into ACPI through a **DSM on the
  accelerometer node** to tell the firmware the angle.
- That DSM call is what makes the EC emit `0xCC` (enter tablet) / `0xCD`
  (leave tablet) notifies on the `intel-hid` device, which Windows turns
  into tablet-mode: keyboard/touchpad off, rotation unlocked, OSK allowed.
- Linux has no HingeAngleService. So on machines that need the DSM,
  `SW_TABLET_MODE` from `intel-hid` is unreliable or absent. Hans de Goede's
  conclusion: only advertise `SW_TABLET_MODE` on a DMI allow-list, because a
  stuck `SW_TABLET_MODE=0` actively breaks GNOME (disables accel rotation
  and OSK popup).

The Yoga 260 is the worse case: it reports **no `SW_TABLET_MODE` at all**,
only `SW_LID`. There is no ACPI fold event. The hinge IIO device is the only
source of truth, which is why this plugin watches `in_angl0` directly
instead of waiting for a switch that never comes.

## Why Windows felt instant, and what was done about it

| Windows | this plugin (before) |
|---------|---------------------|
| HID input reports push on change | poll sysfs at 10 Hz (`pollSec=0.1`) |
| EC debounces + hysteresis in firmware | `settleSec=0.35` + `hystDeg=12` in userspace |
| mode switch via 0xCC/0xCD notify | mode recomputed only when gyro says still |
| keyboard killed by EC + driver together | `hyprctl eval hl.device` per device, per pass |
| display + digitizer rotate in one display-driver commit | panel, pen, finger by 3 separate `hyprctl eval` calls |
| worst case ~1 frame after EC | ~0.45 s, and the pass itself overran its own budget |

Three consequences that mattered:

1. **Polling at 10 Hz against a 10 Hz sensor is the ceiling.** Faster polling
   re-reads the same report, and the three axis files are read separately, so a
   faster loop tears *more* samples rather than more of them. Windows never saw
   this because it consumed whole reports.
2. **The stillness gate is load-bearing here and was free on Windows.** Windows
   knew the fold from the EC notify, so rotation during a carry was never
   attempted. This plugin must infer stillness from the gyro
   (`stillRotDeg=9`), because a rotating accelerometer still reads 1 g — a
   magnitude gate passed 98% of a recording of the machine being carried.
3. **Rotation was N round-trips, one commit there.** Four, in fact.

## Taken: one read per pass

The loop read every sensor **twice** per pass. `changed()` read all six to build
a comparison key, discarded the sample and returned a bool; then `step()` read
the same six again to build the pose it acted on.

Measured on the running machine, one sensor set costs **53.55 ms**. So a pass
cost **107 ms** — which is why the loop's real period was ~169 ms while the
published `pollSec` claimed 100. The code admitted this in a comment and
published the optimistic number anyway.

That was not only cost. The hardware refreshes those attributes at its declared
10 Hz, so two reads can straddle two reports: the pass that *decided* and the
pass that *acted* could be describing different instants. That is exactly the
torn read the rest of the file works to rule out, reintroduced on the one path
that decides anything. The sample is now taken once and threaded through, so
the pass that detects a change is the pass that acts on it.

**Result: 107 ms -> 53.55 ms per pass. A 2.0x cut in detection latency**, and
the pass now fits inside the 100 ms schedule instead of overrunning it.

## Taken: one transaction per rotation

`apply()` cost four round-trips: a `monitors -j` to rebuild the
mode/position/scale, then one `hyprctl eval` each for the panel, the pen and
the finger.

Hyprland's Lua config is evaluated as a chunk rather than an expression, so
several statements separated by `;` go in a single `eval`. That was verified
against the running compositor before being relied on — panel + finger + pen in
one call returns `ok` and applies. The invariant half of the monitor call is
cached, and dropped whenever the monitor is re-read so a resolution change
cannot leave a stale spec behind.

**Result, measured on the running compositor: 4 `hyprctl` calls -> 1** (warm),
carrying all three statements in one chunk.

Device enable/disable is deliberately *not* folded into that call. It is a
different kind of change with its own lock-screen guard, and the ordering
between them is load-bearing. See `set_devices`.

## Taken: the 190 crossing got a band

The EC does not switch at 190 and release at 190. It engages partway through the
band with hysteresis of its own, which is why Dell's convertible documentation
puts the default keyboard cut-out near 225. So `mode_for` now carries a release
threshold below the exit (`bookReleaseDeg`, default 170): past 190 it is a
tablet, back below 170 it is a book, and in between it keeps whatever it was.
A lid parked at 189/191 no longer takes the keyboard on and off.

That crossing got the first band. The 270 and 340 boundaries have one now too
(`modeHystDeg`, default 10°): entry stays exactly on the guide's angles, and
an engaged mode rides the band past them. The earlier claim here -- that those
edges "change a label and gate no input" -- was wrong about 340, which decides
tablet, and tablet decides the keyboard. A tent propped near 340 flexes ±6°
under hand load with the machine held still, and bare comparisons fluttered
tablet/tent/book, taking the keyboard off and on with it. Found live, fixed,
pinned by round-trip tests on both edges.

## Taken: read only the channel a decision is made from

Notifications turned out to be the wrong thing to attack. The **cost of the
read** was the real problem, and it is not evenly distributed. Timed per
attribute on the running machine, median over 40 reads each:

| attribute | each | three of them |
|---|---|---|
| hinge `in_angl{0,1,2}_raw` | **~10.4 ms** | **31.3 ms (58% of a pass)** |
| accel `in_accel_{x,y,z}_raw` | ~5.5 ms | 17.0 ms |
| gyro `in_anglvel_{x,y,z}_raw` | ~1.8 ms | 5.3 ms |
| a static attribute (`in_angl_scale`) | 0.1 ms | — |

Live sensor data is roughly 100x more expensive to read than a static attribute
on the same filesystem, so this is the driver's cost, not sysfs overhead.

Two thirds of the hinge's cost was being spent on `angl1` and `angl2` — the
two channels that never judge the machine's attitude. `read_pose` reads `angl0`
and nothing else; `mode_for` takes the resulting float and does no I/O at all,
so naming it here was always a shorthand for the pass. The residual they produce
is shown in the panel and, past 25°, written into `status` as a note; no
rotation is ever refused because of *it*.

**But "never gates" was too strong, and this file once said so in a way that was
plainly wrong.** `angl1`/`angl2` are never used to judge the machine's
attitude — that was tried, and comparing the accelerometer against a channel
that never moves produced a figure that grows with base tilt until it blocks
the screen. They *do* still gate in one narrow way: the firmware's own
self-consistency check, `angl0` against `wrap360(angl1 - angl2)`, at a
deliberately loose 150°. A failure marks the sample `ok = False` and `step`
refuses to rotate on it — `test_step_hinge_not_consistent` asserts
`rotate_transaction` is never called. That is a *different* residual from the
reported one: the firmware's arithmetic rather than the machine's attitude.
`docs/calibration.md` has it right, and the error was copied from here into a
test docstring, which is how it survived.

So `Hinge.read_fold` reads the fold channel every pass and refreshes the other
two on `telemetrySec` (default 1 s). Measured: **38.95 ms -> 11.02 ms** for the
hinge, and the whole pass **49.57 ms -> 20.85 ms (2.38x)**.

**That optimisation was inert until recently, and the reason is worth
recording.** `status` is built on every pass — `_publish` calls it whether or
not anything changed — and it took its own `hinge.read()`. So all three
channels came back on every decision pass regardless of `telemetrySec`, and the
counts in `HingeTelemetryCadenceTests` all passed because they exercised
`Hinge` in isolation and never went through `read_pose -> step -> _publish`.
`Hinge.report` now reports the cache the pass already filled; a cold cache (the
`status` and `doctor` commands, which sample nothing themselves) still takes
one real read. Counting attribute reads is what catches this — see the next
section for what counting does *not* catch.

This also *reduces* torn-read risk rather than adding to it. All three channels
arrive in one HID input report, so reading them as three files can straddle two
reports; reading one cannot straddle anything. The self-consistency verdict is
carried forward from the last full read rather than recomputed against a fresh
fold and two stale channels — it checks the firmware's own arithmetic, and
firmware does not change its arithmetic between two reads a second apart.

### A measurement that was wrong, and how it was caught

Persistent file descriptors looked like a 2,275x win: `Path.read_text()` on nine
attributes took 50,101 us and `os.read()` on pre-opened descriptors took 22 us.
It was an artifact. The IIO driver only accepts offset 0, so a persistent
descriptor returns the value once and then `b""` forever. The "fast" path was
reading nothing. The 2,275x figure is the number a benchmark produces when it
measures the absence of work.

Caught by printing the bytes rather than the timing. The first
implementation of `read_fold` had the same disease for a different reason — it
cached the sample but never stamped the time, so every call fell through to the
full three-channel read and the optimisation did nothing. That one was caught by
counting attribute reads instead of timing them, and the tests here exist so it
cannot come back: `test_the_cheap_path_is_actually_cheap` fails if the stamp is
removed, and `test_a_full_read_updates_the_cache_the_cheap_path_depends_on`
asserts the stamp is non-zero.

## NOT taken: event-driven hinge reads

This was the most promising item and **it is confirmed impossible unprivileged,
by systematic testing not inference.**

### Systematic investigation (all run on the live machine, uid 1000, kernel 7.2.5)

1. **inotify directly on sysfs files**, not just POLLPRI:
   `inotify_add_watch(fd, "/sys/bus/iio/devices/iio:device5", IN_ALL_EVENTS)`
   plus watches on each `in_angl{N}_raw`. Hammered the attributes for 5 s
   (each read was a real `open()`+`read()`). **Result: 103 inotify batches
   received** — every one of them is our *own* IN_OPEN | IN_ACCESS. inotify
   fires on *access*, never on *value change*. sysfs IIO attributes are
   synthesized per-read by `->show()`, so there is no underlying file whose
   mtime or value changes to notify on.

2. **POLLPRI on the raw attribute files**: same result, zero events.

3. **The triggered-buffer hardware exists but is root-gated**:
   `/sys/bus/iio/devices/iio:device5/buffer0/` contains `enable`, `length`,
   and `in_angl0_en` — all the controls a userspace IIO buffer consumer needs.
   `current_trigger` reads `hinge-dev5`. BUT every one of those
   control attributes is `-rw-r--r-- root root` for a non-root user, so:
   - you cannot enable the buffer,
   - you cannot select scan elements,
   - and even if you could, the sample data comes from `/dev/iio:device5`
     which is `crw------- root root` — the IIO char device is not readable
     unprivileged.
   The `buffer0/data_available` attribute is *readable* (it reports 0 while
   disabled), but enabling the buffer to make it meaningful requires root.

4. **`in_angl_sampling_frequency` is also root-only** (`-rw-r--r-- root root`),
   so an unprivileged user cannot raise the sensor rate above 10 Hz either.

5. **lseek / pread semantics**: `pread(fd, 4, offset)` on `in_angl0_raw`
   returns the value only at offset 0 and **`b""` at every other offset**.
   This is why a persistent-fd benchmark is a measurement of the absent of
   work (see "A measurement that was wrong" above): the driver only accepts
   a fresh `read(2)` at offset 0.

### What the read-cost distribution actually shows

200 reads, no pause: min 3.52 ms, p10 8.68 ms, median 10.09 ms, p90 19.83 ms,
max 30.27 ms — an **8.6× spread**, not a fixed cost. Reads back-to-back amortise
(3 channels at 30.35 ms, 3× individual reads at 30.99 ms median) because the
HID report that satisfies one covers the next. There is no per-attribute
cache that makes the 2nd and 3rd cheap; the 5–10 ms is the synchronous ISHTP
round-trip each time the driver decides it needs a fresh sample.

This is driver behaviour in kernel-space, not sysfs overhead, and it cannot be
moved from userspace.

### Why "poll faster" is not a substitute

`pollSec=0.1` is the sensor's **declared 10 Hz rate**, not a conservative
default. The sensor publishes a new sample every 100 ms. Polling faster than
that reads the same report again; polling slower misses reports. The only way
to react faster is to consume whole HID input reports as they arrive — which is
exactly what requires the IIO char device (`/dev/iio:device5`, root-only).

### Honest statement of what is left on the table

**One sensor period, always: 100 ms, plus one compositor call.** The pass
itself is now ~21 ms against a 100 ms budget. There is headroom, but it is
spare capacity, not a missed opportunity — the floor is now the hardware's
report rate, not this code's read latency.

### The root-triggered buffer, and why it is not being built

The read cost is a synchronous ISHTP round-trip in the kernel driver, so the
only way to remove it is to let the kernel push samples instead of the userland
pulling them. The hardware does have that machinery: `iio:device5` exposes a
triggered buffer, a named trigger (`hinge-dev5`), and scan-element controls
that could select `in_angl0_raw` alone **[MEASURED]**.

All of it is root-gated. `buffer0/enable`, `scan_elements/*_en` and
`/dev/iio:device5` are `-rw-r--r-- root root` and `crw------- root root`
respectively, so an unprivileged user cannot enable the buffer, cannot select
scan elements, and cannot read the sample data. There is no unprivileged subset
that yields samples **[MEASURED]**.

The full design — service unit, socket protocol, the `/run` directory choice,
and the reasons a bigger buffer makes latency *worse* — is written up as a
proposal in [`root_buffer.md`](root_buffer.md). The short version is that it
would take ~10 ms off a pass that already has ~79 ms of headroom **[MEASURED]**,
would not move the 10 Hz floor, and would cost the plugin its no-root property.
It is documented as a starting point for whoever wants it, not recommended.


## What to copy, still open

1. **Hinge-first, accel-second.** Partly done, and should not regress: the
   *decision* is hinge-first — `mode_for` acts on `angl0` and `lid_vector` on
   the accelerometer, and the mode is what gates the keyboard. The *I/O order*
   is the other way round: `read_pose` samples the accelerometer first and the
   hinge second, because the accel sample is needed to build `base_up` before
   the fold angle can be turned through it. So "hinge-first" describes the
   priority of the decision, not the order of the reads.
2. **Treat `angl1`/`angl2` as telemetry, never attitude gates.** Done.
   `angl2` sits at ~0 whatever the base does (104/103/359 open, 132/132/359
   tent); the attitude residual is reported and nothing blocks on it. They still
   gate the firmware's own self-consistency check at 150°, deliberately and
   deliberately only — see the section above.
3. **Keyboard off belongs to the fold transition, not the steady state.**
   Done, but not as first suggested — the suggestion was wrong and the
   opposite gap was real. Sampling the guard only on the fold transition
   would have missed lock transitions entirely: `changed()` watches sensors
   and locking moves no sensor, so locking a folded-and-still machine never
   reached `set_devices` at all and the keyboard stayed off at the password
   prompt — the exact lockout the guard exists to prevent. What shipped
   instead: `poll_lock_state` watches the lock screen on the `verifySec`
   slow timer (one probe per 5 s, next to the re-assert that already costs
   the same order of round trips) and forces a full `step()` only on lock
   transitions. The per-change probe inside `set_devices` is kept: it is
   already bounded to change-passes, and its outcome depends on lock state,
   so it cannot be skipped on steady-state grounds. First probe only
   records, so startup forces no extra pass.
4. **Digitizer rotation is a display-driver feature on Windows; on Linux it is a
   libinput calibration matrix or nothing.** Hyprland 0.56.2 does hand that
   matrix to libinput for both nodes — `setTabletConfigs()` and
   `setTouchDeviceConfigs()` both call
   `libinput_device_config_calibration_set_matrix`. This was long recorded here
   as "accepted and dropped", on the evidence that `hyprctl -j devices` reported
   `transform = None` after `ok`; that field is never emitted for tablets, so the
   evidence was of an absent key. See [`digitizer.md`](digitizer.md). The udev
   `LIBINPUT_CALIBRATION` hwdb route is the only remaining mechanism, and it
   needs observed raw ranges (the declared range is 0..0), root, and a device
   reopen. Do not ship a guessed matrix.


## Sources

- Lenovo Yoga 260 User Guide, `yoga260_ug_en.pdf`, Operating modes pp. 23-27
- Lenovo Support `ps500543` — Intel Integrated Sensor Solution Advisory
- Intel Integrated Sensor Solution driver v3.0.20.3157 (23 Aug 2016,
  PCI `8086:9D35`), package `n1gsh11w.exe`
- Touch Screen Firmware Update Tool `N1GGF09W` (056A/5090/5091/5092)
- kernel: `[PATCH 1/3] intel-hid: add support for SW_TABLET_MODE`
  (lists.openwall.net, 2020-12-01) — HingeAngleService, DSM, 0xCC/0xCD
- kernel: `[PATCH v4 2/3] iio: hid-sensors: Add hinge sensor driver`
  (2020-12-15) — `hinge`/`screen`/`keyboard` channels, EC-computed
- kernel docs: `hid/intel-ish-hid` — ISHTP transport, IIO ABI unchanged
- Dell KB `000132403` — tablet mode past 225, rotation lock requires
  tablet mode + ISH driver
- Notebookcheck / LaptopMag / Windows Central Yoga 260 reviews —
  lift-and-lock keyboard, 360 hinge, pen (ThinkPad Pen Pro, Wacom AES)
