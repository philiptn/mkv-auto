"""Live CPU load and disk throughput for the status line.

A leaf module: it deliberately imports nothing from modules.misc, which imports
this one. It also knows nothing about the config — the caller decides whether
the user wants these chips rendered at all.

Readings are taken by one background thread and only read back by the spinner's
daemon render thread (see ContinuousSpinner._spin in modules/misc.py), which
imposes three rules:

  * Sample continuously, not on demand. A rate needs two readings spaced in
    time; sampling only when a stage asks would leave every stage blank for its
    first seconds and short stages blank entirely.
  * No blocking. psutil.cpu_percent(interval=0.5) would stall its caller for
    half a second, so CPU load is derived from our own cpu_times() deltas
    instead. That also keeps us immune to get_worker_thread_count() and
    get_max_ocr_threads(), which call the blocking form from worker threads
    mid-run and would otherwise reset the process-global sample psutil's
    non-blocking form measures against.
  * No exceptions. A raise on the render thread kills the spinner silently, so
    the public entry points swallow everything and render nothing instead.

Disk figures are scoped to the block device backing the paths handed in — in
practice the temp directory, where every pipeline stage does its work. They are
device-wide, not process-wide: the bytes are moved by ffmpeg/mkvmerge/mkvextract
children whose IO never shows up in this process' own counters.
"""

import os
import threading
import time

import psutil

SAMPLE_INTERVAL = 1.0   # seconds between raw samples
STALE_AFTER = 10.0      # gap after which a delta is discarded, not reported
EMA_ALPHA = 0.4         # smoothing of the displayed value

# Below this the disk counts as idle. It no longer gates what gets rendered —
# the chip stays on the line either way — only whether the next reading is
# treated as the start of new work. Set well under the hundreds of KB/s that
# subtitle and remux work runs at, so those stages read as active; it only has
# to clear the background noise of an idle disk.
IDLE_FLOOR = 32 * 1024  # bytes/s

# Resolved device sets, keyed by the path tuple asked for. Resolution walks
# sysfs and the mount table, so it is done once per stage, not once per render.
_device_cache = {}

# One sampler per device set, kept module-level so readings survive the gap
# between two stages that watch the same disk: only the first stage of a run
# pays the cold start.
_disk_samplers = {}


def format_rate(bytes_per_sec):
    """Throughput as a single unit with no space: "812KB/s", "1.2GB/s".

    Mirrors format_size()'s 1024-based rounding so the status line agrees with
    the size figures printed elsewhere, but adds the sub-megabyte branch that
    format_size() has no need for. Subtitle and remux work often sits in the
    hundreds of KB/s, and a line of "0MB/s" reads as broken rather than idle.
    """
    if bytes_per_sec is None or bytes_per_sec < 0:
        return "0B/s"

    gb_val = bytes_per_sec / (1024 ** 3)
    mb_val = bytes_per_sec / (1024 ** 2)
    kb_val = bytes_per_sec / 1024

    if gb_val >= 1:
        if gb_val >= 10:
            return f"{round(gb_val)}GB/s"
        return f"{round(gb_val, 1)}GB/s"
    elif mb_val >= 1:
        return f"{round(mb_val)}MB/s"
    elif kb_val >= 1:
        return f"{round(kb_val)}KB/s"
    else:
        return f"{round(bytes_per_sec)}B/s"


class _Sampler:
    """Rate-limited sampling with EMA smoothing, shared by both metrics.

    Subclasses supply read() -> raw counters and rates(prev, cur, elapsed) ->
    tuple of displayed values. Sampling is gated to SAMPLE_INTERVAL regardless
    of how often the render thread asks, and a gap longer than STALE_AFTER
    re-primes instead of reporting an average over time when nothing was
    running — a stage that starts after a two-minute encode should not open
    with that encode's throughput smeared across it.
    """

    def __init__(self, clock=time.time):
        self._clock = clock
        self._prev = None
        self._prev_time = 0.0
        self._displayed = None

    def read(self):
        raise NotImplementedError

    def rates(self, prev, cur, elapsed):
        raise NotImplementedError

    def current(self):
        """Last smoothed values without taking a reading, or None."""
        return self._displayed

    def sample(self, now=None):
        """Latest smoothed values, or None until two samples exist."""
        now = self._clock() if now is None else now

        if self._prev is None or now - self._prev_time >= SAMPLE_INTERVAL:
            cur = self.read()
            elapsed = now - self._prev_time

            if self._prev is None or elapsed > STALE_AFTER:
                self._prev = cur
                self._prev_time = now
                self._displayed = None
                self.reset()
                return None

            raw = self.rates(self._prev, cur, elapsed)
            self._prev = cur
            self._prev_time = now
            self._displayed = self.blend(raw, self._displayed)

        return self._displayed

    def blend(self, raw, displayed):
        """What to display, given this reading and what is already displayed.

        The default eases toward the reading, so jitter within a stage does not
        reach the line. A subclass overrides this where a reading has to be
        taken as-is, or where easing from the current value would be wrong.
        """
        if displayed is None:
            return raw
        return tuple(
            d + (r - d) * EMA_ALPHA
            for d, r in zip(displayed, raw)
        )

    def reset(self):
        """Drop any state derived from readings, after a prime or a long gap."""


