#!/usr/bin/env python3
"""Record what the Yoga 260 digitizer actually reports, on one clock.

The question this exists to answer is the one `docs/digitizer.md` cannot settle
by reading code: does the raw `ABS_X`/`ABS_Y` of a tap on a given physical spot
change when the panel is rotated, or does the digitizer go on answering about the
machine's native landscape frame regardless?

The answer is in the numbers, not in a config file, so this writes them down.
One JSON object per sample, newline-delimited, carrying three things against one
timestamp:

  * both evdev nodes of the Wacom AES module (USB 056a:5091), found by USB id
    rather than by event number;
  * the IIO motion sensors -- accelerometer, gyroscope and the hinge's three
    channels -- so every tap has the posture it happened in attached to it;
  * the panel transform Hyprland is currently applying.

Three things it deliberately does not do:

  * **It does not grab the devices.** No EVIOCGRAB, no exclusive open. The pen
    and the finger keep working while it runs, which is the only way to collect
    the data, because the whole procedure is "tap a corner, then tap it again
    after turning".
  * **It does not touch anything.** Read-only file descriptors, `hyprctl -j
    monitors` for the transform, nothing written but the output file.
  * **It needs root**, because the evdev nodes are `crw------- root root` on
    this machine. The IIO attributes and `hyprctl` do not.

Standard library only, Python 3.11+.

    sudo tools/capture-digitizer.py --seconds 120 --out /tmp/digitizer.jsonl

The routine to run while it records: tap each screen corner in landscape, turn
the machine, tap the same corners again, then draw with the pen. Comparing the
same physical spot before and after the turn is the whole experiment.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import select
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# struct input_event: struct timeval (two 8-byte longs) + u16 type + u16 code +
# s32 value, 24 bytes on the 64-bit kernel ABI. Note `q` rather than `l`:
# struct's standard-mode `l` is 4 bytes even though the kernel's is 8, and the
# native (unsuffixed) form happens to agree only by accident of LP64.
# '<' is explicit little-endian and, crucially, no alignment padding -- which is
# what the kernel's layout actually is.
EVENT = struct.Struct("<qqHHi")
INPUT_EVENT_BYTES = EVENT.size

EV_ABS = 0x03
ABS_X, ABS_Y = 0x00, 0x01

DIGITIZER_USB = "3/56a/5091"
IIO_ROOT = Path("/sys/bus/iio/devices")

# Only the axes worth writing down. The finger carries ABS_MISC, ABS_RY3,
# ABS_RX4 and ABS_WIDTH as well; they are not position, and nothing here reads
# them. See docs/digitizer.md for why their absence matters.
AXES = {ABS_X: "ABS_X", ABS_Y: "ABS_Y"}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def find_digitizer_nodes() -> dict[str, str]:
    """The module's two evdev nodes, found by USB id rather than by number.

    Node numbers move between boots, and this repository's whole position is
    that hardware facts should be looked up by property, never by index. So:
    the kernel's own `uevent` narrows the search to this USB id, and `udevadm`
    supplies the `ID_INPUT_*` classification -- which is not in the uevent file
    at all, only in the udev database, and is what actually separates the pen
    from the finger.
    """
    candidates = []
    for event in sorted(Path("/sys/class/input").glob("event*")):
        uevent = event / "device" / "uevent"
        if not uevent.is_file():
            continue
        props = {}
        for line in (read_text(uevent) or "").splitlines():
            key, _, value = line.partition("=")
            props[key] = value
        if props.get("PRODUCT", "").startswith(DIGITIZER_USB):
            candidates.append(str(Path("/dev/input") / event.name))
    if not candidates:
        return {}

    nodes: dict[str, str] = {}
    for path in candidates:
        try:
            raw = subprocess.run(
                ["udevadm", "info", "--query=property", "--name", path],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        props = {}
        for line in raw.stdout.decode("utf-8", "replace").splitlines():
            key, _, value = line.partition("=")
            props[key] = value
        if props.get("ID_INPUT_TABLET") == "1":
            nodes["pen"] = path
        elif props.get("ID_INPUT_TOUCHSCREEN") == "1":
            nodes["finger"] = path
    return nodes


def iio_device(name: str) -> Path | None:
    """An IIO device by its `name` file. Probe order is not stable across boots."""
    try:
        entries = sorted(IIO_ROOT.glob("iio:device*"))
    except OSError:  # pragma: no cover
        return None
    for entry in entries:
        if read_text(entry / "name") == name:
            return entry
    return None


def read_axis_triplet(
    device: Path | None, prefix: str, scale_file: str
) -> dict[str, Any] | None:
    """`in_*_{x,y,z}_raw` with the device's own scale applied, in SI units."""
    if device is None:
        return None
    values = []
    for axis in ("x", "y", "z"):
        raw = read_text(device / f"{prefix}_{axis}_raw")
        if raw is None:
            return None
        try:
            values.append(float(raw))
        except ValueError:
            return None
    scale = read_text(device / scale_file)
    try:
        factor = float(scale) if scale is not None else 1.0
    except ValueError:  # pragma: no cover
        factor = 1.0
    return {
        "raw": values,
        "scale": factor,
        "value": [round(v * factor, 6) for v in values],
    }


def read_hinge(device: Path | None) -> dict[str, Any] | None:
    """The hinge's three channels, one count per degree on this machine."""
    if device is None:
        return None
    channels = []
    for index in (0, 1, 2):
        raw = read_text(device / f"in_angl{index}_raw")
        if raw is None:
            return None
        try:
            channels.append(float(raw))
        except ValueError:  # pragma: no cover
            return None
    return {"channels": channels, "device": device.name}


