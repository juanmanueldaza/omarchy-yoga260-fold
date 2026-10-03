# Root-Triggered Buffer — a PROPOSAL, not an implementation

> **Status: NOT IMPLEMENTED. Nothing in this document has been built, installed,
> or measured.** There is no `yoga260-bufferd` binary, no systemd unit, no
> socket, and no C source in this repository. Every latency figure below is
> either (a) measured on this machine and marked **[MEASURED]**, (b) a
> projection from the mechanism and marked **[PROJECTED]**, or (c) a design
> choice still to be validated and marked **[PROPOSED]**.
>
> This file exists so the option is *on the table* with its trade-offs written
> down honestly. Do not read it as a description of working software.

## Why this document exists

The current plugin polls `in_angl0_raw` on a timer. That read costs
**10.31 ms median**, and it is not something a faster language can fix
**[MEASURED]**:

| | measured value |
|---|---|
| Python overhead for a whole arithmetic+JSON pass | **7.91 µs median** |
| One sysfs read of `in_angl0_raw` | **10.31 ms median** |
| Ratio | **~1300×** |

The cost is a synchronous round-trip inside the kernel driver (ISHTP to the
sensor hub), not userspace work. The only way to remove it is to stop pulling
and let the kernel push.

## What the kernel already offers **[MEASURED]**

`/sys/bus/iio/devices/iio:device5/` on this machine:

| attribute | value / state | who can write it |
|---|---|---|
| `trigger/current_trigger` | `hinge-dev5` | root |
| `buffer/enable` | `0` (disabled) | root |
| `buffer/length` | `2` | root |
| `buffer/watermark` | `1` | root |
| `buffer/direction` | `in` | root |
| `buffer/data_available` | `0` | — (readable by anyone) |
| `buffer0/in_angl{0,1,2}_en` | `0` | root |
| `scan_elements/in_angl{0,1,2}_en` | `0` | root |
| `scan_elements/in_timestamp_en` | `0` | root |
| `dev` | `510:5` → `/dev/iio:device5` | — |
| `/dev/iio:device5` perms | **`crw------- root root`** | root only |

So the full IIO triggered-buffer path exists and is correctly formed. It is
simply **root-gated at three independent points**: enable the buffer, select
scan elements, and read the sample data. There is no unprivileged subset that
yields data.

This also corrects an earlier claim in `windows.md`: the hinge device is *not*
`INDIO_DIRECT_MODE`-only. `buffer/` and `buffer0/` are both present, and
`current_trigger` names a real trigger. The blocker is permissions, not
absence.

## What a root helper would have to do **[PROPOSED]**

```
1. open /dev/iio:device5                      (root; crw-------)
2. write "1" to scan_elements/in_angl0_en     (select the fold channel only)
3. write N to buffer/length                   (small — see Latency note)
4. write "1" to buffer/watermark              (signal on every sample)
5. write "1" to buffer/enable                 (start the DMA into the buffer)
6. blocking read() on the fd                  (kernel returns when data_available)
7. decode sample, publish newest fold angle
8. on shutdown, write "0" to buffer/enable    (leave the device as found)
```

### Latency note — buffer length is a latency knob, not a throughput knob **[PROPOSED]**

For a "latest value" consumer, a *larger* buffer is strictly worse. With
`length = 10` at 10 Hz you buffer 1.0 s of reports, and the sample you read
can be up to ~0.9 s stale — which is **worse than the 10.31 ms poll it
replaces**. `length = 1..2` with `watermark = 1` gives the newest report with
no queueing.

*(An earlier draft of this file claimed `length = 10` was "0.3 s of data".
At 10 Hz, 10 samples is 1.0 s. The figure was wrong.)*

## Architecture **[PROPOSED]**

```
┌────────────────────────────────────────────────────────────────┐
│ system service: yoga260-fold-buffer.service   (User=root)      │
│   reads /dev/iio:device5, publishes to                        │
│   /run/yoga260-fold/fold.sock                                  │
└────────────────────────────────────────────────────────────────┘
                    │  unix stream, SOCK_DGRAM-like line protocol
┌────────────────────────────────────────────────────────────────┐
│ user plugin: omarchy-yoga260-fold            (uid 1000)       │
│   if socket present → consume it                               │
│   else             → current sysfs path (unchanged)            │
└────────────────────────────────────────────────────────────────┘
```

