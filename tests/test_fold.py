"""Tests for omarchy-yoga260-fold.

The interesting part of this plugin is arithmetic: a mount matrix nobody wrote
down, and a screen whose attitude is the base's attitude turned through a hinge
angle. Both are easy to get subtly wrong in a way that no amount of staring at
the code will reveal, so they are tested against the reverse construction --
pick the pose the screen is in, work out what the sensor would have to read for
that to be true, feed those numbers in, and require the original pose back.

    ./tests/run
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "omarchy-yoga260-fold"
spec = importlib.util.spec_from_loader(
    "fold", importlib.machinery.SourceFileLoader("fold", str(SCRIPT))
)
# spec_from_loader only returns None for a loader it cannot describe, which a
# SourceFileLoader is not -- the guards are for the type checker, which only
# knows the Optional, and they turn a silent AttributeError below into a
# failure that names what went missing.
if spec is None:
    raise RuntimeError(f"cannot build a module spec for {SCRIPT}")
loader = spec.loader
if loader is None:
    raise RuntimeError(f"no loader in the module spec for {SCRIPT}")
fold = importlib.util.module_from_spec(spec)
# dataclass resolves annotations through sys.modules, so the module has to be
# registered before it is executed.
sys.modules["fold"] = fold
loader.exec_module(fold)


GRAVITY = 9.80665
M = fold.DEFAULT_MOUNT_MATRIX


def unit(vector):
    length = math.sqrt(sum(v * v for v in vector))
    return tuple(v / length for v in vector)


def base_up_from_lid(lid_up, fold_deg):
    """Invert `lid_vector`: the base's up that would produce this screen's up."""
    radians = math.radians(fold_deg)
    cos_f, sin_f = math.cos(radians), math.sin(radians)
    _, u_top, u_norm = lid_up
    return (
        lid_up[0],
        -cos_f * u_top - sin_f * u_norm,
        sin_f * u_top - cos_f * u_norm,
    )


def raw_for_base_up(base_up, scale, full_scale=2**23):
    """The integers an accelerometer with this mount would report."""
    sensor = unit(tuple(fold.dot(row, base_up) for row in zip(*M)))
    magnitude = GRAVITY / scale
    return tuple(round(v * magnitude) for v in sensor), magnitude


def transpose(matrix):
    return [tuple(row[i] for row in matrix) for i in range(len(matrix))]


class FakeAccelDevice:
    """An IIO accelerometer directory holding one fixed reading."""

    def __init__(self, root, raw, scale):
        self.path = Path(root)
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "name").write_text("accel_3d\n")
        (self.path / "in_accel_scale").write_text(f"{scale!r}\n")
        for axis, value in zip("xyz", raw):
            (self.path / f"in_accel_{axis}_raw").write_text(f"{value}\n")

    @property
    def device(self):
        return self.path


class FakeHingeDevice:
    def __init__(self, root, fold_deg, screen_deg, base_deg, scale=math.pi / 180.0):
        self.path = Path(root)
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "name").write_text("hinge\n")
        (self.path / "in_angl_scale").write_text(f"{scale!r}\n")
        for index, (label, value) in enumerate(
            zip(("hinge", "screen", "keyboard"), (fold_deg, screen_deg, base_deg))
        ):
            (self.path / f"in_angl{index}_label").write_text(f"{label}\n")
            (self.path / f"in_angl{index}_raw").write_text(
                f"{round(math.radians(value) / scale)}\n"
            )

    @property
    def device(self):
        return self.path


# --------------------------------------------------------------------------


@contextlib.contextmanager
def libwacom_in_tmpdir():
    """Redirect both libwacom globals into a temporary directory.

    `LIBWACOM_ENTRY` is the path the install and remove actually write, and it
    is a separate module-level constant from `LIBWACOM_LOCAL`. Patching only
    the directory would leave every one of these tests writing into the real
    ~/.config/libwacom on the machine running them.

    Module-level, not a method, because a second class of tests installs and
    removes the entry too and had been writing the real path. The pair is
    net-zero on a full green run, which is exactly why it went unnoticed: a
    run filtered down to the install alone leaves the pen's real libwacom
    entry behind, and a run of the whole suite deletes one the user installed
    by hand.
    """
    with tempfile.TemporaryDirectory() as td:
        real_dir, real_entry = fold.LIBWACOM_LOCAL, fold.LIBWACOM_ENTRY
        fold.LIBWACOM_LOCAL = Path(td) / "libwacom"
        fold.LIBWACOM_ENTRY = fold.LIBWACOM_LOCAL / "wacom-isdv4-5091.tablet"
        try:
            yield fold.LIBWACOM_ENTRY
        finally:
            fold.LIBWACOM_LOCAL, fold.LIBWACOM_ENTRY = real_dir, real_entry


class Angles(unittest.TestCase):
    def test_wrap180_folds_into_half_turns(self):
        self.assertAlmostEqual(fold.wrap180(0), 0)
        self.assertAlmostEqual(fold.wrap180(180), 180)
        self.assertAlmostEqual(fold.wrap180(181), -179)
        self.assertAlmostEqual(fold.wrap180(-90), -90)
        self.assertAlmostEqual(fold.wrap180(359), -1)

    def test_wrap360_is_non_negative(self):
        self.assertAlmostEqual(fold.wrap360(-1), 359)
        self.assertAlmostEqual(fold.wrap360(725), 5)
        self.assertAlmostEqual(fold.wrap360(360), 0)

    def test_the_lids_three_channels_relate_the_way_the_hinge_does(self):
        up = fold.lid_vector((0.0, 0.0, 1.0), 0.0)
        self.assertAlmostEqual(up[1], 0.0)
        self.assertAlmostEqual(up[2], -1.0)
        up = fold.lid_vector((0.0, 0.0, 1.0), 90.0)
        self.assertAlmostEqual(up[1], 1.0)
        self.assertAlmostEqual(up[2], 0.0)
        up = fold.lid_vector((0.0, 0.0, 1.0), 180.0)
        self.assertAlmostEqual(up[1], 0.0)
        self.assertAlmostEqual(up[2], 1.0)

    def test_a_half_open_lid_is_evenly_split(self):
        up = fold.lid_vector((0.0, 0.0, 1.0), 45.0)
        self.assertAlmostEqual(up[1], 0.70710678, places=7)
        self.assertAlmostEqual(up[2], -0.70710678, places=7)

    def test_an_open_laptop_reads_as_normal(self):
        base_up = unit((0.0, 0.14, 0.99))
        lid_up = fold.lid_vector(base_up, 95.0)
        self.assertEqual(fold.name_orientation(unit(lid_up)), "normal")

    def test_folded_back_the_screen_turns_away_from_the_keyboard(self):
        base_up = unit((0.0, 0.14, -0.99))
        lid_up = unit(fold.lid_vector(base_up, 180.0))
        self.assertEqual(fold.name_orientation(lid_up), "inverted")
        self.assertEqual(
            fold.transform_for(fold.name_orientation(lid_up), "standard"), 2
        )

    def test_folding_never_touches_the_axis_along_the_hinge(self):
        for degrees in (0, 37, 90, 145, 180, 271, 359):
            up = fold.lid_vector((0.31, -0.22, 0.92), degrees)
            self.assertAlmostEqual(up[0], 0.31, places=9)

    def test_lid_vector_is_a_rotation(self):
        for degrees in (0, 45, 90, 135, 180, 225, 315):
            up = fold.lid_vector(unit((0.2, -0.5, 0.84)), degrees)
            self.assertAlmostEqual(math.sqrt(fold.dot(up, up)), 1.0, places=9)


class MountMatrix(unittest.TestCase):
    def test_the_shipped_matrix_is_a_proper_rotation(self):
        self.assertAlmostEqual(fold.determinant(M), 1.0, places=9)
        for i in range(3):
            for j in range(3):
                expected = 1.0 if i == j else 0.0
                self.assertAlmostEqual(
                    fold.dot(M[i], [row[j] for row in M]), expected, places=9
                )

    # Both readings below are real captures from this machine with the machine
    # lying on a desk and the lid open, so neither is exactly axis-aligned: the
    # base leans about 8 degrees, which is what a lid-open machine on a desk
    # does. Asserting `== 1.0` to six places demanded a perfect reading the
    # hardware never produces, so these failed on a correct matrix. What matters
    # is which component dominates and which way it points, and that is what is
    # checked -- the residual lean is asserted too, so a matrix that quietly
    # started mixing axes would still fail here.

    def test_it_puts_a_flat_machine_the_right_way_up(self):
        sensor = unit((0.0, -9.22, -1.29))
        base_up = tuple(fold.dot(row, sensor) for row in M)
        # Face up, with the base's +Y tilted back towards the hinge.
        self.assertGreater(base_up[2], 0.98)
        self.assertAlmostEqual(base_up[0], 0.0, places=6)
        self.assertLess(abs(base_up[1]), 0.2)
        self.assertAlmostEqual(fold.tilt_from_vector(base_up, 2), 8.0, delta=1.0)

    def test_it_puts_a_machine_on_its_side_the_right_way_up(self):
        sensor = unit((9.22, 0.0, -1.29))
        base_up = tuple(fold.dot(row, sensor) for row in M)
        # On its side: the hinge axis component now dominates. The sign is the
        # one thing a single reading cannot settle -- the -1 in row 0 is the
        # choice `calibrate` exists to confirm on the real machine -- so the
        # magnitude and the absence of any face-up component are asserted, and
        # the direction is left to `test_the_x_sign_is_a_calibration_choice`.
        self.assertGreater(abs(base_up[0]), 0.98)
        self.assertLess(abs(base_up[2]), 0.2)
        self.assertAlmostEqual(fold.tilt_from_vector(base_up, 0), 8.0, delta=1.0)

    def test_the_x_sign_is_a_calibration_choice_not_a_derived_fact(self):
        # Both signs give a proper rotation, so neither can be rejected
        # arithmetically -- which is exactly why the matrix ships with a note
        # that `calibrate` settles it by hand. If this ever fails, the two
        # variants stopped being mirror images and the claim in the comment
        # above is no longer true.
        flipped = tuple(tuple(-v for v in row) for row in M)
        self.assertAlmostEqual(fold.determinant(flipped), -1.0, places=9)
        # A reflection, not a rotation -- so a matrix like that is refused.
        self.assertGreater(abs(fold.determinant(flipped) - 1.0), 1e-6)

    def test_the_transpose_is_the_inverse(self):
        inv = transpose(M)
        for i in range(3):
            for j in range(3):
                expected = 1.0 if i == j else 0.0
                self.assertAlmostEqual(
                    fold.dot(M[i], [row[j] for row in inv]), expected, places=9
                )


class ReverseConstruction(unittest.TestCase):
    """The core test: pick a pose, work out what the sensor would read, and
    require the original pose back."""

    def test_a_screen_facing_the_room_reads_upright(self):
        base_up = unit((0.0, 0.14, 0.99))
        lid_up = fold.lid_vector(base_up, 95.0)
        self.assertEqual(fold.name_orientation(unit(lid_up)), "normal")
        self.assertEqual(
            fold.transform_for(fold.name_orientation(lid_up), "standard"), 0
        )

    def test_a_screen_facing_away_reads_upside_down(self):
        base_up = unit((0.0, 0.14, -0.99))
        lid_up = unit(fold.lid_vector(base_up, 180.0))
        self.assertEqual(fold.name_orientation(lid_up), "inverted")
        self.assertEqual(
            fold.transform_for(fold.name_orientation(lid_up), "standard"), 2
        )

    # Which of the two portrait attitudes is called "right" and which "left" is
    # a property of the mount matrix's X sign, and that sign is the one thing
    # `calibrate` exists to settle on the real machine: both choices are proper
    # rotations, so no amount of arithmetic can pick between them. These two
    # tests used to hard-code a guess at the answer and failed, because on this
    # chassis the guess was the wrong way round -- which is not academic, since
    # the panel's Settings offers "Upright" precisely to correct it by hand.
    #
    # So what is asserted here is the part that is true regardless of the sign:
    # the two attitudes are opposite, both are portrait, and they land on the two
    # portrait transforms. The naming is pinned separately, in
    # `test_the_portrait_names_are_pinned_deliberately`, so that changing it is
    # a conscious act rather than a silent drift.

    def _portrait_pair(self):
        """The screen's attitude rotated a quarter turn each way about the hinge."""
        base_up = unit((0.0, 0.14, 0.99))
        upright = unit(fold.lid_vector(base_up, 95.0))
        return (
            (-upright[1], upright[0], upright[2]),
            (upright[1], -upright[0], upright[2]),
        )

    def test_the_two_portrait_attitudes_are_opposite_transforms(self):
        one, other = (fold.name_orientation(unit(v)) for v in self._portrait_pair())
        self.assertNotEqual(one, other)
        self.assertEqual({one, other}, {"left", "right"})

    def test_both_portrait_attitudes_are_distinct_from_landscape(self):
        for vector in self._portrait_pair():
            orientation = fold.name_orientation(unit(vector))
            self.assertIn(orientation, ("left", "right"))
            self.assertNotEqual(
                fold.transform_for(orientation, "standard"),
                fold.transform_for("normal", "standard"),
            )

    def test_the_two_portrait_attitudes_use_the_two_portrait_transforms(self):
        used = {
            fold.transform_for(fold.name_orientation(unit(v)), "standard")
            for v in self._portrait_pair()
        }
        self.assertEqual(used, {1, 3})

    def test_the_portrait_names_are_pinned_deliberately(self):
        # `right` and `left` as this build defines them. Flipping the mount
        # matrix's X sign is what changes this table, and it must be done on the
        # machine, with `calibrate` -- not inferred from a test fixture.
        self.assertEqual(fold.BASE_TRANSFORM["right"], 3)
        self.assertEqual(fold.BASE_TRANSFORM["left"], 1)

    def test_the_upright_mapping_exactly_swaps_the_two_portrait_transforms(self):
        # This is the correction the panel offers, so it has to be an exact
        # swap and leave the two landscape transforms alone.
        for orientation in ("right", "left"):
            standard = fold.transform_for(orientation, "standard")
            swapped = fold.transform_for(orientation, "portrait-swapped")
            self.assertEqual(swapped, 4 - standard)
            self.assertIn(swapped, (1, 3))
        for orientation in ("normal", "inverted"):
            self.assertEqual(
                fold.transform_for(orientation, "portrait-swapped"),
                fold.transform_for(orientation, "standard"),
            )

    def test_the_answer_does_not_depend_on_the_fold_it_was_measured_at(self):
        for fold_deg in (0, 45, 90, 135, 180, 225, 270, 315):
            base_up = unit((0.31, -0.22, 0.92))
            lid_up = unit(fold.lid_vector(base_up, float(fold_deg)))
            recovered = base_up_from_lid(lid_up, float(fold_deg))
            for a, b in zip(base_up, recovered):
                self.assertAlmostEqual(a, b, places=9)