class CpuSampler(_Sampler):
    """System-wide CPU busy percentage, from cpu_times() deltas."""

    def __init__(self, source=psutil.cpu_times, clock=time.time):
        super().__init__(clock=clock)
        self._source = source

    def read(self):
        return self._source()

    def rates(self, prev, cur, elapsed):
        total = sum(cur) - sum(prev)
        idle = (cur.idle - prev.idle) + (
            getattr(cur, 'iowait', 0.0) - getattr(prev, 'iowait', 0.0)
        )
        if total <= 0:
            return (0.0,)
        pct = 100.0 * (total - idle) / total
        return (min(100.0, max(0.0, pct)),)


class DiskSampler(_Sampler):
    """Read/write bytes per second across one set of block devices.

    devices is a tuple of psutil perdisk keys, or None to fall back to the
    system-wide totals when the path could not be traced to a device.
    """

    def __init__(self, devices, source=None, clock=time.time):
        super().__init__(clock=clock)
        self._devices = devices
        self._source = source or self._psutil_counters
        self._idle = True
        self._anchor = False

    def reset(self):
        self._idle = True
        self._anchor = False

    def blend(self, raw, displayed):
        """Keep a figure on the line at all times, idle included.

        This used to blank the chip for any window under the idle floor, which
        on a real copy is most of them: writes reach the device in flush bursts,
        so a second at 600MB/s is regularly followed by a second at nothing.
        With the chip sitting between the header and the description, blanking
        it shifted the whole line back and forth every couple of seconds and
        read as a glitch. A figure easing down to 0B/s and back reads as the
        disk catching its breath, which is what actually happened.

        What the blanking was really protecting was the transition *into* work.
        Sampling runs continuously, so when a stage begins the displayed value
        is whatever the disk was doing before it - near zero - and easing out of
        that made a disk running flat out open at a small fraction of its real
        rate and take about six seconds to admit the truth. That protection is
        kept, without the blanking:

          * the window straddling the moment work starts is part idle and so
            understates badly; the line holds its current figure through it, and
          * the full window after it is taken as-is rather than eased.

        Everything else is eased, so the figure falls away over a few seconds
        once a stage stops working the disk instead of being inherited whole by
        whichever stage comes next.
        """
        active = max(raw) >= IDLE_FLOOR
        straddling = active and self._idle
        self._idle = not active

        if straddling:
            self._anchor = True
            return displayed if displayed is not None else tuple(0.0 for _ in raw)

        if self._anchor:
            self._anchor = False
            if active:
                return raw

        eased = super().blend(raw, displayed)
        if not active and max(eased) < IDLE_FLOOR:
            # An idle disk lands on a clean zero rather than trailing the last
            # few kilobytes of an EMA that is still remembering finished work.
            return tuple(0.0 for _ in eased)
        return eased

    def _psutil_counters(self):
        if self._devices is None:
            counters = psutil.disk_io_counters()
            if counters is None:
                return (0, 0)
            return (counters.read_bytes, counters.write_bytes)

        perdisk = psutil.disk_io_counters(perdisk=True)
        read = sum(perdisk[d].read_bytes for d in self._devices if d in perdisk)
        write = sum(perdisk[d].write_bytes for d in self._devices if d in perdisk)
        return (read, write)

    def read(self):
        return self._source()

    def rates(self, prev, cur, elapsed):
        if elapsed <= 0:
            return (0.0, 0.0)
        # Counters only ever climb; a drop means a device disappeared from the
        # sum or the kernel counter wrapped, neither of which is a real rate.
        read = max(0, cur[0] - prev[0]) / elapsed
        write = max(0, cur[1] - prev[1]) / elapsed
        return (read, write)


def _existing_ancestor(path):
    """Nearest existing directory at or above path.

    Stage working directories are created as the pipeline goes, so a path may
    not exist yet at the moment a stage starts rendering. Its parent is on the
    same filesystem, which is all that matters here.
    """
    path = os.path.abspath(path)
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    return path