### Why the socket is NOT in `/run/user/1000`

An earlier draft put it at `/run/user/1000/fold.sock` and told the installer
to `mkdir -p /run/user/1000`. **That is wrong.** `/run/user/<uid>` is created
and destroyed by `systemd --user` / `pam_systemd`; a system-level daemon
writing into it fights logind, and the directory can disappear underneath the
service on logout.

The correct mechanism is `RuntimeDirectory=`, which systemd creates, owns, and
removes for you:

```ini
[Service]
RuntimeDirectory=yoga260-fold
RuntimeDirectoryMode=0750
```

That yields `/run/yoga260-fold/`, mode 0750, owned `root:<group>`. Put the
invoking user in `<group>` (e.g. a `fold` group) and no world-readable socket
exists.

## systemd unit sketch **[PROPOSED — not written, not tested]**

```ini
[Unit]
Description=Yoga 260 fold angle buffer helper
Documentation=man:iio

[Service]
Type=simple
User=root
RuntimeDirectory=yoga260-fold
RuntimeDirectoryMode=0750
ExecStart=/usr/lib/yoga260-fold/fold-bufferd
Restart=on-failure
RestartSec=3
StateDirectory=yoga260-fold

# Hardening — this process reads exactly one device and writes one socket.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=
ProtectKernelTunables=no   # REQUIRED: we must write the sysfs buffer controls
DeviceAllow=/dev/iio:device5 char-510:5
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
```

Notes on the sketch, because several defaults are wrong here:

- **No `After=network.target`.** An earlier draft had `After=network.target`
  and `Wants=network.target`. This service reads a local I²C/SPI-attached
  sensor over an internal transport. It needs no network, and declaring the
  dependency just delays boot for nothing.
- `ProtectSystem=strict` plus writing `/sys/bus/iio/.../buffer*` conflicts.
  sysfs is not covered by `ReadWritePaths=` remapping in the way one might
  hope; this needs testing before it can be claimed to work.
- `CapabilityBoundingSet=` empty is correct — the service opens a device it is
  already root-owned, it does not need any capability bit.

## Socket protocol **[PROPOSED — invented for this document, not validated]**

One JSON object per line, newline-delimited, both directions.

Client → server:

| line | meaning |
|---|---|
| `{"cmd":"hello"}` | client identifies; server replies with its config |
| `{"cmd":"status"}` | request a `status` message |

Server → client:

| message | meaning |
|---|---|
| `{"type":"sample","fold":112.0,"t":1724223480.04}` | newest fold angle, monotonic `t` |
| `{"type":"status","rate_hz":10.0,"connected":true}` | reply to hello/status |
| `{"type":"error","code":"...","msg":"..."}` | something the client should surface |

### Rules the implementation must obey

1. **Validate `fold` before use.** Range-check to `0 <= fold <= 360` and reject
   NaN/Inf. A root daemon feeding unvalidated floats into a compositor command
   is a foot-gun. (The user-side daemon already range-checks; defence in depth,
   but the producer should not emit garbage in the first place.)
2. **Never block the client.** A slow or dead client must not stall sensor
   capture. Either drop samples for that client (correct: a fold daemon wants
   the *newest* value, not every value) or cap per-client buffers.
3. **One line, no partial reads.** The client must handle a short `read()`
   and buffer until `\n`.
4. **Reconnect must be cheap and must not lose the fallback.** On socket error
   the plugin drops back to the sysfs path immediately rather than retrying —
   see below.
5. **The server is the authority on `rate_hz`.** The plugin's `pollSec` only
   governs the sysfs path.

### Permissions are the real security surface

The socket grants read access to the fold angle — low sensitivity — but it is
a root process serving it. The mitigations that matter:

- socket in `/run/yoga260-fold/`, mode `0750`, group-gated (above)
- drop the client if it does not send `hello` within a short timeout
- no `eval`, no shell, no arbitrary command dispatch — **the command set must
  be a fixed enum**. The earlier draft listed a `SHUTDOWN` client command,
  which is a remote-shutdown primitive on a root process for no benefit.
  Remove it; the service stops via `systemctl`.