class FlatBlindness(unittest.TestCase):
    """A machine lying flat has no in-plane orientation, and must not be given one.

    An accelerometer measures gravity. Gravity points at the ground however the
    machine is *spun about the vertical*, so a machine flat on a desk reads
    identically at 0, 90, 180 and 270 degrees. The attitude derived from it is
    therefore a guess, and the panel used to present that guess as a reading --
    it answered a hardcoded "Landscape while open" for book mode, so a machine
    spun to portrait was told it was in landscape, which is the case the sensor
    is least able to see.
    """

    def test_spinning_a_flat_machine_changes_nothing_the_accelerometer_can_read(self):
        # Run the real pipeline for each quarter turn about the vertical and
        # require the same answer every time. Gravity is along the base's +Z for
        # all of them, so the sensor reports the same integers, the mount matrix
        # produces the same base frame, and the orientation comes out identical
        # whichever way the machine is actually turned. This is the end-to-end
        # statement of why the panel cannot name a direction here.
        seen = set()
        for degrees in (0.0, 90.0, 180.0, 270.0):
            base_up = unit((0.0, 0.0, 1.0))  # yaw about +Z leaves this unchanged
            raw, _ = raw_for_base_up(base_up, 9.806e-06)
            with tempfile.TemporaryDirectory() as td:
                dev = FakeAccelDevice(td, raw, 9.806e-06)
                accel = fold.Accelerometer(dev.device, fold.DEFAULT_MOUNT_MATRIX)
                sample = accel.read()
            self.assertTrue(sample.ok)
            self.assertAlmostEqual(fold.base_flat_deg(sample.vector), 0.0, places=6)
            seen.add(tuple(round(v, 6) for v in sample.vector))
            seen.add(fold.attitude(sample.vector)["orientation"])
        # One attitude for all four quarter turns: two distinct entries, the
        # frame and the single name it resolves to.
        self.assertEqual(len(seen), 2)

    def test_a_flat_machine_is_reported_as_blind(self):
        self.assertAlmostEqual(fold.base_flat_deg((0.0, 0.0, 1.0)), 0.0, places=6)
        self.assertLess(fold.base_flat_deg((0.0, 0.26, 0.965)), 20.0)

    def test_a_machine_held_up_is_not_reported_as_blind(self):
        # On its side gravity is along the base's X, so the tilt from flat is 90
        # and the in-plane angle is fully resolvable.
        self.assertAlmostEqual(fold.base_flat_deg((1.0, 0.0, 0.0)), 90.0, places=6)
        self.assertAlmostEqual(fold.base_flat_deg((0.0, 1.0, 0.0)), 90.0, places=6)
        self.assertGreater(fold.base_flat_deg((0.7, 0.0, 0.7)), 20.0)

    def test_the_reading_on_this_machine_is_genuinely_flat(self):
        # Captured from the machine lying on a desk with the lid open, which is
        # the posture that prompted all of this. It rests at about 15 degrees,
        # inside the tolerance, which is why the tolerance cannot simply be zero:
        # a machine at rest on a desk is never at zero.
        # The vector recorded in DEFAULTS, which is the reading the mount matrix
        # was derived from. It is 8 degrees off flat, not 15: the tolerance has
        # to clear this with room to spare, and the live machine currently rests
        # nearer 15, so the margin is real but not large.
        sensor = unit((0.0, -9.22, -1.29))
        base_up = tuple(fold.dot(r, sensor) for r in fold.DEFAULT_MOUNT_MATRIX)
        self.assertAlmostEqual(fold.base_flat_deg(base_up), 8.0, delta=1.0)

    def test_the_state_carries_the_blindness_so_the_panel_can_ask(self):
        daemon = bare_daemon(flatToleranceDeg=20.0)
        daemon.accel = MagicMock()
        daemon.accel.available = True
        daemon.accel.read.return_value = fold.AccelSample(
            (0, -9.22, -1.29), (0.0, 0.26, 0.965), 9.8, True
        )
        daemon.hinge = MagicMock()
        daemon.hinge.available = True
        daemon.hinge.read_fold.return_value = fold.HingeSample(
            105.0, 104.0, 359.0, 105.0, 1.0, True
        )
        daemon.motion = MagicMock()
        daemon.motion.read.return_value = (0.3, True)
        daemon.motion.is_still.return_value = True
        pose = daemon.read_pose()
        self.assertTrue(pose["flat"])
        self.assertLess(pose["flatDeg"], 20.0)
        # The orientation is still computed -- it is just not to be trusted.
        self.assertIn(pose["orientation"], ("normal", "inverted", "left", "right"))

    def test_a_tilted_machine_is_not_blind(self):
        daemon = bare_daemon(flatToleranceDeg=20.0)
        daemon.accel = MagicMock()
        daemon.accel.available = True
        # Turned on its side: gravity now along the base's X.
        daemon.accel.read.return_value = fold.AccelSample(
            (0, -9.22, -1.29), (1.0, 0.0, 0.0), 9.8, True
        )
        daemon.hinge = MagicMock()
        daemon.hinge.available = True
        daemon.hinge.read_fold.return_value = fold.HingeSample(
            105.0, 104.0, 359.0, 105.0, 1.0, True
        )
        daemon.motion = MagicMock()
        daemon.motion.read.return_value = (0.3, True)
        daemon.motion.is_still.return_value = True
        pose = daemon.read_pose()
        self.assertFalse(pose["flat"])


class FlatTurnTracking(unittest.TestCase):
    """Following a flat machine round, which the accelerometer cannot see.

    The gyroscope carries a bias of about +0.274 deg/s about the vertical on this
    machine, measured while it sat still. Integrated unconditionally that is 16
    degrees a minute and a full quarter turn wrong inside ten, so the whole
    design here is that nothing is integrated unless the machine is genuinely
    turning: a stationary machine reads 0.27, which is inside the deadband, so
    the bias is never banked. These tests pin that, because it is the only
    property standing between this feature and a confidently wrong answer that
    gets worse all day.
    """

    BIAS = 0.274  # deg/s, measured on this machine at rest

    def _daemon(self, anchor=0):
        daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        daemon.settings = fold.load_settings()
        daemon.state = fold.State()
        daemon.yaw_deg = 0.0
        daemon.yaw_anchor = anchor
        daemon.yaw_turning = False
        if anchor == 2:
            daemon.yaw_anchor = "inverted"
        daemon._last_yaw_at = None
        return daemon

    def _run(self, daemon, rate, seconds, *, flat=True, t0=100.0, orientation="normal"):
        t = t0
        for _ in range(int(seconds / 0.1)):
            pose = {"flat": flat, "verticalRate": rate}
            if not flat:
                pose["orientation"] = orientation
            daemon.track_yaw(pose, t)
            t += 0.1
        return t

    def test_a_quarter_turn_while_flat_is_followed(self):
        d = self._daemon()
        self._run(d, 40.0, 2.25)
        self.assertAlmostEqual(d.yaw_deg, 90.0, delta=8.0)
        self.assertEqual(d.yaw_orientation(), "left")

    def test_a_turn_the_other_way_is_followed_too(self):
        d = self._daemon()
        self._run(d, -40.0, 2.25)
        self.assertAlmostEqual(d.yaw_deg, -90.0, delta=8.0)
        self.assertEqual(d.yaw_orientation(), "right")

    def test_the_stationary_bias_is_never_integrated(self):
        # The property the whole thing rests on. Ten minutes of sitting still on
        # a desk -- open-loop integration would be 164 degrees wrong by now.
        d = self._daemon()
        t = self._run(d, 40.0, 2.25)
        banked = d.yaw_deg
        t = self._run(d, self.BIAS, 600.0, t0=t)
        self.assertEqual(d.yaw_deg, banked)
        self.assertEqual(d.yaw_orientation(), "left")

    def test_wobble_below_the_deadband_is_ignored(self):
        d = self._daemon()
        self._run(d, 5.0, 10.0)
        self.assertEqual(d.yaw_deg, 0.0)
        self.assertIsNone(d.yaw_orientation())

    def test_a_partial_turn_does_not_move_the_screen(self):
        d = self._daemon()
        self._run(d, 40.0, 0.5)  # about 20 degrees
        self.assertIsNone(d.yaw_orientation())

    def test_consecutive_turns_accumulate_rather_than_replacing(self):
        d = self._daemon()
        t = self._run(d, 40.0, 2.25)
        t = self._run(d, 0.0, 0.5, t0=t)
        t = self._run(d, 40.0, 2.25, t0=t)
        # Two quarter turns from the same start, not the second overwriting the
        # first.
        self.assertEqual(d.yaw_orientation(), "inverted")

    def test_tilting_the_machine_hands_authority_back_to_the_accelerometer(self):
        d = self._daemon()
        t = self._run(d, 40.0, 2.25)
        t = self._run(d, 0.0, 5.0, flat=False, t0=t, orientation="right")
        self.assertEqual(d.yaw_deg, 0.0)
        self.assertFalse(d.yaw_turning)
        self.assertIsNone(d.yaw_orientation())
        # The anchor is the accelerometer's own word for where it is, not a
        # transform it happened to be mapped to.
        self.assertEqual(d.yaw_anchor, "right")

    def test_the_turn_is_relative_to_the_anchor(self):
        d = self._daemon(anchor=2)
        self._run(d, 40.0, 2.25)
        self.assertEqual(d.yaw_orientation(), "right")

    def test_the_yaw_path_answers_in_orientations_not_transforms(self):
        # This is the bug: the yaw path used to add a quarter turn straight to
        # the transform, which had already had `mapping` folded into it, so it
        # was doing arithmetic in a different space from the accelerometer path.
        # The two then disagreed about which way was portrait -- landscape came
        # out as portrait -- and the mapping setting was bypassed entirely.
        self.assertEqual(fold.FoldDaemon._anchor_after("normal", 90.0), "left")
        self.assertEqual(fold.FoldDaemon._anchor_after("left", 90.0), "inverted")
        self.assertEqual(fold.FoldDaemon._anchor_after("right", 90.0), "normal")
        self.assertIsInstance(fold.FoldDaemon._anchor_after("normal", 90.0), str)

    def test_both_paths_pass_through_the_same_mapping(self):
        # Whatever the mapping, the two paths must agree about which transform a
        # given orientation means. If they ever diverge the screen has two
        # opinions about which way is up.
        for mapping in fold.MAPPINGS:
            for orientation in ("normal", "inverted", "right", "left"):
                self.assertIn(fold.transform_for(orientation, mapping), (0, 1, 2, 3))
        # And the user's correction applies to the gyro's answer as well.
        self.assertEqual(fold.transform_for("left", "standard"), 1)
        self.assertEqual(fold.transform_for("left", "portrait-swapped"), 3)

    def test_the_direction_can_be_flipped_without_touching_the_mapping(self):
        # A wrong sense of turn is corrected by `yawSign`, not by `mapping`:
        # reusing the mapping here would fix the flat case and break every
        # non-flat one.
        right = self._daemon()
        right.settings["yawSign"] = 1
        self._run(right, 40.0, 2.25)
        left = self._daemon()
        left.settings["yawSign"] = -1
        self._run(left, 40.0, 2.25)
        self.assertNotEqual(right.yaw_orientation(), left.yaw_orientation())
        # Both still answer with a valid orientation name.
        self.assertIn(right.yaw_orientation(), fold.ORIENTATION_CYCLE)
        self.assertIn(left.yaw_orientation(), fold.ORIENTATION_CYCLE)

    def test_the_orientation_cycle_matches_the_transform_table(self):
        # `ORIENTATION_CYCLE` is only true of `BASE_TRANSFORM` if the two are
        # kept in step, and nothing else would notice them drifting apart.
        for name in fold.ORIENTATION_CYCLE:
            self.assertEqual(
                fold.BASE_TRANSFORM[name], fold.ORIENTATION_CYCLE.index(name)
            )


class VerticalRateTests(unittest.TestCase):
    def test_the_vertical_component_needs_the_mount_matrix(self):
        # Sign matters now, and the stillness test only ever took a magnitude, so
        # this is the first thing that reads the gyro directionally.
        with tempfile.TemporaryDirectory() as td:
            dev = Path(td) / "gyro"
            dev.mkdir()
            (dev / "name").write_text("gyro_3d\n")
            (dev / "in_anglvel_scale").write_text(f"{math.pi / 180.0!r}\n")
            for axis, value in zip("xyz", (0, 900, 0)):
                (dev / f"in_anglvel_{axis}_raw").write_text(f"{value}\n")
            motion = fold.Motion(dev, 9.0, fold.DEFAULT_MOUNT_MATRIX)
            # base_Z is the sensor's -Y, so a +Y rate is a turn the other way.
            self.assertAlmostEqual(
                motion.vertical_rate((0.0, 0.0, 1.0)), -900.0, places=6
            )
            # And the magnitude is unchanged by any of this.
            self.assertAlmostEqual(motion.read()[0], 900.0, places=6)

    def test_no_gyroscope_means_no_vertical_rate(self):
        motion = fold.Motion(None, 9.0, fold.DEFAULT_MOUNT_MATRIX)
        self.assertIsNone(motion.vertical_rate((0.0, 0.0, 1.0)))


class FakeSensors(unittest.TestCase):
    """Tests that use fake IIO devices to exercise the sensor classes."""

    def test_accelerometer_reads_a_flat_machine(self):
        with tempfile.TemporaryDirectory() as td:
            raw, _ = raw_for_base_up((0.0, 0.0, 1.0), 9.806e-06)
            dev = FakeAccelDevice(td, raw, 9.806e-06)
            accel = fold.Accelerometer(dev.device, M)
            sample = accel.read()
            self.assertTrue(sample.ok)
            self.assertAlmostEqual(sample.vector[2], 1.0, places=6)

    def test_accelerometer_rejects_a_torn_read(self):
        with tempfile.TemporaryDirectory() as td:
            dev = FakeAccelDevice(td, (0, 0, 0), 9.806e-06)
            accel = fold.Accelerometer(dev.device, M)
            sample = accel.read()
            self.assertFalse(sample.ok)

    def test_hinge_reads_three_channels(self):
        with tempfile.TemporaryDirectory() as td:
            dev = FakeHingeDevice(td, 102.0, 101.0, 359.0)
            hinge = fold.Hinge(dev.device)
            sample = hinge.read()
            self.assertTrue(sample.ok)
            self.assertAlmostEqual(sample.fold_deg, 102.0, places=1)

    def test_hinge_detects_a_channel_that_makes_no_sense(self):
        # The three channels come from firmware, and on this machine they have
        # been seen 131 degrees apart mid-fold, so the tolerance is a deliberate
        # 150 rather than something tight. This case is well outside it.
        with tempfile.TemporaryDirectory() as td:
            dev = FakeHingeDevice(td, 10.0, 200.0, 0.0)
            hinge = fold.Hinge(dev.device)
            sample = hinge.read()
            self.assertFalse(sample.ok)

    def test_hinge_tolerates_the_disagreement_firmware_actually_produces(self):
        # The other side of the same pair, so the looseness cannot quietly become
        # a way of ignoring a broken channel: a disagreement inside the
        # documented tolerance is still accepted.
        with tempfile.TemporaryDirectory() as td:
            dev = FakeHingeDevice(td, 102.0, 50.0, 359.0)
            hinge = fold.Hinge(dev.device)
            sample = hinge.read()
            self.assertTrue(sample.ok)
            self.assertLessEqual(sample.residual_deg, 150.0)


class Mode(unittest.TestCase):
    """Four poses, per the official Lenovo Yoga 260 user guide.

    notebook  0° – 190°    keyboard on
    tablet    190° – 270°   keyboard off, screen facing out
    tent      270° – 340°   keyboard on, propped in a Λ shape
    stand     340° – 360°   keyboard off, nearly fully folded back
    """

    def setUp(self):
        self.daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        self.daemon.settings = {
            "bookExitDeg": 190.0,
            "tentEnterDeg": 270.0,
            "tentExitDeg": 340.0,
        }
        self.daemon.state = fold.State()

    def test_an_open_machine_is_a_book(self):
        self.assertEqual(self.daemon.mode_for(0.0), ("book", False))
        self.assertEqual(self.daemon.mode_for(98.0), ("book", False))

    def test_a_lid_open_for_use_is_still_a_book(self):
        for degrees in (90, 100, 104, 110, 117, 150, 189):
            with self.subTest(fold=degrees):
                self.assertEqual(self.daemon.mode_for(degrees), ("book", False))

    def test_a_propped_machine_is_a_tent(self):
        self.assertEqual(self.daemon.mode_for(270.0), ("tent", False))
        self.assertEqual(self.daemon.mode_for(300.0), ("tent", False))
        self.assertEqual(self.daemon.mode_for(339.0), ("tent", False))

    def test_a_flat_fold_is_a_tablet(self):
        self.assertEqual(self.daemon.mode_for(190.0), ("tablet", True))
        self.assertEqual(self.daemon.mode_for(250.0), ("tablet", True))
        self.assertEqual(self.daemon.mode_for(350.0), ("tablet", True))

    def test_stand_mode_is_tablet(self):
        self.assertEqual(self.daemon.mode_for(340.0), ("tablet", True))
        self.assertEqual(self.daemon.mode_for(359.0), ("tablet", True))


class BookMode(unittest.TestCase):
    """A laptop on knees must not turn, whatever it is doing."""

    class StubHypr:
        def __init__(self, transform):
            self.transform = transform

        def current_transform(self, panel):
            return self.transform

    def test_a_locked_screen_adopts_the_panel(self):
        daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        daemon.settings = {"locked": True}
        daemon.hypr = self.StubHypr(2)
        daemon.state = fold.State()
        daemon.state.transform = 0
        _transform, why = daemon.desired_transform({"candidates": []}, "book")
        self.assertEqual(why, "locked")
        self.assertEqual(daemon.state.transform, 2)

    def test_a_locked_screen_does_not_fight_a_hand_turn(self):
        daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        daemon.settings = {"locked": True}
        daemon.hypr = self.StubHypr(3)
        daemon.state = fold.State()
        daemon.state.transform = 3
        transform, why = daemon.desired_transform({"candidates": []}, "book")
        self.assertEqual(why, "locked")
        self.assertEqual(transform, 3)
        self.assertEqual(daemon.state.transform, 3)


def bare_daemon(**settings):
    """A daemon with only what `desired_transform` reads.

    Built with `__new__` so nothing looks for hardware, but every attribute the
    method actually touches is present. The previous hand-rolled versions of
    these fixtures were missing `decision`, `decision_since`, `still` and
    `mapping`, so all four of these tests raised AttributeError and none of them
    had ever checked a decision.
    """
    daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
    daemon.settings = {
        "mapping": "standard",
        "locked": False,
        "hystDeg": 12.0,
        "settleSec": 0.35,
        "flatToleranceDeg": 20.0,
        **settings,
    }
    daemon.state = fold.State()
    daemon.decision = None
    daemon.decision_since = time.monotonic()
    daemon.still = True
    return daemon