def _device_from_sysfs(path, perdisk):
    """Block device backing path, via its st_dev major:minor in sysfs.

    Returns None for filesystems with an anonymous st_dev (btrfs, zfs,
    overlayfs), which have no /sys/dev/block entry to resolve.
    """
    st = os.stat(path)
    link = f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
    if not os.path.exists(link):
        return None
    name = os.path.basename(os.path.realpath(link))
    return name if name in perdisk else None


def _device_from_mounts(path, perdisk):
    """Block device backing path, via the longest matching mountpoint.

    The fallback for anonymous-st_dev filesystems. realpath() on the device
    resolves /dev/mapper/* names to the dm-N keys psutil reports.
    """
    best = None
    best_len = -1
    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if path == mount or path.startswith(mount.rstrip(os.sep) + os.sep):
            if len(mount) > best_len:
                best = part.device
                best_len = len(mount)

    if best is None:
        return None
    name = os.path.basename(os.path.realpath(best))
    return name if name in perdisk else None


def _resolve_devices(paths):
    """Perdisk keys for paths, or None to mean "every disk".

    None is a usable answer, not a failure: system-wide totals still show the
    machine working, they just include disks the pipeline is not touching.
    """
    key = tuple(paths)
    if key in _device_cache:
        return _device_cache[key]

    devices = set()
    resolved_all = True

    try:
        perdisk = psutil.disk_io_counters(perdisk=True) or {}
        for path in paths:
            existing = _existing_ancestor(path)
            if existing is None:
                resolved_all = False
                continue

            name = _device_from_sysfs(existing, perdisk)
            if name is None:
                name = _device_from_mounts(existing, perdisk)

            if name is None:
                resolved_all = False
            else:
                devices.add(name)
    except Exception:
        resolved_all = False

    result = tuple(sorted(devices)) if (resolved_all and devices) else None
    _device_cache[key] = result
    return result


def _normalize(paths):
    if isinstance(paths, str):
        return (paths,)
    return tuple(paths)


_cpu_sampler = CpuSampler()

_thread = None
_thread_lock = threading.Lock()


def _sample_loop():
    """Keep every known sampler warm, forever.

    Sampling on demand from the render thread would mean each stage starts with
    no reading and shows nothing for its first two render ticks — and a stage
    that finishes inside four seconds, like moving one file to the output
    folder, would never show anything at all. A rate needs two readings spaced
    in time, so the only way to have one ready the moment a stage starts is to
    have been reading all along.
    """
    while True:
        try:
            _cpu_sampler.sample()
            for sampler in list(_disk_samplers.values()):
                sampler.sample()
        except Exception:
            pass
        time.sleep(SAMPLE_INTERVAL)


def _ensure_running():
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_sample_loop, daemon=True)
        _thread.start()


def cpu_chip():
    """"CPU 92%" for the status line, or "" until a rate is known.

    Bare text: the caller owns how it sits on the line.
    """
    try:
        _ensure_running()
        values = _cpu_sampler.current()
        if values is None:
            return ""
        return f"CPU {values[0]:.0f}%"
    except Exception:
        return ""


def peak_rate(read, write):
    """The busier direction, as an arrow and a rate: "↓1.2GB/s", "↑840MB/s".

    Only one of the two is worth watching: a stage is either pulling a file in
    or pushing one out, and the quieter figure is the other end of that same
    copy. Which direction leads is itself information, so when the balance
    flips mid-stage the chip flips with it — no hysteresis, no tie-breaking
    toward whichever side happened to lead first.
    """
    if write > read:
        return f"↑{format_rate(write)}"
    return f"↓{format_rate(read)}"


def disk_chip(paths):
    """"↓1.2GB/s" for the busier direction on the disk(s) behind paths, or "".

    Down is read, up is write, matching the direction bytes travel relative to
    the process. paths is a single path or an iterable of them; passing both
    ends of a cross-device copy makes the chip cover the union.

    The empty string means there is no reading yet - the first second of a cold
    device set, or psutil declining to answer - not that the disk is idle. An
    idle disk renders "↓0B/s"; see DiskSampler.blend.
    """
    try:
        _ensure_running()
        devices = _resolve_devices(_normalize(paths))
        sampler = _disk_samplers.get(devices)
        if sampler is None:
            sampler = DiskSampler(devices)
            # Take the baseline here rather than waiting for the sampler
            # thread's next tick, so a device set seen for the first time has a
            # rate one second from now instead of two.
            sampler.sample()
            _disk_samplers[devices] = sampler

        values = sampler.current()
        if values is None:
            return ""
        return peak_rate(values[0], values[1])
    except Exception:
        return ""