- bind to `SOCK_STREAM` with `listen(1)`-style backlog, not an unbounded accept
  loop.

## Fallback behaviour **[PROPOSED]**

| situation | plugin behaviour |
|---|---|
| socket absent at startup | sysfs path, forever (current behaviour) |
| socket present, connects | sample path |
| socket dies mid-session | **drop to sysfs within one poll interval**, log once |
| socket present but silent | fall back on a timeout; do not block the pass |
| helper disabled later | re-probe on a slow timer (e.g. every 30 s) |

The one hard requirement: **the plugin must never depend on the root helper.**
The root path is an optimisation. Any bug in it must degrade to the 10.31 ms
poll, not to a broken screen.

## Expected performance **[PROJECTED — not measured]**

There is no implementation, so there are no measurements. What the mechanism
predicts:

| | current | projected |
|---|---|---|
| per-sample kernel cost | 10.31 ms | ~0 (pushed, not pulled) |
| pass wall time | ~21 ms | dominated by the compositor call |
| sensor-rate ceiling | 10 Hz (kernel-fixed) | 10 Hz (unchanged) |

**The 10 Hz floor does not move.** The buffer changes *when* the plugin learns
the angle, not *how often new angles exist*. The honest framing:

- **Latency to notice a fold** improves — from "up to one poll period plus a
  10.31 ms read" to "as soon as the report lands".
- **Pass wall time** improves only by the read cost, i.e. ~10 ms of a ~21 ms
  pass.
- **Battery** improves marginally: fewer blocking wakeups, but the sensor still
  reports at 10 Hz either way.

> An earlier draft of this file contained a "Real-World Test Results" section
> showing `real 0m0.003s` before and after. **Those numbers were invented.**
> There was no implementation to run. They are removed, and should never be
> reintroduced without an actual `time` capture from a real run.

## Is it worth building? — the honest case

**Against:**

- The current plugin is already 5.1× faster than it was, and the pass fits the
  100 ms budget with ~79 ms headroom **[MEASURED]**.
- The floor is the sensor's 10 Hz rate, which the buffer does not change.
- It adds a root system service to a plugin whose entire value proposition is
  that it needs no root.
- Every line of the helper is new, privileged, kernel-adjacent code on a
  machine where the unprivileged path already works.
- The benefit is bounded by ~10 ms per pass on a pass that already has 79 ms
  of slack.

**For:**

- It removes the only remaining blocking kernel round-trip from the hot path.
- It is the one mechanism the hardware explicitly provides for this.
- If fold latency ever matters more than no-root (kiosk mode, a demo, a
  one-off install), it is available.

**Recommendation: do not build it.** Ship the no-root plugin. Keep this
document as the record of why, and as the starting point for anyone who later
has a reason to want it.

## If someone does build it — checklist

- [ ] Real `fold-bufferd` source in-repo, reviewed, no unexplained privileges
- [ ] `RuntimeDirectory=`, group-gated socket, no `/run/user/` writes
- [ ] `length` set small (1–2); **verify** rather than trusting this note
- [ ] Sample validation (range, NaN, monotonic clock) on the producer side
- [ ] Fixed enum command set; no client-triggered shutdown
- [ ] Plugin falls back to sysfs on every failure mode, tested by killing the
      helper mid-session
- [ ] Tests for: socket present/absent, mid-session death, silent server,
      malformed line, out-of-range fold, reconnect
- [ ] `systemd-analyze verify` clean
- [ ] Real `time` captures published, with the command used
- [ ] Documented uninstall path
- [ ] Confirmed it does not regress the 311 existing tests

## Sources for the mechanism

- `Documentation/iio/` — buffer/watermark/data_available/scan element ABI
- `drivers/iio/industrialio-buffer.c` — `iio_buffer`, `data_available`
- `drivers/hid/hid-sensor-custom-intel-hinge.c` — the hinge channels
- `systemd.exec(5)` — `RuntimeDirectory`, `DeviceAllow`, `ProtectSystem`
- Measured directly on this machine (kernel 7.2.5-3-omarchy, uid 1000) —
  see the table above