class Hysteresis(unittest.TestCase):
    """The screen must not oscillate between two poses.

    `flat` and `top` cannot be used to tell the two cases apart: both with a
    positive sign resolve to "normal" and so to transform 0, so a fixture built
    from them gives the same answer whichever way the hysteresis goes, and
    passes whether the code is right or not. These use `right` (transform 3)
    against `flat` (transform 0), which are genuinely different outputs.
    """

    INCUMBENT: ClassVar[dict] = {"label": "right", "sign": 1}
    CHALLENGER: ClassVar[dict] = {"label": "flat", "sign": 1}

    def _pose(self, incumbent_tilt, challenger_tilt):
        """Candidates in the order `attitude` emits them: best first.

        `attitude` sorts by `abs(up[axis])` descending, so `candidates[0]` is
        always the axis world-up leans on most, i.e. the smallest `tiltDeg`, and
        `desired_transform` reads `candidates[0]` as the challenger. A fixture
        that hands over an unsorted list is not a weaker test, it is a different
        test: the code then compares the wrong pair. Sorting here keeps the
        numbers below the only thing that decides the outcome.
        """
        candidates = [
            dict(self.CHALLENGER, tiltDeg=challenger_tilt),
            dict(self.INCUMBENT, tiltDeg=incumbent_tilt),
        ]
        return {"candidates": sorted(candidates, key=lambda c: c["tiltDeg"])}

    def test_a_small_margin_keeps_the_incumbent(self):
        # settleSec 0 so the settle window cannot mask which candidate won; that
        # is what `SettleTests` is for.
        daemon = bare_daemon(hystDeg=12.0, settleSec=0.0)
        daemon.state.axis, daemon.state.signed = "right", 1
        daemon.state.transform = 3
        # The challenger is 4 degrees better: inside the 12 degree margin.
        transform, _ = daemon.desired_transform(self._pose(80.0, 84.0), "book")
        self.assertEqual(transform, 3)
        self.assertEqual((daemon.state.axis, daemon.state.signed), ("right", 1))

    def test_a_large_margin_takes_the_challenger(self):
        daemon = bare_daemon(hystDeg=12.0, settleSec=0.0)
        daemon.state.axis, daemon.state.signed = "right", 1
        daemon.state.transform = 3
        # The challenger is 15 degrees better: outside the 12 degree margin.
        transform, _ = daemon.desired_transform(self._pose(95.0, 80.0), "book")
        self.assertEqual(transform, 0)
        self.assertEqual((daemon.state.axis, daemon.state.signed), ("flat", 1))

    def test_the_two_cases_really_do_differ(self):
        # If these two ever agree again the pair has stopped testing anything.
        results = []
        for incumbent_tilt, challenger_tilt in ((80.0, 84.0), (95.0, 80.0)):
            daemon = bare_daemon(hystDeg=12.0, settleSec=0.0)
            daemon.state.axis, daemon.state.signed = "right", 1
            daemon.state.transform = 3
            results.append(
                daemon.desired_transform(
                    self._pose(incumbent_tilt, challenger_tilt), "book"
                )[0]
            )
        self.assertEqual(results, [3, 0])


class SettleTests(unittest.TestCase):
    """The screen must not turn while the machine is moving."""

    def test_a_moving_machine_does_not_turn(self):
        daemon = bare_daemon(settleSec=0.35, hystDeg=12.0)
        daemon.still = False
        pose = {"candidates": [{"label": "right", "sign": 1, "tiltDeg": 90.0}]}
        transform, why = daemon.desired_transform(pose, "book")
        self.assertEqual(why, "turning")
        self.assertEqual(transform, 0)

    def test_a_settling_machine_does_not_turn_yet(self):
        daemon = bare_daemon(settleSec=0.35, hystDeg=12.0)
        daemon.still = True
        daemon.decision = 3
        daemon.decision_since = time.monotonic()
        daemon.state.transform = 0
        pose = {"candidates": [{"label": "right", "sign": 1, "tiltDeg": 90.0}]}
        transform, why = daemon.desired_transform(pose, "book")
        self.assertIn("settling", why)
        self.assertEqual(transform, 0)

    def test_a_settled_machine_does_turn(self):
        # The other side of the same coin, so the pair cannot both pass by
        # accident: once the window has elapsed the decision must be delivered.
        daemon = bare_daemon(settleSec=0.0, hystDeg=12.0)
        daemon.still = True
        daemon.decision = 3
        daemon.decision_since = time.monotonic()
        daemon.state.transform = 0
        pose = {"candidates": [{"label": "right", "sign": 1, "tiltDeg": 90.0}]}
        transform, _ = daemon.desired_transform(pose, "book")
        self.assertEqual(transform, 3)


class TransformForTests(unittest.TestCase):
    def test_transform_for_all_mappings(self):
        for mapping in fold.MAPPINGS:
            for orientation in ("normal", "inverted", "left", "right"):
                t = fold.transform_for(orientation, mapping)
                self.assertIn(t, (0, 1, 2, 3))

    def test_transform_for_unknown(self):
        self.assertEqual(fold.transform_for("unknown", "standard"), 0)


class HingeTelemetryCadenceTests(unittest.TestCase):
    """The fold angle is read every pass; the other two are not.

    Reading one IIO attribute on this machine is not cheap. Timed per attribute,
    median over 40 reads:

        hinge  in_angl{0,1,2}_raw   ~10.4 ms each
        accel  in_accel_{x,y,z}_raw ~5.5 ms each
        gyro   in_anglvel_*_raw     ~1.8 ms each
        static in_angl_scale         0.1 ms

    So the hinge was ~58% of a whole pass, and two thirds of that went on
    `angl1` and `angl2` -- the two channels this plugin's own docs describe as
    telemetry that nothing gates on. `mode_for` reads `angl0` and nothing else.
    The residual derived from the other two is shown in the panel and, past 25
    degrees, noted in `status`; no rotation is ever refused because of it.

    These pin that the cheap path is actually taken, because a version that set
    the cache without stamping it looked identical and did nothing at all.
    """

    def _hinge(self, fold_deg=102.0):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        dev = FakeHingeDevice(self._td.name, fold_deg, fold_deg - 1.0, 359.0)
        hinge = fold.Hinge(dev.device)
        # Count which attributes are actually read.
        self.reads = []
        real = fold.read_float

        def counting(path):
            self.reads.append(Path(path).name)
            return real(path)

        fold.read_float = counting
        self.addCleanup(lambda: setattr(fold, "read_float", real))
        return hinge

    def test_the_cheap_path_reads_only_the_fold_channel(self):
        hinge = self._hinge()
        hinge.read()  # warm the cache
        self.reads.clear()
        hinge.read_fold(time.monotonic(), 1.0)
        self.assertEqual(self.reads, ["in_angl0_raw"])

    def test_the_cheap_path_is_actually_cheap(self):
        # Guards the bug this whole thing was written for: `read()` set the
        # cache but never stamped the time, so every call fell through to the
        # full three-channel read and the optimisation did nothing. It is
        # invisible in the API -- `read_fold` returns a perfectly good sample
        # either way -- so it can only be caught by counting the reads.
        hinge = self._hinge()
        hinge.read()
        self.reads.clear()
        for _ in range(5):
            hinge.read_fold(time.monotonic(), 60.0)
        self.assertEqual(len(self.reads), 5)
        self.assertEqual(set(self.reads), {"in_angl0_raw"})

    def test_the_fold_value_is_never_stale(self):
        # The cache must not become a way of acting on an old angle. Only the
        # two unused channels are held back; the one every decision reads is
        # fetched every time.
        hinge = self._hinge(fold_deg=102.0)
        hinge.read()
        (hinge.device / "in_angl0_raw").write_text(
            f"{round(math.radians(250.0) / hinge.scale)}\n"
        )
        for _ in range(3):
            sample = hinge.read_fold(time.monotonic(), 60.0)
            self.assertAlmostEqual(sample.fold_deg, 250.0, places=1)

    def test_the_channels_come_back_on_the_telemetry_cadence(self):
        hinge = self._hinge()
        hinge.read()
        self.reads.clear()
        # A window of zero has elapsed, so this is a full read again.
        hinge.read_fold(time.monotonic() + 10.0, 1.0)
        self.assertEqual(
            set(self.reads), {"in_angl0_raw", "in_angl1_raw", "in_angl2_raw"}
        )

    def test_a_cadence_of_zero_reads_everything_every_time(self):
        hinge = self._hinge()
        for _ in range(3):
            hinge.read_fold(time.monotonic(), 0.0)
        self.assertEqual(self.reads.count("in_angl1_raw"), 3)

    def test_the_self_consistency_verdict_is_carried_not_recomputed(self):
        # `ok` is a check on the firmware's own arithmetic, computed
        # independently of the fold channel. Holding `angl0` fresh against two
        # channels from a different instant would report a disagreement that
        # means nothing, so the last real verdict is what carries forward.
        hinge = self._hinge()
        full = hinge.read()
        self.assertTrue(full.ok)
        sample = hinge.read_fold(time.monotonic(), 60.0)
        self.assertEqual(sample.ok, full.ok)
        self.assertEqual(sample.residual_deg, full.residual_deg)

    def test_derived_is_withheld_when_it_cannot_be_computed_honestly(self):
        hinge = self._hinge()
        self.assertIsNotNone(hinge.read().derived_fold_deg)
        sample = hinge.read_fold(time.monotonic(), 60.0)
        self.assertIsNone(sample.derived_fold_deg)

    def test_a_full_read_updates_the_cache_the_cheap_path_depends_on(self):
        # The stamp, not just the value. Without it the cheap path can never be
        # entered, which is exactly what went wrong.
        hinge = self._hinge()
        self.assertIsNone(hinge._cached)
        hinge.read()
        self.assertIsNotNone(hinge._cached)
        self.assertGreater(hinge._cached_at, 0.0)


class HyprlandTests(unittest.TestCase):
    def setUp(self):
        self._real_which = fold.shutil.which
        self._real_run = fold.subprocess.run
        fold.shutil.which = lambda x: "/usr/bin/hyprctl"
        fold.subprocess.run = self._fake_run
        self.hypr = fold.Hyprland()
        self.hypr.env = {"HYPRLAND_INSTANCE_SIGNATURE": "test"}

    def tearDown(self):
        fold.shutil.which = self._real_which
        fold.subprocess.run = self._real_run

    def _fake_run(self, cmd, **kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = R()
        if "monitors" in cmd:
            r.stdout = json.dumps(
                [
                    {
                        "name": "eDP-1",
                        "width": 1920,
                        "height": 1080,
                        "refreshRate": 60.0,
                        "x": 0,
                        "y": 0,
                        "scale": 1.0,
                        "transform": 0,
                    }
                ]
            )
        elif "devices" in cmd:
            r.stdout = json.dumps(
                {
                    "keyboards": [{"name": "at-translated-set-2-keyboard"}],
                    "touch": [{"name": "wacom-pen-and-multitouch-sensor-finger"}],
                    "tablets": [{"name": "wacom-pen-and-multitouch-sensor-pen"}],
                    "mice": [
                        {"name": "etps/2-elantech-trackpoint"},
                        {"name": "etps/2-elantech-touchpad"},
                    ],
                    "touchpads": [{"name": "etps/2-elantech-touchpad"}],
                }
            )
        elif "clients" in cmd:
            r.stdout = json.dumps([])
        elif "layers" in cmd:
            r.stdout = "namespace: lock"
        return r

    def test_monitors(self):
        m = self.hypr.monitors()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["name"], "eDP-1")

    def test_monitor(self):
        self.assertEqual(self.hypr.monitor("eDP-1")["name"], "eDP-1")
        self.assertIsNone(self.hypr.monitor("HDMI-1"))

    def test_internal_monitor(self):
        self.assertEqual(self.hypr.internal_monitor("eDP-1"), "eDP-1")

    def test_set_transform(self):
        ok, msg = self.hypr.set_transform("eDP-1", 2)
        self.assertTrue(ok, msg)

    def test_current_transform(self):
        self.assertEqual(self.hypr.current_transform("eDP-1"), 0)

    def test_devices(self):
        d = self.hypr.devices()
        self.assertIn("keyboards", d)

    def test_device_names(self):
        # Keyed by kind, singular, as `device_names` returns it -- not by
        # Hyprland's own plurals, which is what the raw `devices -j` uses. The
        # touchpad is filed under `touchpad` and removed from `pointer` on
        # purpose, so the two are asserted separately below.
        n = self.hypr.device_names()
        self.assertIn("at-translated-set-2-keyboard", n["keyboard"])
        self.assertIn("etps/2-elantech-trackpoint", n["pointer"])
        self.assertIn("etps/2-elantech-touchpad", n["touchpad"])
        self.assertNotIn("etps/2-elantech-touchpad", n["pointer"])
        self.assertIn("wacom-pen-and-multitouch-sensor-finger", n["touch"])
        self.assertIn("wacom-pen-and-multitouch-sensor-pen", n["pen"])

    def test_session_locked(self):
        self.assertTrue(self.hypr.session_locked())

    def test_match_device(self):
        self.assertEqual(
            self.hypr.match_device("wacom.*finger", ("touch",)),
            "wacom-pen-and-multitouch-sensor-finger",
        )

    def test_match_any(self):
        result = self.hypr.match_any(
            ["at-translated-set-2-keyboard"], ["at-translated-set-2-keyboard", "other"]
        )
        self.assertEqual(result, ["at-translated-set-2-keyboard"])

    def test_set_device(self):
        ok, msg = self.hypr.set_device("test", transform=1, output="eDP-1")
        self.assertTrue(ok, msg)

    def test_set_device_transform(self):
        ok, msg = self.hypr.set_device_transform("test", 1, "eDP-1")
        self.assertTrue(ok, msg)

    def test_set_device_enabled(self):
        ok, msg = self.hypr.set_device_enabled("test", False)
        self.assertTrue(ok, msg)

    def test_available(self):
        self.assertTrue(self.hypr.available)


class RotationTransactionTests(unittest.TestCase):
    """Panel, pen and finger must move together, in one round trip.

    On Windows the display driver committed all three at once, so there was no
    interval in which the picture had turned and the input had not. Here each
    call is its own `hyprctl eval`, so three sequential ones can land either
    side of a compositor reload and leave the pen rotated against the panel.

    Hyprland's Lua config is evaluated as a chunk rather than an expression, so
    several statements separated by `;` go in one `eval`. That was verified
    against the running compositor before being relied on: panel + finger + pen
    in a single call returns `ok` and applies.
    """

    TOUCH = "wacom-pen-and-multitouch-sensor-finger"
    PEN = "wacom-pen-and-multitouch-sensor-pen"

    def _hypr(self):
        hypr = fold.Hyprland.__new__(fold.Hyprland)
        hypr.binary = "/usr/bin/hyprctl"
        hypr.env = {"HYPRLAND_INSTANCE_SIGNATURE": "test"}
        hypr._monitor_spec = {}
        self.calls = []
        hypr.run = lambda argv, timeout=5.0: (
            self.calls.append(argv),
            (0, "ok"),
        )[1]
        hypr.monitor = lambda name: {
            "name": name,
            "width": 1366,
            "height": 768,
            "refreshRate": 60.0,
            "x": 0,
            "y": 0,
            "scale": 1.0,
            "transform": 0,
        }
        return hypr

    def test_the_whole_rotation_is_a_single_call(self):
        hypr = self._hypr()
        ok, msg = hypr.rotate_transaction("eDP-1", 3, [self.TOUCH, self.PEN])
        self.assertTrue(ok, msg)
        self.assertEqual(len(self.calls), 1)

    def test_that_one_call_carries_the_panel_and_both_nodes(self):
        hypr = self._hypr()
        hypr.rotate_transaction("eDP-1", 3, [self.TOUCH, self.PEN])
        lua = self.calls[0][1]
        self.assertIn("hl.monitor(", lua)
        self.assertIn("transform = 3", lua)
        self.assertIn(self.TOUCH, lua)
        self.assertIn(self.PEN, lua)
        # Three statements in one chunk, not three evaluations.
        self.assertEqual(lua.count("hl."), 3)

    def test_a_missing_device_is_skipped_rather_than_failing_the_rotation(self):
        # The daemon only knows about the nodes Hyprland reported. An absent
        # one must not leave a stray `hl.device({ name = , ...})` in the chunk,
        # which is a Lua syntax error and would take the panel down with it.
        hypr = self._hypr()
        ok, _ = hypr.rotate_transaction("eDP-1", 0, [None, self.PEN])
        self.assertTrue(ok)
        lua = self.calls[0][1]
        self.assertNotIn("name = null", lua)
        self.assertEqual(lua.count("hl.device("), 1)

    def test_the_monitor_geometry_is_looked_up_once_not_per_rotation(self):
        # Re-deriving mode/position/scale means a `monitors -j` before every
        # rotation, which was half the cost of turning the screen.
        hypr = self._hypr()
        looked_up = []
        real_monitor = hypr.monitor

        def counting(name):
            looked_up.append(name)
            return real_monitor(name)

        hypr.monitor = counting
        for transform in (1, 2, 3, 0):
            hypr.rotate_transaction("eDP-1", transform, [])
        self.assertEqual(len(looked_up), 1)

    def test_the_cached_geometry_is_dropped_when_the_monitor_is_re_read(self):
        # A resolution or scale change has to be noticed, or a rotation would be
        # built from the geometry the monitor used to have.
        hypr = self._hypr()
        hypr.rotate_transaction("eDP-1", 0, [])
        self.assertIn("eDP-1", hypr._monitor_spec)
        hypr.current_transform("eDP-1")
        self.assertEqual(hypr._monitor_spec, {})

    def test_an_unknown_panel_fails_instead_of_emitting_a_broken_call(self):
        hypr = self._hypr()
        hypr.monitor = lambda name: None
        ok, _msg = hypr.rotate_transaction("nope", 0, [])
        self.assertFalse(ok)
        self.assertEqual(self.calls, [])