def panel_transform(panel: str | None) -> dict[str, Any] | None:
    """What Hyprland is currently giving the panel, read-only."""
    try:
        raw = subprocess.run(
            ["hyprctl", "-j", "monitors"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        monitors = json.loads(raw.stdout or b"[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if not isinstance(monitors, list) or not monitors:
        return None

    def pick(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        for monitor in candidates:
            name = str(monitor.get("name", ""))
            if panel is None:
                if str(monitor.get("description", "")).upper().startswith("EDP"):
                    return monitor
            elif name == panel:
                return monitor
        return candidates[0] if len(candidates) == 1 else None

    chosen = pick([m for m in monitors if isinstance(m, dict)])
    if chosen is None:
        return None
    return {
        "name": chosen.get("name"),
        "transform": chosen.get("transform"),
        "width": chosen.get("width"),
        "height": chosen.get("height"),
    }


class Node:
    """One evdev node, open read-only and never grabbed.

    O_NONBLOCK is set so a `read()` that outruns the queue returns EAGAIN rather
    than stalling the loop; `select()` says when there is something to read.
    """

    def __init__(self, label: str, path: str) -> None:
        self.label = label
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.axes: dict[int, float] = {}

    def drain(self) -> int:
        count = 0
        while True:
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                return count
            except OSError:  # pragma: no cover
                return count
            if not data:
                # End of stream, or the node went away. Without this the loop
                # spins at 100% on a device that will never produce another
                # event, which is exactly what it must not do to a machine
                # somebody is using with a pen in their hand.
                return count
            for offset in range(
                0, len(data) - INPUT_EVENT_BYTES + 1, INPUT_EVENT_BYTES
            ):
                _, _, kind, code, value = EVENT.unpack_from(data, offset)
                if kind != EV_ABS or code not in AXES:
                    continue
                self.axes[code] = float(value)
                count += 1

    def sample(self) -> dict[str, Any]:
        return {"path": self.path, "axes": {AXES[c]: v for c, v in self.axes.items()}}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record both digitizer nodes, the IIO sensors and the panel transform.",
    )
    parser.add_argument(
        "--seconds", type=float, default=120.0, help="how long to record for"
    )
    parser.add_argument("--hz", type=float, default=10.0, help="sampling rate")
    parser.add_argument("--out", default="-", help="output file, or - for stdout")
    parser.add_argument(
        "--panel", default=None, help="force a monitor name instead of eDP-*"
    )
    parser.add_argument(
        "--panel-refresh",
        type=float,
        default=2.0,
        help="how often to re-read the panel transform; it needs a subprocess",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.seconds <= 0 or args.hz <= 0:
        print("--seconds and --hz must both be positive", file=sys.stderr)
        return 2

    paths = find_digitizer_nodes()
    missing = {"pen", "finger"} - paths.keys()
    if missing:
        print(
            f"no digitizer node found for {sorted(missing)}; is this USB "
            f"{DIGITIZER_USB.replace('/', ':')}?",
            file=sys.stderr,
        )
        return 1

    try:
        nodes = [Node("pen", paths["pen"]), Node("finger", paths["finger"])]
    except OSError as error:
        print(
            f"cannot open {error.filename}: {error.strerror} (root is required)",
            file=sys.stderr,
        )
        return 1

    sensors = {
        "accel": iio_device("accel_3d"),
        "gyro": iio_device("gyro_3d"),
        "hinge": iio_device("hinge"),
    }
    period = 1.0 / args.hz
    started = time.monotonic()
    deadline = started + args.seconds
    next_sample = started
    panel = panel_transform(args.panel)
    panel_at = time.monotonic()
    written = 0

    # The output file is deliberately open for the whole capture rather than
    # written a line at a time, so a capture interrupted half-way still has
    # every sample up to that point on disk.
    with contextlib.ExitStack() as stack:
        handle = (
            sys.stdout
            if args.out == "-"
            else stack.enter_context(open(args.out, "w", encoding="utf-8"))
        )
        try:
            print(
                f"# nodes   pen={paths['pen']} finger={paths['finger']}\n"
                f"# iio     accel={sensors['accel']} gyro={sensors['gyro']} hinge={sensors['hinge']}\n"
                f"# rate    {args.hz:g} Hz for {args.seconds:g}s -> {args.out}",
                file=handle,
            )
            handle.flush()

            while time.monotonic() < deadline:
                now = time.monotonic()
                if now < next_sample:
                    select.select(
                        [n.fd for n in nodes], [], [], min(period, next_sample - now)
                    )
                    continue
                next_sample = max(next_sample + period, time.monotonic())

                counts = {n.label: n.drain() for n in nodes}
                # Two clocks, on purpose. `t` is wall time, so a row can be
                # lined up against a recording of something else; `elapsed` is
                # from the monotonic clock, so it cannot jump when NTP steps the
                # wall clock mid-capture and leave a hole in the interval column.
                stamp = time.time()
                mono = time.monotonic()
                if mono - panel_at >= args.panel_refresh:
                    panel = panel_transform(args.panel)
                    panel_at = time.monotonic()

                sample = {
                    "t": round(stamp, 4),
                    "elapsed": round(mono - started, 3),
                    "panel": panel,
                    "iio": {
                        "accel": read_axis_triplet(
                            sensors["accel"], "in_accel", "in_accel_scale"
                        ),
                        "gyro": read_axis_triplet(
                            sensors["gyro"], "in_anglvel", "in_anglvel_scale"
                        ),
                        "hinge": read_hinge(sensors["hinge"]),
                    },
                    "nodes": {n.label: n.sample() for n in nodes},
                    "events": counts,
                }
                handle.write(json.dumps(sample) + "\n")
                written += 1
                if written % max(1, int(args.hz)) == 0:
                    handle.flush()
        finally:
            for node in nodes:
                os.close(node.fd)

    print(f"\n# wrote {written} samples to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