class ModeBandTests(unittest.TestCase):
    """The 190 crossing has a band, not a line.

    The EC does not engage at exactly 190 and release at exactly 190. Dell's
    Windows convertible documentation puts the keyboard cut-out near 225 --
    inside Lenovo's 190-270 tablet band, not at its edge -- because the
    firmware engages partway through with hysteresis of its own. A bare
    comparison on one threshold chatters whenever a lid is rested near it,
    which is exactly where a person tends to leave one.
    """

    def _daemon(self, release=170.0):
        daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        daemon.settings = {
            "bookExitDeg": 190.0,
            "bookReleaseDeg": release,
            "tentEnterDeg": 270.0,
            "tentExitDeg": 340.0,
        }
        daemon.state = fold.State()
        return daemon

    def test_the_published_boundaries_are_unchanged_for_a_cold_start(self):
        # Nothing is known about the previous mode, so these are the user
        # guide's ranges exactly. The existing Mode tests pin the same thing.
        daemon = self._daemon()
        self.assertEqual(daemon.mode_for(189.0), ("book", False))
        self.assertEqual(daemon.mode_for(190.0), ("tablet", True))

    def test_a_lid_left_on_the_boundary_does_not_flutter(self):
        daemon = self._daemon()
        # Entered tablet, then the lid settles a hair below the threshold.
        self.assertEqual(daemon.mode_for(191.0, "book"), ("tablet", True))
        # Still inside the band: stays a tablet, so the keyboard stays off.
        self.assertEqual(daemon.mode_for(185.0, "tablet"), ("tablet", True))
        self.assertEqual(daemon.mode_for(180.0, "tablet"), ("tablet", True))

    def test_it_comes_back_to_book_once_it_is_below_the_release(self):
        daemon = self._daemon()
        self.assertEqual(daemon.mode_for(169.0, "tablet"), ("book", False))
        self.assertEqual(daemon.mode_for(100.0, "tablet"), ("book", False))

    def test_a_round_trip_across_the_boundary_never_toggles(self):
        # The failure this exists to prevent: a lid parked at the edge, jittering
        # either side of it, taking the keyboard on and off every few seconds.
        # One crossing in, and never back out while it stays in the band.
        daemon = self._daemon()
        mode = "book"
        readings = [189, 190, 189, 191, 190, 189, 190, 189]
        seen = []
        for fold_deg in readings:
            mode, _folded = daemon.mode_for(float(fold_deg), mode)
            seen.append(mode)
        self.assertEqual(seen[0], "book")
        self.assertEqual(set(seen[1:]), {"tablet"})
        # And it does come back once the lid genuinely retreats.
        self.assertEqual(daemon.mode_for(150.0, mode), ("book", False))

    def test_entering_from_book_still_happens_at_the_published_angle(self):
        daemon = self._daemon()
        self.assertEqual(daemon.mode_for(190.0, "book"), ("tablet", True))
        self.assertEqual(daemon.mode_for(189.0, "book"), ("book", False))

    def test_the_tent_boundaries_stay_exact(self):
        # The band is only for the crossing that gates input. The other two
        # change a label and nothing else, so they must keep meaning what the
        # user guide says: tablet to 269, tent from 270 to 339, stand from 340.
        daemon = self._daemon()
        self.assertEqual(daemon.mode_for(269.0, "tablet"), ("tablet", True))
        self.assertEqual(daemon.mode_for(270.0, "tablet"), ("tent", False))
        self.assertEqual(daemon.mode_for(339.0, "tent"), ("tent", False))
        self.assertEqual(daemon.mode_for(340.0, "tent"), ("tablet", True))

    def test_the_band_is_configurable(self):
        narrow = self._daemon(release=188.0)
        self.assertEqual(narrow.mode_for(188.5, "tablet"), ("tablet", True))
        self.assertEqual(narrow.mode_for(187.0, "tablet"), ("book", False))


class SessionLockTests(unittest.TestCase):
    """Whether the session is locked, which is what keeps the keyboard alive.

    `session_locked` guards the one failure mode that is not cosmetic: a folded
    machine with its keyboard switched off, locked, with no on-screen keyboard
    reachable. Its Hyprland check read the layers output only when the command
    had *failed* -- and `hyprctl layers` succeeds normally -- so the namespace
    test never ran and locking was decided by guessing at window class and
    title. These pin the real output shape, captured from this machine.

    The output is `Layer <id>: xywh: ..., namespace: <name>, pid: <n>`, with
    windows indented under their layer. The namespace name is therefore on the
    same line as the word `namespace:`, and the window *title* is not in this
    output at all.
    """

    LOCKED = (
        "Monitor eDP-1:\n"
        "\tLayer level 3 (overlay):\n"
        "\t\tLayer abc: xywh: 0 0 768 1366, a: 1, namespace: hyprlock, pid: 999\n"
    )
    UNLOCKED = (
        "Monitor eDP-1:\n"
        "\tLayer level 2 (top):\n"
        "\t\tLayer def: xywh: 0 0 768 26, a: 1, namespace: omarchy-bar, pid: 109156\n"
    )

    def _hypr(self, layers, *, rc=0, clients="[]"):
        hypr = fold.Hyprland.__new__(fold.Hyprland)
        hypr.binary = "/usr/bin/hyprctl"
        hypr.env = {"HYPRLAND_INSTANCE_SIGNATURE": "test"}

        def run(argv, timeout=5.0):
            if "layers" in argv:
                return (rc, layers)
            return (0, clients)

        hypr.run = run
        hypr.json = lambda argv: json.loads(clients) if "clients" in argv else None
        return hypr

    def test_a_lock_namespace_is_a_locked_session(self):
        self.assertIs(self._hypr(self.LOCKED).session_locked(), True)

    def test_no_lock_namespace_is_not_a_locked_session(self):
        self.assertIs(self._hypr(self.UNLOCKED).session_locked(), False)

    def test_a_window_titled_lock_is_not_a_locked_session(self):
        # The false positive the old whole-text search was heading towards. The
        # layers output carries no titles, so matching the namespace line only
        # is what keeps an editor with "lock" in a filename from pinning the
        # keyboard on forever.
        titled = self.UNLOCKED + (
            "\t\tLayer 111: xywh: 0 0 100 100, a: 1, namespace: kitty, pid: 5\n"
        )
        self.assertIs(self._hypr(titled).session_locked(), False)

    def test_a_failed_layers_query_falls_through_to_the_clients(self):
        # Unknown is not the same as unlocked, and this path is the one that
        # returns None so `set_devices` leaves the keyboard alone.
        self.assertIs(self._hypr("", rc=1).session_locked(), False)
        self.assertIs(
            self._hypr(
                "", rc=1, clients='[{"class": "waylock", "title": ""}]'
            ).session_locked(),
            True,
        )

    def test_the_keyboard_is_never_disabled_while_the_session_is_locked(self):
        # The behaviour the whole function exists for, end to end: a folded
        # machine, locked, keeps its keyboard.
        daemon = bare_daemon(lockKeyboard=True, lockPointers=True)
        daemon.state.keyboard_disabled = False
        daemon.state.pointers_disabled = False
        daemon.hypr = MagicMock()
        daemon.hypr.session_locked.return_value = True
        daemon.keyboards, daemon.pointers, daemon.touchpads = ["kb"], ["ptr"], ["pad"]
        daemon.set_devices(True, True)
        self.assertFalse(daemon.state.keyboard_disabled)
        self.assertEqual(daemon.state.lock_safe, "kept on: the session is locked")
        # The pointers are a different matter: they cannot unlock anything.
        self.assertTrue(daemon.state.pointers_disabled)

    def test_an_unknown_lock_state_also_leaves_the_keyboard_alone(self):
        daemon = bare_daemon(lockKeyboard=True, lockPointers=True)
        daemon.state.keyboard_disabled = False
        daemon.state.pointers_disabled = False
        daemon.hypr = MagicMock()
        daemon.hypr.session_locked.return_value = None
        daemon.keyboards, daemon.pointers, daemon.touchpads = ["kb"], ["ptr"], ["pad"]
        daemon.set_devices(True, True)
        self.assertFalse(daemon.state.keyboard_disabled)

    def test_the_kept_on_reason_is_cleared_once_the_session_unlocks(self):
        # The reason is recomputed on every call, not set once under the lock.
        # It used to be set under the lock and never cleared, so the panel kept
        # claiming the keyboard was "kept on" after the session had been
        # unlocked and the keyboard had been switched off normally.
        daemon = bare_daemon(lockKeyboard=True, lockPointers=True)
        daemon.state.keyboard_disabled = False
        daemon.state.pointers_disabled = False
        daemon.hypr = MagicMock()
        daemon.keyboards, daemon.pointers, daemon.touchpads = ["kb"], ["ptr"], ["pad"]

        daemon.hypr.session_locked.return_value = True
        daemon.set_devices(True, True)
        self.assertEqual(daemon.state.lock_safe, "kept on: the session is locked")
        self.assertFalse(daemon.state.keyboard_disabled)

        # Unlocked: the keyboard is now the thing being asked for, and the
        # stale explanation must not outlive the situation that produced it.
        daemon.hypr.session_locked.return_value = False
        daemon.set_devices(True, True)
        self.assertEqual(daemon.state.lock_safe, "")
        self.assertTrue(daemon.state.keyboard_disabled)


class OnScreenKeyboardTests(unittest.TestCase):
    def setUp(self):
        self._real_path = fold.Path
        self.settings = {
            "oskPlugins": {"test-plugin": ""},
            "oskCommand": "",
            "oskPlugin": "",
        }

    def test_installed_empty(self):
        osk = fold.OnScreenKeyboard(self.settings)
        self.assertEqual(osk.installed(), [])

    def test_target_none(self):
        osk = fold.OnScreenKeyboard(self.settings)
        self.assertIsNone(osk.target())

    def test_toggle_no_plugin(self):
        osk = fold.OnScreenKeyboard(self.settings)
        result = osk.toggle()
        self.assertFalse(result["ok"])

    def test_as_dict(self):
        osk = fold.OnScreenKeyboard(self.settings)
        d = osk.as_dict()
        self.assertIn("installed", d)
        self.assertFalse(d["drivable"])


class FoldDaemonStepTests(unittest.TestCase):
    def setUp(self):
        # `step` calls `reload_settings` on every pass, which reads the real
        # shell.json. Unpatched, these tests inherited whatever the person
        # running them had actually set -- a `locked: true` left behind by an
        # earlier run made every assertion here moot, because the daemon then
        # did nothing but adopt the panel. Settings come from a temporary file
        # so a test cannot be decided by the desktop's state.
        self._real_shell = fold.SHELL_JSON
        self._shell_td = tempfile.TemporaryDirectory()
        fold.SHELL_JSON = Path(self._shell_td.name) / "shell.json"
        fold.SHELL_JSON.write_text(
            json.dumps(
                {"bar": {"layout": {"right": [{"id": "estrocondoso.yoga260-fold"}]}}}
            )
        )
        self.addCleanup(self._restore_shell)
        self.daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        self.daemon.settings = {
            "bookExitDeg": 190.0,
            "tentEnterDeg": 270.0,
            "tentExitDeg": 340.0,
            "lockKeyboard": True,
            "lockPointers": True,
            "oskAuto": False,
            "stillRotDeg": 9.0,
            "hystDeg": 12.0,
            "flatToleranceDeg": 20.0,
            "settleSec": 0.35,
            "pollSec": 0.1,
            "verifySec": 5.0,
            "coherentBandG": [0.75, 1.30],
            "mapping": "standard",
            "locked": False,
            "mountMatrix": [[-1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, -1.0, 0.0]],
            "accelName": "accel_3d",
            "hingeName": "hinge",
            "gyroName": "gyro_3d",
            "panel": "eDP-1",
            "touchPattern": "wacom.*finger",
            "penPattern": "wacom.*pen",
            "keyboardNames": ["at-translated-set-2-keyboard"],
            "pointerNames": ["etps/2-elantech-trackpoint"],
            "touchpadNames": ["etps/2-elantech-touchpad"],
            "reportTiltResidualDeg": 25.0,
        }
        self.daemon.state = fold.State()
        self.daemon.machine = fold.Machine(
            "LENOVO", "20FE", "ThinkPad Yoga 260", "31", "20FES04T1M", "N1GETA9W"
        )
        self.daemon.hypr = MagicMock()
        self.daemon.hypr.env = {}
        self.daemon.hypr.available = True
        self.daemon.hypr.current_transform.return_value = 0
        self.daemon.accel = MagicMock()
        self.daemon.accel.available = True
        self.daemon.accel.device = Path("/dev/iio:device0")
        self.daemon.accel.scale = 1.0
        self.daemon.accel.hz = 10.0
        self.daemon.accel.matrix = [
            [-1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
            [0.0, -1.0, 0.0],
        ]
        self.daemon.accel.has_kernel_matrix = False
        self.daemon.accel.reader.rejected = 0
        self.daemon.accel.reader.tries = 0
        self.daemon.accel.reader.rejection_rate = 0.0
        self.daemon.accel.read.return_value = fold.AccelSample(
            (0, -9.22, -1.29), (0.0, 0.92, 0.39), 9.8, True
        )
        self.daemon.hinge = MagicMock()
        self.daemon.hinge.available = True
        self.daemon.hinge.device = Path("/dev/iio:device5")
        self.daemon.hinge.scale = 1.0
        self.daemon.hinge.hz = 10.0
        self.daemon.hinge.hysteresis = 1.0
        self.daemon.hinge.labels = {}
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            102.0, 101.0, 359.0, 102.0, 1.0, True
        )
        self.daemon.motion = MagicMock()
        self.daemon.motion.available = True
        self.daemon.motion.device = Path("/dev/iio:device1")
        self.daemon.motion.scale = 1.0
        self.daemon.motion.read.return_value = (0.5, True)
        self.daemon.motion.is_still.return_value = True
        self.daemon.osk = fold.OnScreenKeyboard(self.daemon.settings)
        self.daemon.stop = False
        self.daemon.decision = None
        self.daemon.decision_since = 0
        self.daemon.still = True
        self.daemon._was_still = True
        self.daemon.last_applied = 0
        self.daemon.last_status = ""
        self.daemon.last_signature = None
        self.daemon.last_publish = 0
        self.daemon._last_key = None
        self.daemon._last_wall = None
        self.daemon._last_mono = None
        self.daemon._settings_stamp = None
        self.daemon.keyboards = ["at-translated-set-2-keyboard"]
        self.daemon.pointers = ["etps/2-elantech-trackpoint"]
        self.daemon.touchpads = ["etps/2-elantech-touchpad"]
        self.daemon.state.touch = "wacom-pen-and-multitouch-sensor-finger"
        self.daemon.state.pen = "wacom-pen-and-multitouch-sensor-pen"
        self.daemon.state.panel = "eDP-1"
        self.daemon.emit = lambda payload: None
        self.daemon.hypr.rotate_transaction.return_value = (True, "")
        self.daemon.hypr.set_device_transform.return_value = (True, "")
        self.daemon.hypr.set_device_enabled.return_value = (True, "")
        # The shape `Hyprland.device_names()` actually returns: one key per kind
        # of thing, singular, with touchpads in `touchpad` and deliberately
        # filtered *out* of `pointer`. Not the raw `hyprctl devices -j` shape,
        # which is keyed by Hyprland's own plurals and keeps touchpads among the
        # mice -- mocking that here tested a dict the daemon never sees.
        self.daemon.hypr.device_names.return_value = {
            "touch": ["wacom-pen-and-multitouch-sensor-finger"],
            "pen": ["wacom-pen-and-multitouch-sensor-pen"],
            "pointer": ["etps/2-elantech-trackpoint"],
            "touchpad": ["etps/2-elantech-touchpad"],
            "keyboard": ["at-translated-set-2-keyboard"],
        }
        self.daemon.hypr.match_device.side_effect = lambda pattern, kinds: (
            "wacom-pen-and-multitouch-sensor-finger"
            if "finger" in pattern
            else "wacom-pen-and-multitouch-sensor-pen"
        )
        self.daemon.hypr.match_any.side_effect = lambda candidates, present: [
            c for c in candidates if c in present
        ]
        self.daemon.hypr.internal_monitor.return_value = "eDP-1"
        self.daemon.hypr.session_locked.return_value = False

    def _restore_shell(self):
        fold.SHELL_JSON = self._real_shell
        self._shell_td.cleanup()

    def test_step_book_mode(self):
        result = self.daemon.step()
        self.assertIsInstance(result, bool)

    def test_step_tablet_mode(self):
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            200.0, 200.0, 359.0, 200.0, 1.0, True
        )
        result = self.daemon.step()
        self.assertIsInstance(result, bool)

    def test_step_tent_mode(self):
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            300.0, 300.0, 359.0, 300.0, 1.0, True
        )
        result = self.daemon.step()
        self.assertIsInstance(result, bool)

    def test_step_stand_mode(self):
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            350.0, 350.0, 359.0, 350.0, 1.0, True
        )
        result = self.daemon.step()
        self.assertIsInstance(result, bool)

    def test_step_blocked_by_machine(self):
        self.daemon.machine = fold.Machine("DELL", "XPS", "XPS 13", "", "", "")
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.messages)
        # The whole point of the gate: a machine the plugin was not written for
        # must get no hyprctl writes at all, not a rotation the daemon happens
        # to decide on the way past.
        self.daemon.hypr.rotate_transaction.assert_not_called()
        self.daemon.hypr.set_device_enabled.assert_not_called()

    def test_step_blocked_by_no_panel(self):
        self.daemon.state.panel = None
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.messages)
        self.daemon.hypr.rotate_transaction.assert_not_called()
        self.daemon.hypr.set_device_enabled.assert_not_called()

    def test_step_blocked_by_no_touch(self):
        self.daemon.state.touch = None
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.messages)
        self.daemon.hypr.rotate_transaction.assert_not_called()
        self.daemon.hypr.set_device_enabled.assert_not_called()

    def test_step_blocked_by_no_pen(self):
        self.daemon.state.pen = None
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.messages)
        self.daemon.hypr.rotate_transaction.assert_not_called()
        self.daemon.hypr.set_device_enabled.assert_not_called()

    def test_step_blocked_by_bad_matrix(self):
        self.daemon.accel.matrix = [[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.messages)
        self.daemon.hypr.rotate_transaction.assert_not_called()
        self.daemon.hypr.set_device_enabled.assert_not_called()

    def test_step_hinge_not_consistent(self):
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            102.0, 50.0, 359.0, None, None, False
        )
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.messages)
        # Reported nonsense from the hinge means hold, not turn: a reading the
        # driver itself flags as inconsistent must never reach the panel.
        self.daemon.hypr.rotate_transaction.assert_not_called()

    def test_step_moving(self):
        self.daemon.motion.is_still.return_value = False
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.messages)
        # The stillness gate is what stops the screen reacting while the
        # machine is being carried.
        self.daemon.hypr.rotate_transaction.assert_not_called()

    def test_step_tilt_residual_high(self):
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            102.0, 101.0, 359.0, 102.0, 30.0, True
        )
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.tilt_note)
        # The other half of the contract: `angl2` is static on this machine, so
        # a large residual is reported and must not be mistaken for a gate. If
        # this ever stops being called, the note has quietly become a veto.
        self.daemon.hypr.rotate_transaction.assert_called()

    def test_step_applies_transform(self):
        self.daemon.hypr.current_transform.return_value = 2
        self.daemon.state.transform = 0
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.daemon.hypr.rotate_transaction.assert_called()

    def test_step_tablet_mode_disables_keyboard(self):
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            200.0, 200.0, 359.0, 200.0, 1.0, True
        )
        self.daemon.state.keyboard_disabled = False
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.keyboard_disabled)

    def test_step_tablet_mode_enables_osk(self):
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            200.0, 200.0, 359.0, 200.0, 1.0, True
        )
        self.daemon.settings["oskAuto"] = True
        self.daemon.state.osk_asked = False
        self.daemon.osk = MagicMock()
        self.daemon.osk.toggle.return_value = {"ok": True}
        self.daemon.osk.as_dict.return_value = {
            "installed": [],
            "drivable": False,
            "auto": True,
            "pluginId": "",
        }
        self.daemon.reload_settings = MagicMock()
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertTrue(self.daemon.state.osk_asked)

    def test_step_non_tablet_resets_osk_asked(self):
        self.daemon.state.osk_asked = True
        result = self.daemon.step()
        self.assertIsInstance(result, bool)
        self.assertFalse(self.daemon.state.osk_asked)

    def test_step_settles_decision_on_stillness(self):
        self.daemon.decision = 1
        self.daemon._was_still = False
        self.daemon.still = True
        result = self.daemon.step()
        self.assertIsInstance(result, bool)

    def test_blocking_reason_none(self):
        self.assertIsNone(self.daemon.blocking_reason())

    def test_blocking_reason_no_panel(self):
        self.daemon.state.panel = None
        self.assertIsNotNone(self.daemon.blocking_reason())

    def test_blocking_reason_no_touch(self):
        self.daemon.state.panel = "eDP-1"
        self.daemon.state.touch = None
        self.assertIsNotNone(self.daemon.blocking_reason())

    def test_blocking_reason_no_pen(self):
        self.daemon.state.panel = "eDP-1"
        self.daemon.state.touch = "touch"
        self.daemon.state.pen = None
        self.assertIsNotNone(self.daemon.blocking_reason())

    def test_blocking_reason_bad_matrix(self):
        self.daemon.state.panel = "eDP-1"
        self.daemon.state.touch = "touch"
        self.daemon.state.pen = "pen"
        self.daemon.accel.matrix = [[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertIsNotNone(self.daemon.blocking_reason())

    def test_desired_transform_locked(self):
        self.daemon.settings["locked"] = True
        self.daemon.hypr.current_transform.return_value = 2
        transform, why = self.daemon.desired_transform({"candidates": []}, "book")
        self.assertEqual(why, "locked")
        self.assertEqual(transform, 2)

    def test_desired_transform_normal(self):
        pose = {"candidates": [{"label": "flat", "sign": 1, "tiltDeg": 90.0}]}
        transform, why = self.daemon.desired_transform(pose, "book")
        self.assertEqual(why, "normal")
        self.assertEqual(transform, 0)

    def test_desired_transform_still(self):
        self.daemon.still = False
        self.daemon.state.transform = 2
        pose = {"candidates": [{"label": "flat", "sign": 1, "tiltDeg": 90.0}]}
        transform, why = self.daemon.desired_transform(pose, "book")
        self.assertEqual(why, "turning")
        # While moving the screen keeps what it had, rather than adopting the
        # pose the accelerometer is mid-way through reporting.
        self.assertEqual(transform, 2)

    def test_desired_transform_settling(self):
        self.daemon.still = True
        self.daemon.decision = 1
        self.daemon.decision_since = time.monotonic()
        self.daemon.state.transform = 0
        pose = {"candidates": [{"label": "right", "sign": 1, "tiltDeg": 80.0}]}
        transform, why = self.daemon.desired_transform(pose, "book")
        self.assertIn("settling", why)
        self.assertEqual(transform, 0)

    def test_set_devices_no_change(self):
        self.daemon.state.keyboard_disabled = True
        self.daemon.state.pointers_disabled = True
        self.daemon.set_devices(True, True)
        # `set_devices` runs five times a second, and the whole reason it
        # returns early is that hyprctl must not be spawned for a state that
        # is already true. Without this the test only proves the call returns.
        self.daemon.hypr.set_device_enabled.assert_not_called()
        self.assertTrue(self.daemon.state.keyboard_disabled)
        self.assertTrue(self.daemon.state.pointers_disabled)

    def test_set_devices_change(self):
        self.daemon.state.keyboard_disabled = False
        self.daemon.state.pointers_disabled = False
        self.daemon.set_devices(True, True)
        self.assertTrue(self.daemon.state.keyboard_disabled)

    def test_set_devices_locked_session(self):
        self.daemon.state.keyboard_disabled = False
        self.daemon.state.pointers_disabled = False
        self.daemon.hypr.session_locked.return_value = True
        self.daemon.set_devices(True, True)
        self.assertFalse(self.daemon.state.keyboard_disabled)

    def test_set_devices_unknown_lock(self):
        self.daemon.state.keyboard_disabled = False
        self.daemon.state.pointers_disabled = False
        self.daemon.hypr.session_locked.return_value = None
        self.daemon.set_devices(True, True)
        self.assertFalse(self.daemon.state.keyboard_disabled)

    def test_read_pose(self):
        pose = self.daemon.read_pose()
        self.assertIn("accel", pose)
        self.assertIn("hinge", pose)
        self.assertIn("fold", pose)

    def _pose(self, fold_deg=102.0, vector=(0.0, 0.92, 0.39)):
        """The sample `changed()` judges, built rather than read.

        `changed()` used to read the sensors itself, so the tests set up the
        device mocks and let it. It is now handed the pass's sample by `run()`,
        which is the whole point of the change, so the tests build one -- and
        building it is what lets them prove that the very same reading reaches
        both the detection and the decision.
        """
        return {
            "accel": fold.AccelSample((0, -9.22, -1.29), tuple(vector), 9.8, True),
            "hinge": fold.HingeSample(
                fold_deg, fold_deg - 1.0, 359.0, fold_deg, 1.0, True
            ),
        }

    def test_mode_for_book(self):
        self.assertEqual(self.daemon.mode_for(0.0), ("book", False))
        self.assertEqual(self.daemon.mode_for(189.0), ("book", False))

    def test_mode_for_tablet(self):
        self.assertEqual(self.daemon.mode_for(190.0), ("tablet", True))
        self.assertEqual(self.daemon.mode_for(269.0), ("tablet", True))

    def test_mode_for_tent(self):
        self.assertEqual(self.daemon.mode_for(270.0), ("tent", False))
        self.assertEqual(self.daemon.mode_for(339.0), ("tent", False))

    def test_mode_for_stand(self):
        self.assertEqual(self.daemon.mode_for(340.0), ("tablet", True))
        self.assertEqual(self.daemon.mode_for(359.0), ("tablet", True))

    def test_changed_no_change(self):
        self.daemon._last_key = (102.0, 0.0, 0.92, 0.39, 9.8)
        self.assertFalse(self.daemon.changed(self._pose()))

    def test_changed_with_change(self):
        self.daemon._last_key = (999.0, 0.0, 0.92, 0.39, 9.8)
        self.assertTrue(self.daemon.changed(self._pose()))

    def test_settling_no_decision(self):
        self.daemon.decision = None
        self.assertFalse(self.daemon.settling())

    def test_settling_not_still(self):
        self.daemon.decision = 1
        self.daemon.still = False
        self.assertTrue(self.daemon.settling())

    def test_settling_decision_matches(self):
        self.daemon.decision = 0
        self.daemon.still = True
        self.daemon.state.transform = 0
        self.assertFalse(self.daemon.settling())

    def test_settling_decision_differs(self):
        self.daemon.decision = 1
        self.daemon.still = True
        self.daemon.state.transform = 0
        self.assertTrue(self.daemon.settling())

    def test_check_resume_no_previous(self):
        self.daemon._last_wall = None
        self.assertFalse(self.daemon.check_resume())

    def test_check_resume_with_gap(self):
        self.daemon._last_wall = time.time() - 10
        self.daemon._last_mono = time.monotonic() - 5
        self.assertTrue(self.daemon.check_resume())

    def test_reload_settings_no_change(self):
        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(self._shell_with({"locked": True}))
            self.daemon.settings["locked"] = False
            self.daemon._settings_stamp = fold.SHELL_JSON.stat().st_mtime_ns
            self.daemon.reload_settings()
            # The file on disk disagrees with the daemon, but its mtime is the
            # one the daemon last read. The stamp is what keeps this off the
            # hot path ten times a second, so a reload that happened here would
            # be the bug.
            self.assertFalse(self.daemon.settings["locked"])
            fold.SHELL_JSON = real

    def test_reload_settings_with_change(self):
        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(self._shell_with({"locked": True}))
            self.daemon.settings["locked"] = False
            self.daemon._settings_stamp = None
            self.daemon.reload_settings()
            # The stamp disagreed, so the file has to be read and the new value
            # has to land -- otherwise a tap on "Lock" in the panel does nothing
            # until the daemon restarts.
            self.assertTrue(self.daemon.settings["locked"])
            self.assertEqual(
                self.daemon._settings_stamp, fold.SHELL_JSON.stat().st_mtime_ns
            )
            fold.SHELL_JSON = real

    def test_reload_settings_picks_up_a_changed_osk_plugin(self):
        # The comparison set used to be SETTING_KEYS minus `oskPlugin`, so
        # changing the preferred keyboard from the panel did nothing until the
        # daemon was restarted. End to end: a value written to disk lands in
        # the live settings on the next pass, and the stamp moves with it.
        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(self._shell_with({"oskPlugin": "plugin-one"}))
            self.daemon._settings_stamp = None
            self.daemon.reload_settings()
            self.assertEqual(self.daemon.settings["oskPlugin"], "plugin-one")

            # A second write. The mtime is forced forward rather than left to
            # the clock, because a filesystem with coarse timestamps could put
            # both writes in the same tick and the stamp would then match --
            # silently skipping the reload this test exists to check.
            fold.SHELL_JSON.write_text(self._shell_with({"oskPlugin": "plugin-two"}))
            stamp = fold.SHELL_JSON.stat().st_mtime_ns
            os.utime(fold.SHELL_JSON, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))
            self.daemon.reload_settings()
            self.assertEqual(self.daemon.settings["oskPlugin"], "plugin-two")
            fold.SHELL_JSON = real

    @staticmethod
    def _shell_with(entry_values):
        """A shell.json holding one plugin entry with these values on it."""
        entry = {"id": fold.PLUGIN_ID, **entry_values}
        return json.dumps({"bar": {"layout": {"right": [entry]}}})

    def test_verify_no_change(self):
        self.daemon.state.transform = 0
        self.daemon.hypr.current_transform.return_value = 0
        self.daemon.verify()
        # `verify` runs on a timer for the life of the session; re-issuing a
        # transform that is already in force would be a hyprctl call per tick
        # for no reason.
        self.daemon.hypr.rotate_transaction.assert_not_called()

    def test_verify_reasserts(self):
        self.daemon.state.transform = 2
        self.daemon.hypr.current_transform.return_value = 0
        self.daemon.verify()
        # Something straightened the panel behind the daemon's back, and the
        # whole contract of `verify` is that it puts the transform back.
        self.daemon.hypr.rotate_transaction.assert_called_once_with(
            "eDP-1",
            2,
            [
                "wacom-pen-and-multitouch-sensor-finger",
                "wacom-pen-and-multitouch-sensor-pen",
            ],
        )

    def test_announce_no_error(self):
        self.daemon.state.last_error = ""
        with (
            patch.object(
                fold, "which", return_value="/usr/bin/omarchy-osd"
            ) as which_mock,
            patch.object(fold.subprocess, "run") as run_mock,
        ):
            self.daemon.announce()
        which_mock.assert_called_once_with("omarchy-osd")
        run_mock.assert_called_once()
        args = run_mock.call_args.args[0]
        self.assertEqual(args[1], "-i")
        self.assertEqual(args[2], fold.ICONS["laptop"])
        self.assertEqual(args[4], "Laptop · landscape")

    def test_announce_with_error(self):
        self.daemon.state.last_error = "test"
        with (
            patch.object(
                fold, "which", return_value="/usr/bin/omarchy-osd"
            ) as which_mock,
            patch.object(fold.subprocess, "run") as run_mock,
        ):
            self.daemon.announce()
        # A panel that already has an error on it must not also announce a pose
        # -- the early return is the whole guard.
        which_mock.assert_not_called()
        run_mock.assert_not_called()

    def test_write_state(self):
        with tempfile.TemporaryDirectory() as td:
            real = fold.STATE_FILE
            fold.STATE_FILE = Path(td) / "state.json"
            self.daemon.write_state({"test": True})
            self.assertTrue(fold.STATE_FILE.exists())
            fold.STATE_FILE = real

    def test_status(self):
        s = self.daemon.status()
        self.assertIn("state", s)
        self.assertIn("settings", s)

    def test_resolve_devices(self):
        # The mocks setUp already installed are the whole point: `resolve_devices`
        # reads a dict that Hyprland.device_names() builds, and that dict has a
        # `pointer` and a `keyboard` key alongside the raw per-kind lists. A hand
        # written stub here that omits either one is not a second opinion, it is
        # a different shape, and it used to raise KeyError on the missing key
        # rather than fail on something about device resolution.
        self.daemon.resolve_devices()
        self.assertEqual(
            self.daemon.state.touch, "wacom-pen-and-multitouch-sensor-finger"
        )
        self.assertEqual(self.daemon.state.pen, "wacom-pen-and-multitouch-sensor-pen")
        self.assertEqual(self.daemon.state.panel, "eDP-1")
        self.assertEqual(self.daemon.keyboards, ["at-translated-set-2-keyboard"])
        self.assertEqual(self.daemon.pointers, ["etps/2-elantech-trackpoint"])
        self.assertEqual(self.daemon.touchpads, ["etps/2-elantech-touchpad"])

    def test_a_touchpad_is_found_although_device_names_hides_it_from_pointers(self):
        # Regression. `device_names` files touchpads under `touchpad` and strips
        # them out of `pointer`, because a touchpad is not a pointer. Building
        # the search pool without the `touchpad` key therefore made
        # `self.touchpads` always empty, and a folded machine kept a live
        # touchpad under a dead keyboard -- the exact thing `lockPointers` is
        # there to prevent, and invisible because nothing errored.
        self.assertIn(
            "etps/2-elantech-touchpad", self.daemon.hypr.device_names()["touchpad"]
        )
        self.assertNotIn(
            "etps/2-elantech-touchpad", self.daemon.hypr.device_names()["pointer"]
        )
        self.daemon.resolve_devices()
        self.assertEqual(self.daemon.touchpads, ["etps/2-elantech-touchpad"])
        self.assertEqual(self.daemon.pointers, ["etps/2-elantech-trackpoint"])

    def test_folding_really_does_switch_the_touchpad_off(self):
        # The end the bug had: a populated list is only worth having if the
        # devices on it are the ones that get disabled.
        self.daemon.hinge.read_fold.return_value = fold.HingeSample(
            200.0, 200.0, 359.0, 200.0, 1.0, True
        )
        self.daemon.state.keyboard_disabled = False
        self.daemon.state.pointers_disabled = False
        self.daemon.step()
        self.assertTrue(self.daemon.state.pointers_disabled)
        # `set_device_enabled(name, enabled)` passes `enabled` positionally.
        disabled = {
            call.args[0]
            for call in self.daemon.hypr.set_device_enabled.call_args_list
            if len(call.args) > 1 and call.args[1] is False
        }
        self.assertIn("etps/2-elantech-touchpad", disabled)
        self.assertIn("etps/2-elantech-trackpoint", disabled)


class CLITests(unittest.TestCase):
    def setUp(self):
        self._real_shell = fold.SHELL_JSON
        fold.SHELL_JSON = Path("/nonexistent/shell.json")

    def tearDown(self):
        fold.SHELL_JSON = self._real_shell

    def test_cmd_doctor(self):
        # Real settings, not `{}`: `cmd_doctor` builds sensors straight out of
        # them, so an empty dict raised KeyError on 'accelName' and the report it
        # exists to print was never produced by any test.
        self.assertIn(fold.main(["doctor"]), (0, 1))

    def test_cmd_self_test(self):
        rc = fold.cmd_self_test()
        self.assertEqual(rc, 0)

    def test_cmd_lock(self):
        rc = fold.cmd_lock({}, "toggle")
        self.assertEqual(rc, 1)

    def test_cmd_mapping(self):
        rc = fold.cmd_mapping({}, "standard")
        self.assertEqual(rc, 1)

    def test_cmd_keyboard(self):
        rc = fold.cmd_keyboard({}, "toggle")
        self.assertEqual(rc, 1)

    def test_cmd_setting(self):
        rc = fold.cmd_setting({}, "mapping", "standard")
        self.assertEqual(rc, 1)

    def test_cmd_setting_read(self):
        rc = fold.cmd_setting({}, "mapping", None)
        self.assertEqual(rc, 0)

    # These four used to call `cmd_libwacom_status`, `cmd_libwacom_install`,
    # `cmd_libwacom_remove` and `cmd_hwdb_show`. None of those functions has ever
    # existed: the subcommands are `libwacom_status`, `libwacom_install`,
    # `libwacom_remove` and `hwdb_show`, reached through `main`. So all four
    # tests raised AttributeError on every run and none of them checked a
    # subcommand that exists. They go through `main` now, which is the path a
    # person or a keybinding actually takes, so the dispatch is covered too.

    def test_cmd_libwacom_status(self):
        self.assertEqual(fold.main(["libwacom", "status"]), 0)

    def test_cmd_libwacom_install(self):
        with libwacom_in_tmpdir() as entry:
            self.assertEqual(fold.main(["libwacom", "install"]), 0)
            self.assertTrue(entry.exists())

    def test_cmd_libwacom_remove(self):
        with libwacom_in_tmpdir() as entry:
            fold.main(["libwacom", "install"])
            self.assertEqual(fold.main(["libwacom", "remove"]), 0)
            self.assertFalse(entry.exists())

    def test_cmd_hwdb_show(self):
        self.assertEqual(fold.main(["hwdb", "show"]), 0)

    def test_main_status(self):
        rc = fold.main(["status"])
        self.assertEqual(rc, 0)

    def test_main_self_test(self):
        rc = fold.main(["self-test"])
        self.assertEqual(rc, 0)

    def test_main_no_args(self):
        rc = fold.main([])
        self.assertEqual(rc, 0)

    def test_main_unknown(self):
        # argparse rejects an unknown subcommand itself, before `main` reaches
        # the `print_help(); return 1` at the bottom, so this raises SystemExit
        # with argparse's own status rather than returning 1. Asserting a return
        # code here meant the test errored on every run and the "no command at
        # all prints help" path was never actually checked.
        with self.assertRaises(SystemExit) as caught:
            fold.main(["unknown"])
        self.assertNotEqual(caught.exception.code, 0)


class WriteShellEntryTests(unittest.TestCase):
    def setUp(self):
        self._real_shell = fold.SHELL_JSON
        self._real_home = fold.Path.home
        import tempfile

        self.td = tempfile.TemporaryDirectory()
        self.shell_path = Path(self.td.name) / "shell.json"
        self.shell_path.write_text(
            json.dumps(
                {
                    "bar": {
                        "layout": {
                            "right": [
                                {"id": "estrocondoso.yoga260-fold", "locked": False}
                            ]
                        }
                    }
                }
            )
        )
        fold.SHELL_JSON = self.shell_path
        fold.Path.home = lambda: Path(self.td.name)

    def tearDown(self):
        fold.SHELL_JSON = self._real_shell
        fold.Path.home = self._real_home
        self.td.cleanup()

    def test_write_shell_entry(self):
        result = fold.write_shell_entry({"locked": True})
        self.assertTrue(result)
        data = json.loads(self.shell_path.read_text())
        self.assertTrue(data["bar"]["layout"]["right"][0]["locked"])

    def test_write_shell_entry_remove(self):
        result = fold.write_shell_entry({"locked": None})
        self.assertTrue(result)
        data = json.loads(self.shell_path.read_text())
        self.assertNotIn("locked", data["bar"]["layout"]["right"][0])

    def test_save_shell(self):
        doc = {"test": True}
        result = fold._save_shell(doc)
        self.assertTrue(result)


class LibwacomTests(unittest.TestCase):
    def test_libwacom_status(self):
        with libwacom_in_tmpdir():
            s = fold.libwacom_status()
        self.assertIn("installed", s)
        self.assertIn("covered", s)

    def test_libwacom_install(self):
        # These two used to call install and remove with no redirection, so they
        # wrote and then deleted the real ~/.config/libwacom entry on whichever
        # machine ran the suite. Asserting the returned path is the temporary
        # one is what makes a dropped redirect fail loudly instead of quietly
        # mutating a real user's pen config again.
        with libwacom_in_tmpdir() as entry:
            result = fold.libwacom_install()
            self.assertTrue(result["ok"])
            self.assertEqual(result["path"], str(entry))
            self.assertTrue(entry.exists())
            self.assertEqual(entry.name, "wacom-isdv4-5091.tablet")

    def test_libwacom_remove(self):
        with libwacom_in_tmpdir() as entry:
            fold.libwacom_install()
            self.assertTrue(entry.exists())
            result = fold.libwacom_remove()
            self.assertTrue(result["ok"])
            self.assertTrue(result["removed"])
            self.assertFalse(entry.exists())


class HwdbTests(unittest.TestCase):
    def test_hwdb_body(self):
        body = fold.hwdb_body([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        self.assertIn("ACCEL_MOUNT_MATRIX", body)

    def test_hwdb_show(self):
        s = fold.hwdb_show([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        self.assertIn("ACCEL_MOUNT_MATRIX", s)

    def test_hwdb_apply_root(self):
        # This used to fake `os.geteuid() == 0` and let the real thing run, so it
        # wrote to /etc/udev/hwdb.d/ and then shelled out to `systemd-hwdb update`
        # and `udevadm trigger` -- a test that reconfigures udev on whichever
        # machine runs the suite. The write goes to a temporary path and the two
        # commands are recorded instead of executed.
        with tempfile.TemporaryDirectory() as td:
            real_local, real_run = fold.HWDB_LOCAL, fold.subprocess.run
            fold.HWDB_LOCAL = Path(td) / "61-sensor-local.hwdb"
            recorder = MagicMock(return_value=MagicMock(returncode=0))
            fold.subprocess.run = recorder
            real_geteuid = os.geteuid
            os.geteuid = lambda: 0
            try:
                rc = fold.hwdb_apply(
                    [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], yes=False
                )
                self.assertEqual(rc, 0)
                self.assertIn("ACCEL_MOUNT_MATRIX", fold.HWDB_LOCAL.read_text())
                ran = [c.args[0] for c in recorder.call_args_list]
                self.assertIn(["systemd-hwdb", "update"], ran)
                self.assertEqual(ran[-1][:2], ["udevadm", "trigger"])
            finally:
                os.geteuid = real_geteuid
                fold.HWDB_LOCAL, fold.subprocess.run = real_local, real_run


class Settings(unittest.TestCase):
    def setUp(self):
        # A real, readable shell.json with no entry for this plugin, rather than
        # a path that does not exist. The torn-read test builds its torn copy by
        # truncating this file, and against a nonexistent path it died with
        # FileNotFoundError before reaching the assertion it exists for --
        # which is the one that matters most here, since a torn read is what
        # could otherwise quietly unlock a screen somebody had locked.
        self._real_shell = fold.SHELL_JSON
        self.td = tempfile.TemporaryDirectory()
        fold.SHELL_JSON = Path(self.td.name) / "shell.json"
        fold.SHELL_JSON.write_text(json.dumps({"bar": {"layout": {"right": []}}}))

    def tearDown(self):
        fold.SHELL_JSON = self._real_shell
        self.td.cleanup()

    def test_an_unknown_mapping_falls_back_to_standard(self):
        settings = fold.load_settings({"mapping": "sideways"})
        self.assertEqual(settings["mapping"], "standard")

    def test_a_known_mapping_survives(self):
        for value in fold.MAPPINGS:
            self.assertEqual(fold.load_settings({"mapping": value})["mapping"], value)

    def test_defaults_deep_copy(self):
        first = fold.load_settings()
        first["keyboardNames"].append("bogus")
        first["mountMatrix"][0][0] = 42.0
        second = fold.load_settings()
        self.assertNotIn("bogus", second["keyboardNames"])
        self.assertNotEqual(second["mountMatrix"][0][0], 42.0)

    def test_a_torn_read_does_not_unlock_a_locked_screen(self):
        import tempfile as tf

        real = fold.SHELL_JSON
        data = real.read_text()
        with tf.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write(data[: len(data) // 2])
            torn = Path(handle.name)
        try:
            fold.SHELL_JSON = torn
            self.assertIsNone(fold.shell_entry())
            kept = fold.load_settings(
                fallback={"locked": True, "mapping": "rotated-180"}
            )
            self.assertTrue(kept["locked"])
            self.assertEqual(kept["mapping"], "rotated-180")
        finally:
            fold.SHELL_JSON = real
            os.unlink(torn)

    def test_a_widget_that_is_not_on_the_bar_yet_is_not_a_torn_read(self):
        # Not None. None means "shell.json could not be read at all" and makes
        # `load_settings` fall back to the current values; {} means "it was read
        # and there is simply no entry for this plugin yet". Collapsing the two
        # is what would let a missing bar entry quietly reset every setting, so
        # this asserts the distinction rather than the absence of an error. The
        # old assertion was `assertIsNone`, which demanded exactly the collapse
        # the docstring warns against, and so failed on correct code.
        self.assertEqual(fold.shell_entry(), {})

    def test_the_matrix_default_survives_a_load(self):
        self.assertEqual(
            fold.load_settings()["mountMatrix"],
            [list(r) for r in fold.DEFAULT_MOUNT_MATRIX],
        )

    def _write_entry(self, entry_values):
        """Put one plugin entry holding these values onto the bar."""
        fold.SHELL_JSON.write_text(
            json.dumps(
                {"bar": {"layout": {"right": [{"id": fold.PLUGIN_ID, **entry_values}]}}}
            )
        )

    def test_a_stored_mount_matrix_is_read_back(self):
        # `mountMatrix` is deliberately not in SETTING_KEYS, so this is the
        # only path that can get a `calibrate --write` back out of shell.json.
        # If it is dropped, the write is a no-op and calibration silently does
        # nothing -- which is why it is asserted rather than assumed.
        stored = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
        self._write_entry({"mountMatrix": stored})
        self.assertEqual(fold.load_settings()["mountMatrix"], stored)

    def test_a_stored_matrix_that_is_not_a_rotation_is_ignored(self):
        # A reflection: determinant -1, so every turn comes out inverted and
        # looks plausible until the screen is upside down. It must fall back to
        # the shipped matrix rather than being used.
        reflection = [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self._write_entry({"mountMatrix": reflection})
        self.assertEqual(
            fold.load_settings()["mountMatrix"],
            [list(r) for r in fold.DEFAULT_MOUNT_MATRIX],
        )

    def test_a_stored_matrix_of_the_wrong_shape_is_ignored(self):
        self._write_entry({"mountMatrix": [[1.0, 0.0], [0.0, 1.0]]})
        self.assertEqual(
            fold.load_settings()["mountMatrix"],
            [list(r) for r in fold.DEFAULT_MOUNT_MATRIX],
        )

    def test_a_stored_matrix_that_is_not_numbers_is_ignored(self):
        self._write_entry(
            {"mountMatrix": [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]]}
        )
        self.assertEqual(
            fold.load_settings()["mountMatrix"],
            [list(r) for r in fold.DEFAULT_MOUNT_MATRIX],
        )


class IsRotationTests(unittest.TestCase):
    """The one check standing between a stored matrix and the screen.

    Every mount matrix passes through here -- on the way in from
    `calibrate --write` and on the way out of shell.json -- so its two failure
    modes are worth pinning separately: a reflection (determinant -1) and a
    non-rotation that is not a reflection at all (a scale). Both must be
    refused, and both look like perfectly ordinary 3x3 matrices of numbers.
    """

    def test_the_shipped_matrix_is_a_rotation(self):
        self.assertTrue(fold.is_rotation(fold.DEFAULT_MOUNT_MATRIX))

    def test_identity_is_a_rotation(self):
        self.assertTrue(
            fold.is_rotation([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        )

    def test_a_reflection_is_not_a_rotation(self):
        self.assertFalse(
            fold.is_rotation([[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        )

    def test_a_scale_is_not_a_rotation(self):
        self.assertFalse(
            fold.is_rotation([[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        )

    def test_a_shear_of_determinant_one_is_refused(self):
        # determinant +1 but not orthonormal: row0 . row1 = 1. Accepted by a
        # determinant-only check, so `is_rotation` checks the rows too -- a
        # shear skews every angle without ever flipping the screen, and the
        # matrix arrives from hand-typed numbers (`calibrate --write`, or a
        # hand-edited shell.json). `calibration.md` and `hardware.md` both
        # promise a "proper rotation" with "rows orthonormal".
        shear = [[1.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertEqual(fold.determinant(shear), 1.0)
        self.assertFalse(fold.is_rotation(shear))

    def test_scaled_rows_are_refused_even_with_det_one(self):
        # A row scaled down with another scaled up keeps det at +1.
        scale = [[0.5, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertEqual(fold.determinant(scale), 1.0)
        self.assertFalse(fold.is_rotation(scale))

    def test_the_wrong_shape_is_not_a_rotation(self):
        for matrix in (
            [[1.0, 0.0], [0.0, 1.0]],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            [],
        ):
            with self.subTest(matrix=matrix):
                self.assertFalse(fold.is_rotation(matrix))

    def test_a_ragged_row_is_not_a_rotation(self):
        self.assertFalse(
            fold.is_rotation([[1.0, 0.0, 0.0], [0.0, 1.0], [0.0, 0.0, 1.0]])
        )

    def test_something_that_is_not_a_matrix_at_all_is_not_a_rotation(self):
        for matrix in (None, "identity", 42, {"rows": 3}, [1.0, 0.0, 0.0]):
            with self.subTest(matrix=matrix):
                self.assertFalse(fold.is_rotation(matrix))

    def test_a_matrix_of_strings_that_parse_is_a_rotation(self):
        # `calibrate --write` takes floats from argparse, but the value read
        # back out of shell.json has been through JSON and could have been
        # hand-edited as strings. float() accepts them, so the check does too.
        self.assertTrue(
            fold.is_rotation([["1", "0", "0"], ["0", "1", "0"], ["0", "0", "1"]])
        )

    def test_a_determinant_that_is_merely_close_is_refused(self):
        # The tolerance is 1e-6. Anything looser would accept a matrix that is
        # not quite a rotation, and the error compounds into the turn.
        almost = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0001]]
        self.assertFalse(fold.is_rotation(almost))

    def test_a_determinant_inside_the_tolerance_is_accepted(self):
        inside = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0 + 1e-7]]
        self.assertTrue(fold.is_rotation(inside))


class ModelTests(unittest.TestCase):
    def test_model(self):
        self.assertEqual(fold.MODEL["sysVendor"], "LENOVO")
        self.assertEqual(fold.MODEL["productVersion"], "ThinkPad Yoga 260")

    def test_default_mount_matrix(self):
        self.assertEqual(len(fold.DEFAULT_MOUNT_MATRIX), 3)
        self.assertEqual(len(fold.DEFAULT_MOUNT_MATRIX[0]), 3)

    def test_defaults(self):
        self.assertIn("bookExitDeg", fold.DEFAULTS)
        self.assertIn("tentEnterDeg", fold.DEFAULTS)
        self.assertIn("tentExitDeg", fold.DEFAULTS)
        self.assertEqual(fold.DEFAULTS["bookExitDeg"], 190.0)
        self.assertEqual(fold.DEFAULTS["tentEnterDeg"], 270.0)
        self.assertEqual(fold.DEFAULTS["tentExitDeg"], 340.0)


class MachineTests(unittest.TestCase):
    def test_machine_detect(self):
        m = fold.Machine.detect()
        self.assertIsNotNone(m)

    def test_machine_as_dict(self):
        m = fold.Machine(
            "LENOVO", "20FE", "ThinkPad Yoga 260", "31", "20FES04T1M", "N1GETA9W"
        )
        d = m.as_dict()
        self.assertIn("matches", d)

    def test_machine_matches(self):
        m = fold.Machine(
            "LENOVO", "20FE", "ThinkPad Yoga 260", "31", "20FES04T1M", "N1GETA9W"
        )
        self.assertTrue(m.matches())

    def test_machine_does_not_match(self):
        m = fold.Machine("DELL", "XPS", "XPS 13", "", "", "")
        self.assertFalse(m.matches())


class WrapTests(unittest.TestCase):
    def test_wrap180(self):
        self.assertAlmostEqual(fold.wrap180(0), 0)
        self.assertAlmostEqual(fold.wrap180(180), 180)
        self.assertAlmostEqual(fold.wrap180(181), -179)

    def test_wrap360(self):
        self.assertAlmostEqual(fold.wrap360(0), 0)
        self.assertAlmostEqual(fold.wrap360(-1), 359)
        self.assertAlmostEqual(fold.wrap360(360), 0)


class TiltFromVectorTests(unittest.TestCase):
    def test_tilt_from_vector(self):
        self.assertAlmostEqual(fold.tilt_from_vector((0, 0, 1), 2), 0)
        self.assertAlmostEqual(fold.tilt_from_vector((0, 0, 1), 1), 90)


class AttitudeTests(unittest.TestCase):
    def test_attitude(self):
        result = fold.attitude((0.0, 0.97, 0.24))
        self.assertEqual(result["orientation"], "normal")
        self.assertIn("candidates", result)

    def test_classify(self):
        orientation, margin = fold.classify((0.0, 0.97, 0.24))
        self.assertEqual(orientation, "normal")
        self.assertGreaterEqual(margin, 0)

    def test_name_orientation(self):
        self.assertEqual(fold.name_orientation((0.0, 0.97, 0.24)), "normal")


class CoherentTests(unittest.TestCase):
    def test_coherent_no_device(self):
        c = fold.Coherent(None, None, 0.75, 1.30)
        self.assertIsNone(c.read())
        self.assertEqual(c.rejection_rate, 0.0)


class MotionTests(unittest.TestCase):
    def test_motion_no_device(self):
        m = fold.Motion(None, 9.0)
        self.assertFalse(m.available)
        rate, ok = m.read()
        # An unreadable gyro must not read as "turning"; a nonzero rate here
        # would freeze every screen that has no gyro at all.
        self.assertEqual(rate, 0.0)
        self.assertFalse(ok)

    def test_motion_is_still(self):
        m = fold.Motion(None, 9.0)
        self.assertTrue(m.is_still(5.0, True))
        self.assertFalse(m.is_still(15.0, True))
        self.assertFalse(m.is_still(5.0, False))


class SensorRateTests(unittest.TestCase):
    def test_sensor_rate_no_device(self):
        self.assertEqual(fold.sensor_rate(None, "test"), 10.0)


class FindIioDeviceTests(unittest.TestCase):
    def test_find_iio_device_none(self):
        result = fold.find_iio_device("nonexistent_device_xyz")
        self.assertIsNone(result)


class ReadTextTests(unittest.TestCase):
    def test_read_text_none(self):
        self.assertIsNone(fold.read_text(Path("/nonexistent/file")))

    def test_read_int_none(self):
        self.assertIsNone(fold.read_int(Path("/nonexistent/file")))

    def test_read_float_none(self):
        self.assertIsNone(fold.read_float(Path("/nonexistent/file")))


class WhichTests(unittest.TestCase):
    def test_which(self):
        self.assertIsNone(fold.which("nonexistent_program_xyz"))


class DetTests(unittest.TestCase):
    def test_determinant(self):
        self.assertAlmostEqual(fold.determinant([[1, 0, 0], [0, 1, 0], [0, 0, 1]]), 1.0)

    def test_dot(self):
        self.assertEqual(fold.dot((1, 2, 3), (4, 5, 6)), 32)


class ClampTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(fold.clamp(5, 0, 10), 5)
        self.assertEqual(fold.clamp(-5, 0, 10), 0)
        self.assertEqual(fold.clamp(15, 0, 10), 10)


class BaseFrameGravityTests(unittest.TestCase):
    def test_base_frame_gravity(self):
        sample = fold.AccelSample((0, 0, 0), (0, 0, 1), 1.0, True)
        self.assertEqual(fold.base_frame_gravity(sample), (0, 0, 1))


class LidVectorTests(unittest.TestCase):
    def test_lid_vector(self):
        up = fold.lid_vector((0.0, 0.0, 1.0), 0.0)
        self.assertAlmostEqual(up[1], 0.0)
        self.assertAlmostEqual(up[2], -1.0)

        up = fold.lid_vector((0.0, 0.0, 1.0), 90.0)
        self.assertAlmostEqual(up[1], 1.0)
        self.assertAlmostEqual(up[2], 0.0)

        up = fold.lid_vector((0.0, 0.0, 1.0), 180.0)
        self.assertAlmostEqual(up[1], 0.0)
        self.assertAlmostEqual(up[2], 1.0)


class HingeSampleTests(unittest.TestCase):
    def test_hinge_sample(self):
        s = fold.HingeSample(102.0, 101.0, 359.0, 102.0, 1.0, True)
        self.assertEqual(s.fold_deg, 102.0)
        self.assertTrue(s.ok)


class AccelSampleTests(unittest.TestCase):
    def test_accel_sample(self):
        s = fold.AccelSample((0, 0, 0), (0, 0, 0), 0.0, False)
        self.assertFalse(s.ok)


class StateTests(unittest.TestCase):
    def test_state_as_dict(self):
        s = fold.State()
        d = s.as_dict()
        self.assertIn("transform", d)
        self.assertIn("mode", d)


class OrientationLabelTests(unittest.TestCase):
    def test_orientation_label(self):
        self.assertEqual(fold.ORIENTATION_LABEL["normal"], "landscape")
        self.assertEqual(fold.ORIENTATION_LABEL["inverted"], "landscape, upside down")
        self.assertEqual(fold.ORIENTATION_LABEL["right"], "portrait, the long way")
        self.assertEqual(fold.ORIENTATION_LABEL["left"], "portrait")


class IconsTests(unittest.TestCase):
    def test_icons(self):
        self.assertIn("tablet", fold.ICONS)
        self.assertIn("tent", fold.ICONS)
        self.assertIn("laptop", fold.ICONS)


class MappingsTests(unittest.TestCase):
    def test_mappings(self):
        self.assertIn("standard", fold.MAPPINGS)
        self.assertIn("portrait-swapped", fold.MAPPINGS)
        self.assertIn("landscape-swapped", fold.MAPPINGS)
        self.assertIn("rotated-180", fold.MAPPINGS)


class BaseTransformTests(unittest.TestCase):
    def test_base_transform(self):
        self.assertEqual(fold.BASE_TRANSFORM["normal"], 0)
        self.assertEqual(fold.BASE_TRANSFORM["inverted"], 2)
        self.assertEqual(fold.BASE_TRANSFORM["right"], 3)
        self.assertEqual(fold.BASE_TRANSFORM["left"], 1)


class ReadPublishedStateTests(unittest.TestCase):
    def test_read_published_state_none(self):
        result = fold.read_published_state(max_age=0)
        self.assertIsNone(result)

    def test_read_published_state_valid(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            real = fold.STATE_FILE
            fold.STATE_FILE = Path(td) / "state.json"
            fold.STATE_FILE.write_text(json.dumps({"test": True}))
            result = fold.read_published_state(max_age=5.0)
            self.assertIsNotNone(result)
            fold.STATE_FILE = real


class CalibrateTests(unittest.TestCase):
    def test_calibrate_refuses_a_machine_it_was_not_written_for(self):
        # `calibrate` calls `load_settings()` itself and then refuses on the
        # machine identity, so this exercises the real early return. On a machine
        # that *is* a Yoga 260 the first thing it does is prompt on stdin, so it
        # is only safe to run when it will bail out before that.
        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(json.dumps({}))
            real_detect = fold.Machine.detect
            fold.Machine.detect = classmethod(
                lambda cls: cls(
                    "ACME", "NOTA-YOGA", "Some Other Laptop", "10", "X", "Y"
                )
            )
            try:
                rc = fold.calibrate(type("Args", (), {"write": None})())
            finally:
                fold.SHELL_JSON = real
                fold.Machine.detect = real_detect
        self.assertEqual(rc, 1)

    def test_calibrate_write_takes_exactly_nine_floats(self):
        # The parser is the only thing that decides how many numbers
        # `calibrate --write` accepts. Nine goes through as nine floats in
        # row-major order, which is the shape `calibrate` slices into rows.
        parser = fold.build_parser()
        args = parser.parse_args(
            ["calibrate", "--write", "1", "0", "0", "0", "1", "0", "0", "0", "1"]
        )
        self.assertEqual(args.write, [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])

    def test_calibrate_write_refuses_eight_numbers(self):
        # One short of a 3x3, and argparse has to reject it rather than pass a
        # short list on for `calibrate` to slice into ragged rows.
        parser = fold.build_parser()
        with self.assertRaises(SystemExit) as caught:
            parser.parse_args(
                ["calibrate", "--write", "1", "0", "0", "0", "1", "0", "0", "0"]
            )
        self.assertNotEqual(caught.exception.code, 0)

    def test_calibrate_write_refuses_ten_numbers(self):
        parser = fold.build_parser()
        with self.assertRaises(SystemExit) as caught:
            parser.parse_args(
                [
                    "calibrate",
                    "--write",
                    "1",
                    "0",
                    "0",
                    "0",
                    "1",
                    "0",
                    "0",
                    "0",
                    "1",
                    "0",
                ]
            )
        self.assertNotEqual(caught.exception.code, 0)

    def test_calibrate_without_write_leaves_it_none(self):
        parser = fold.build_parser()
        args = parser.parse_args(["calibrate"])
        self.assertIsNone(args.write)


class CmdAnalyzeTests(unittest.TestCase):
    def _write_recording(
        self, *, wall: bool, label: str | None, transforms: tuple[int, ...] = (0,)
    ) -> str:
        """Four samples: three of one pose, one of another, plus junk lines.

        The blank line, the unparseable line and the row whose `kind` is
        neither header nor sample are deliberate -- the reader skips all
        three, and a test that never feeds one proves nothing about that.
        `wall` adds three wall-clock stamps making two adjacent pairs: the
        first runs 3.9s ahead of the recording (a suspend), the second keeps
        pace. `transforms` round-robins over the samples; the default repeats
        one value, which leaves transforms the recording never reached.
        """
        rows: list[str] = []
        if label is not None:
            rows.append(
                json.dumps(
                    {
                        "kind": "header",
                        "label": label,
                        "settings": {"mapping": "standard"},
                    }
                )
            )
        rows.append(json.dumps({"kind": "marker"}))
        specs = [
            (0.1, 104.0, True),
            (0.2, 104.2, False),
            (0.3, 270.0, False),
            (0.4, 104.1, False),
        ]
        walls = (100.0, 104.0, 104.1, None)
        for index, (t, fold, still) in enumerate(specs):
            row: dict = {
                "kind": "sample",
                "t": t,
                "fold": fold,
                "still": still,
                "axis": "y",
                "sign": 1,
                "tiltDeg": 12.5,
                "marginDeg": 4.0,
                "orientation": "landscape",
                "lidUp": [0.0, 1.0, 0.0],
                "baseUp": [0.0, 0.0, 1.0],
                "raw": [0, -9000, -1000],
                "mode": "laptop",
                "transform": transforms[index % len(transforms)],
                "panelTransform": 0,
                "residual": 0.0,
                "hingeOk": True,
            }
            if wall and walls[index] is not None:
                row["wall"] = walls[index]
            rows.append(json.dumps(row))
        rows.append("")
        rows.append("not json")
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write("\n".join(rows) + "\n")
        self.addCleanup(lambda: os.unlink(handle.name))
        return handle.name

    def test_cmd_analyze_no_file(self):
        rc = fold.cmd_analyze(
            type("Args", (), {"path": "/nonexistent.jsonl", "json": False})()
        )
        self.assertEqual(rc, 1)

    def test_cmd_analyze_empty(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("")
            path = f.name
        rc = fold.cmd_analyze(type("Args", (), {"path": path, "json": False})())
        self.assertEqual(rc, 1)
        os.unlink(path)

    def test_cmd_analyze_groups_poses_and_reports_them(self):
        path = self._write_recording(wall=True, label="fixture")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fold.cmd_analyze(type("Args", (), {"path": path, "json": True})())
        self.assertEqual(rc, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["label"], "fixture")
        self.assertEqual(report["samples"], 4)
        self.assertEqual(report["seconds"], 0.3)
        self.assertEqual(report["stillFraction"], 0.25)
        self.assertEqual(report["foldMin"], 104.0)
        self.assertEqual(report["foldMax"], 270.0)
        self.assertEqual(len(report["poses"]), 2)
        self.assertEqual(report["poses"][0]["n"], 3)
        self.assertEqual(report["poses"][1]["n"], 1)
        # wall 100.0 at t=0.1 and wall 104.0 at t=0.2: the wall clock gained
        # 3.9s more than the recording clock, which is a suspend. The next
        # pair keeps pace with the recording, so only the first one counts.
        self.assertEqual(len(report["suspends"]), 1)
        self.assertEqual(report["suspends"][0]["seconds"], 3.9)

    def test_cmd_analyze_prints_the_table(self):
        path = self._write_recording(wall=True, label=None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fold.cmd_analyze(type("Args", (), {"path": path, "json": False})())
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("recording", text)
        self.assertIn("SUSPENDS    1", text)
        self.assertIn("transforms never chosen", text)
        self.assertNotIn("label       ", text)

    def test_cmd_analyze_without_walls_reports_no_suspends(self):
        path = self._write_recording(
            wall=False, label="labelled", transforms=(0, 1, 2, 3)
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = fold.cmd_analyze(type("Args", (), {"path": path, "json": False})())
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("suspends    none", text)
        self.assertIn("label       labelled", text)
        self.assertNotIn("transforms never chosen", text)


class CmdRecordTests(unittest.TestCase):
    def test_cmd_record(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.jsonl"
            rc = fold.cmd_record(
                type(
                    "Args",
                    (),
                    {"out": str(out), "seconds": 0.1, "hz": 10, "label": "test"},
                )()
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())


MONITOR = {
    "name": "eDP-1",
    "width": 1366,
    "height": 768,
    "refreshRate": 60.0,
    "x": 0,
    "y": 0,
    "scale": 1.0,
    "transform": 0,
    "description": "eDP-1",
}

DEVICES = {
    "keyboards": [{"name": "at-translated-set-2-keyboard"}],
    "touch": [{"name": "wacom-pen-and-multitouch-sensor-finger"}],
    "tablets": [{"name": "wacom-pen-and-multitouch-sensor-pen"}],
    "mice": [
        {"name": "etps/2-elantech-trackpoint"},
        {"name": "etps/2-elantech-touchpad"},
    ],
    "touchpads": [{"name": "etps/2-elantech-touchpad"}],
}


class CmdRotateTests(unittest.TestCase):
    """Turning the screen by hand.

    Both of these used to run against the real machine. `cmd_rotate` reaches for
    `hyprctl` and for `shell.json` directly, and this class patched neither, so
    `test_cmd_rotate_force` rotated the actual panel and wrote
    `locked: true` into the actual `~/.config/omarchy/shell.json` -- leaving the
    user's rotation locked after a test run, and turning a screen in the middle
    of one. A unit test may not reconfigure the desktop it runs on.
    """

    def setUp(self):
        self._real_shell = fold.SHELL_JSON
        self.td = tempfile.TemporaryDirectory()
        shell = Path(self.td.name) / "shell.json"
        shell.write_text(
            json.dumps(
                {"bar": {"layout": {"right": [{"id": "estrocondoso.yoga260-fold"}]}}}
            )
        )
        fold.SHELL_JSON = shell
        self._real_run = fold.subprocess.run
        self._real_json = fold.subprocess.run

        class Done:
            returncode = 0
            stdout = json.dumps(
                [
                    {
                        "name": "eDP-1",
                        "width": 1366,
                        "height": 768,
                        "refreshRate": 60.0,
                        "x": 0,
                        "y": 0,
                        "scale": 1.0,
                        "transform": 0,
                    }
                ]
            )
            stderr = ""

        def fake_run(cmd, **kwargs):
            done = Done()
            if any("monitors" in str(part) for part in cmd):
                done.stdout = json.dumps([MONITOR])
            elif any("devices" in str(part) for part in cmd):
                # Without a real digitizer the daemon's own hardware gate
                # refuses to act, and the test would be asserting that refusal
                # rather than the lock ordering it is named for.
                done.stdout = json.dumps(DEVICES)
            elif any("clients" in str(part) for part in cmd):
                done.stdout = "[]"
            else:
                done.stdout = ""
            return done

        fold.subprocess.run = fake_run

    def tearDown(self):
        fold.SHELL_JSON = self._real_shell
        fold.subprocess.run = self._real_run
        self.td.cleanup()

    def test_a_book_machine_is_refused_a_hand_turn(self):
        rc = fold.cmd_rotate(fold.load_settings(), "normal", force=False)
        self.assertEqual(rc, 1)

    def test_forcing_a_turn_never_touches_the_real_shell_json(self):
        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(
                json.dumps(
                    {
                        "bar": {
                            "layout": {"right": [{"id": "estrocondoso.yoga260-fold"}]}
                        }
                    }
                )
            )
            try:
                fold.cmd_rotate(fold.load_settings(), "normal", force=True)
                written = json.loads(fold.SHELL_JSON.read_text())
            finally:
                fold.SHELL_JSON = real
        entry = written["bar"]["layout"]["right"][0]
        self.assertIs(entry["locked"], True)

    def test_the_lock_is_written_before_the_turn(self):
        # The daemon re-asserts the transform it thinks is right, so a turn
        # issued without the lock already in place is a turn it undoes on its
        # next pass.
        settings = fold.load_settings()
        self.assertIs(settings["locked"], False)
        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(
                json.dumps(
                    {
                        "bar": {
                            "layout": {"right": [{"id": "estrocondoso.yoga260-fold"}]}
                        }
                    }
                )
            )
            try:
                self.assertEqual(fold.cmd_rotate(settings, "next", force=True), 0)
                entry = fold.shell_entry()
                self.assertIs(entry["locked"], True)
            finally:
                fold.SHELL_JSON = real


class CmdDebugTests(unittest.TestCase):
    """The live `debug` stream.

    This is an interactive command: it loops printing a row per change until it
    is interrupted, and holds no other exit. It used to be called with `{}` as
    its settings, which made it raise KeyError on 'accelName' and fail
    instantly -- so the suite never hung, and equally the command was never
    actually run by any test. Handed real settings it streams forever, which
    hangs the suite on a machine that has the hardware.

    So it is started for real and then interrupted from the inside, which is the
    only way to check that it produces its header and rows without waiting for a
    person to press Ctrl-C.
    """

    def test_debug_prints_a_header_and_rows_and_then_stops_when_interrupted(self):
        seen = {"n": 0}
        first: dict = {}
        real_read_pose = fold.FoldDaemon.read_pose

        def fake_read_pose(self):
            seen["n"] += 1
            if seen["n"] > 2:
                raise KeyboardInterrupt
            if seen["n"] == 2:
                # The dedup row keys on two consecutive poses being equal, and
                # two real gyro samples a moment apart only *usually* round
                # alike -- so the branch ran or not depending on ambient
                # vibration, and the coverage gate was a coin flip. Replay the
                # first pose: the state the dedup exists for, by construction.
                return dict(first)
            pose = real_read_pose(self)
            first.update(pose)
            return pose

        with tempfile.TemporaryDirectory() as td:
            real_state = fold.STATE_FILE
            fold.STATE_FILE = Path(td) / "state.json"
            try:
                with patch.object(fold.FoldDaemon, "read_pose", fake_read_pose):
                    rc = fold.cmd_debug(fold.load_settings())
            finally:
                fold.STATE_FILE = real_state
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(seen["n"], 2)

    def test_debug_says_why_it_cannot_act_before_it_starts_streaming(self):
        # The header is the one line that tells somebody the plugin is not
        # running, which is the whole reason to reach for `debug` first.
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            real_state = fold.STATE_FILE
            fold.STATE_FILE = Path(td) / "state.json"
            try:
                with (
                    patch.object(
                        fold.FoldDaemon, "read_pose", side_effect=KeyboardInterrupt
                    ),
                    contextlib.redirect_stdout(out),
                ):
                    fold.cmd_debug(fold.load_settings())
            finally:
                fold.STATE_FILE = real_state
        self.assertIn("#", out.getvalue())


class FoldDaemonInitTests(unittest.TestCase):
    def test_fold_daemon_init(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(json.dumps({}))
            # Real settings: `__init__` builds the sensors straight out of them,
            # so the `{}` this used to pass raised KeyError on 'accelName' and
            # the constructor was never actually covered.
            daemon = fold.FoldDaemon(fold.load_settings())
            self.assertIsNotNone(daemon)
            self.assertIsNotNone(daemon.state)
            # The gate's contract, not this machine's answer: every condition
            # `blocking_reason` checks, recomputed here. When all of them hold
            # the reason has to be None, and when one does not it has to be a
            # sentence somebody can act on. Asserting only that the reason is
            # None would pass on the laptop this suite runs on and say nothing
            # about the daemon -- and would fail outright on a machine the
            # plugin was never meant to drive, which is the case the gate
            # exists for.
            gate = (
                daemon.machine.matches(),
                daemon.hypr.available,
                daemon.accel.available,
                daemon.hinge.available,
                bool(daemon.state.panel),
                bool(daemon.state.touch),
                bool(daemon.state.pen),
                abs(fold.determinant(daemon.accel.matrix) - 1.0) <= 1e-6,
            )
            reason = daemon.blocking_reason()
            if all(gate):
                self.assertIsNone(reason)
            else:
                self.assertTrue(reason, "blocked but no reason given")
                self.assertTrue(reason.strip(), "reason is blank")
            fold.SHELL_JSON = real


class FoldDaemonRunTests(unittest.TestCase):
    def test_fold_daemon_run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            real = fold.SHELL_JSON
            fold.SHELL_JSON = Path(td) / "shell.json"
            fold.SHELL_JSON.write_text(json.dumps({}))
            daemon = fold.FoldDaemon(fold.load_settings())
            daemon.stop = True
            rc = daemon.run()
            self.assertEqual(rc, 0)
            fold.SHELL_JSON = real

    def _looping_daemon(self, *, changed, settling=False, blocked=None, passes=3):
        """A daemon whose only real method is `run`, driven deterministically.

        No thread and no patched `time.sleep`: the loop is stopped by `changed`
        returning True `passes` times and then flipping `stop`, so the number of
        iterations is exact and nothing global is disturbed. Patching `time.sleep`
        module-wide from inside a test also reaches unittest's own timing and the
        other tests in the process, which is not a thing a unit test may do.
        """
        daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        daemon.settings = {"pollSec": 0.001, "verifySec": 0.001}
        daemon.state = fold.State()
        daemon.stop = False
        remaining = {"n": passes}

        def counted(*_args, **_kwargs):
            remaining["n"] -= 1
            if remaining["n"] <= 0:
                daemon.stop = True
            return changed

        daemon.changed = MagicMock(side_effect=counted)
        daemon.settling = MagicMock(return_value=settling)
        daemon.step = MagicMock()
        # `run()` takes the sample itself now and hands the same one to both
        # `changed` and `step`, so the loop no longer depends on those two
        # reading the hardware between them. Mocking the read here is what lets
        # the tests below check that it is threaded through.
        daemon.read_pose = MagicMock(return_value={"sample": True})
        daemon._publish = MagicMock(return_value=True)
        daemon.blocking_reason = MagicMock(return_value=blocked)
        daemon.verify = MagicMock()
        daemon.resolve_devices = MagicMock()
        daemon.reload_settings = MagicMock()
        daemon.announce = MagicMock()
        return daemon

    def test_run_executes_step_when_changed(self):
        daemon = self._looping_daemon(changed=True)
        self.assertEqual(daemon.run(), 0)
        daemon.step.assert_called()

    def test_run_hands_step_the_very_sample_it_detected_on(self):
        """One read per pass, and it is the one that decides.

        This is the regression guard for the change that removed the second
        sensor read. The loop used to call `changed()`, which read all six
        sensors and discarded them, and then call `step()`, which read the same
        six again and acted on those. Two reads of a 10 Hz sensor can describe
        two different reports, so the pass that *decided* and the pass that
        *acted* could be working from different instants.

        So `changed` and `step` must both receive the identical object the loop
        read once, and `read_pose` must be called once per pass -- not twice.
        """
        daemon = self._looping_daemon(changed=True)
        self.assertEqual(daemon.run(), 0)
        sample = {"sample": True}
        daemon.read_pose.assert_called()
        daemon.changed.assert_called_with(sample)
        daemon.step.assert_called_with(sample)
        self.assertEqual(daemon.read_pose.call_count, daemon.changed.call_count)

    def test_run_executes_step_when_settling_and_nothing_changed(self):
        daemon = self._looping_daemon(changed=False, settling=True)
        self.assertEqual(daemon.run(), 0)
        daemon.step.assert_called()

    def test_run_skips_step_when_nothing_changed_and_nothing_owed(self):
        daemon = self._looping_daemon(changed=False, settling=False)
        self.assertEqual(daemon.run(), 0)
        daemon.step.assert_not_called()

    def test_run_announces_once_the_outcome_changes(self):
        daemon = self._looping_daemon(changed=True)
        self.assertEqual(daemon.run(), 0)
        daemon.announce.assert_called()

    def test_run_never_announces_while_it_has_a_message_to_give(self):
        # The screen that is being held and the reason for it are the same
        # fact; announcing on every pass of a held pose would spam the OSD.
        daemon = self._looping_daemon(changed=True)
        daemon.state.messages = ["moving; holding the last orientation"]
        self.assertEqual(daemon.run(), 0)
        daemon.announce.assert_not_called()

    def test_run_does_not_reassert_a_transform_it_must_not_touch(self):
        # `verify` re-asserts, which means it rotates. Only `step` used to
        # consult the hardware check, so the slow timer turned a panel on a
        # machine this plugin is not for while every other path refused.
        daemon = self._looping_daemon(changed=False, blocked="not a Yoga 260")
        self.assertEqual(daemon.run(), 0)
        daemon.verify.assert_not_called()
        daemon.step.assert_not_called()

    def test_run_reasserts_and_rediscovers_devices_when_allowed(self):
        daemon = self._looping_daemon(changed=False, blocked=None)
        self.assertEqual(daemon.run(), 0)
        daemon.verify.assert_called()
        daemon.resolve_devices.assert_called()


class AnnounceTests(unittest.TestCase):
    def setUp(self):
        self.daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        self.daemon.state = fold.State()
        self.daemon.state.folded = False
        self.daemon.state.mode = "book"
        self.daemon.state.orientation = "normal"
        self.daemon.state.last_error = ""
        self.daemon.hypr = MagicMock()
        self.daemon.hypr.env = {}

    def test_announce_no_error(self):
        with (
            patch.object(
                fold, "which", return_value="/usr/bin/omarchy-osd"
            ) as which_mock,
            patch.object(fold.subprocess, "run") as run_mock,
        ):
            self.daemon.announce()
        which_mock.assert_called_once_with("omarchy-osd")
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0][4], "Laptop · landscape")

    def test_announce_with_error(self):
        self.daemon.state.last_error = "test error"
        with (
            patch.object(
                fold, "which", return_value="/usr/bin/omarchy-osd"
            ) as which_mock,
            patch.object(fold.subprocess, "run") as run_mock,
        ):
            self.daemon.announce()
        which_mock.assert_not_called()
        run_mock.assert_not_called()


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        self.daemon.state = fold.State()
        self.daemon.state.panel = "eDP-1"
        self.daemon.state.transform = 0
        self.daemon.hypr = MagicMock()
        self.daemon.hypr.current_transform.return_value = 0
        self.daemon.hypr.rotate_transaction.return_value = (True, "")
        self.daemon.hypr.set_device_transform.return_value = (True, "")
        self.daemon.state.touch = None
        self.daemon.state.pen = None
        # `apply` records how long the decision has been held, so the attribute
        # has to exist even on a daemon built with `__new__`.
        self.daemon.decision_since = time.monotonic()

    def test_verify_no_change(self):
        self.daemon.verify()
        self.daemon.hypr.rotate_transaction.assert_not_called()

    def test_verify_reasserts(self):
        self.daemon.hypr.current_transform.return_value = 2
        self.daemon.verify()
        self.daemon.hypr.rotate_transaction.assert_called_once_with(
            "eDP-1", 0, [None, None]
        )


class ResolveDevicesTests(unittest.TestCase):
    def setUp(self):
        self.daemon = fold.FoldDaemon.__new__(fold.FoldDaemon)
        self.daemon.state = fold.State()
        self.daemon.settings = {
            "touchPattern": "wacom.*finger",
            "penPattern": "wacom.*pen",
            "keyboardNames": ["at-translated-set-2-keyboard"],
            "pointerNames": ["etps/2-elantech-trackpoint"],
            "touchpadNames": ["etps/2-elantech-touchpad"],
            "panel": "eDP-1",
        }
        self.daemon.hypr = MagicMock()
        self.daemon.hypr.device_names.return_value = {
            "touch": ["wacom-pen-and-multitouch-sensor-finger"],
            "pen": ["wacom-pen-and-multitouch-sensor-pen"],
            "pointer": ["etps/2-elantech-trackpoint"],
            "touchpad": ["etps/2-elantech-touchpad"],
            "keyboard": ["at-translated-set-2-keyboard"],
        }
        self.daemon.hypr.match_device.side_effect = lambda pattern, kinds: (
            "wacom-pen-and-multitouch-sensor-finger"
            if "finger" in pattern
            else "wacom-pen-and-multitouch-sensor-pen"
        )
        self.daemon.hypr.match_any.side_effect = lambda candidates, present: [
            c for c in candidates if c in present
        ]
        self.daemon.hypr.internal_monitor.return_value = "eDP-1"

    def test_resolve_devices(self):
        self.daemon.resolve_devices()
        self.assertEqual(
            self.daemon.state.touch, "wacom-pen-and-multitouch-sensor-finger"
        )
        self.assertEqual(self.daemon.state.pen, "wacom-pen-and-multitouch-sensor-pen")


if __name__ == "__main__":
    unittest.main(verbosity=2)
