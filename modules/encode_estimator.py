"""Whole-batch time-remaining estimate for the media encoder.

Pure bookkeeping and arithmetic — nothing here spawns a process. The sampler
that produces the measurements lives in modules/media_encoder.py; this module
only consumes them, so it can be unit-tested without media on disk.

The estimate has two independent sources:

  * short timed sample encodes, which give a per-file cost before that file has
    started (this is what makes an ETA available early), and
  * the real encodes' own progress, which is ground truth and progressively
    displaces the sampled prediction for the file it belongs to.

A single calibration factor reconciles the two, absorbing everything a sample
structurally cannot include: autocrop, Dolby Vision RPU extract/inject, the
mkvmerge remux tail, seek/GOP warm-up and encoder ramp-up.
"""

import threading
import time
from statistics import median

# Sampling shape. Pass 1 takes one sample per file at SAMPLE_OFFSETS[0]; pass 2
# refines with the remaining offsets while an encode slot happens to be idle.
SAMPLE_VIDEO_SECONDS = 6.0
SAMPLE_OFFSETS = (0.35, 0.20, 0.70)
MAX_SAMPLES_PER_FILE = 3
SAMPLE_MIN_SECONDS = 2.0
SAMPLE_MIN_SOURCE = 15.0
SAMPLE_MAX_WALL = 180.0

# A file's own progress is ignored below MIN and fully trusted at TRUST.
ETA_MIN_FRACTION = 0.03
ETA_TRUST_FRACTION = 0.50

# Display easing. Rising slowly matters: each new sample perturbs the pooled
# unit cost, and a countdown that jumps up costs more trust than one that
# converges from below.
ETA_ALPHA_DOWN = 0.30
ETA_ALPHA_UP = 0.10

ETA_CALIB_ALPHA = 0.30
ETA_CALIB_CLAMP = (0.4, 3.0)
ETA_MAX_SECONDS = 30 * 86400

SCHED_POLL_INTERVAL = 1.0

# Per-file lifecycle
PENDING = "pending"
RUNNING = "running"
DONE = "done"


class EncodeEstimator:
    """Thread-safe batch ETA. Mirrors ProgressState's lock discipline.

    Fed from three sides: the sampler thread (record_sample), the encode
    threads (note_start/note_progress/note_complete) and the scheduler
    (set_slots/set_sampling). Read only by the spinner's render thread, via
    display_eta(). Every method takes the lock for its whole body, and the lock
    is never held across a subprocess call, a join, or a ProgressState call.
    """

    def __init__(self, files, codec_cap):
        """files: [{'index','name','duration','width','height','is_4k','threads'}]

        'threads' is the thread count that file's *real* encode will use, so
        sampled and predicted costs are expressed in the same units.
        """
        self._lock = threading.Lock()
        self._codec_cap = max(1, int(codec_cap))
        self._slots = self._codec_cap
        self._sampling = False
        self._status_text = None
        self._calibration = 1.0
        self._displayed = None

        self._files = {}
        for f in files:
            duration = f.get('duration')
            width = f.get('width') or 0
            height = f.get('height') or 0
            # Pixel count normalises samples across resolutions. Fall back to
            # 1080p rather than zero so a failed dimension probe degrades to a
            # plausible cost instead of a division by zero.
            pixels = (width * height) or (1920 * 1080)
            self._files[f['index']] = {
                'name': f.get('name'),
                'duration': duration if duration and duration > 0 else None,
                'pixels': pixels,
                'is_4k': bool(f.get('is_4k')),
                'threads': max(1, int(f.get('threads') or 1)),
                'state': PENDING,
                'sample_wall': 0.0,
                'sample_video': 0.0,
                'samples': 0,
                'unsampleable': False,
                'started_at': None,
                'fraction': 0.0,
                'actual_wall': None,
            }

    # ------------------------------------------------------------------
    # sampler side
    # ------------------------------------------------------------------

    def record_sample(self, index, wall_seconds, video_seconds):
        with self._lock:
            rec = self._files.get(index)
            if rec is None or wall_seconds <= 0 or video_seconds <= 0:
                return
            rec['sample_wall'] += wall_seconds
            rec['sample_video'] += video_seconds
            rec['samples'] += 1

    def mark_unsampleable(self, index):
        with self._lock:
            rec = self._files.get(index)
            if rec is not None:
                rec['unsampleable'] = True

    def sample_count(self, index):
        with self._lock:
            rec = self._files.get(index)
            return rec['samples'] if rec else 0

    def wants_sample(self, index):
        """True if another sample of this file would still be worth taking."""
        with self._lock:
            rec = self._files.get(index)
            if rec is None:
                return False
            return (
                rec['state'] == PENDING
                and not rec['unsampleable']
                and rec['duration'] is not None
                and rec['samples'] < MAX_SAMPLES_PER_FILE
            )

    def file_is_pending(self, index):
        with self._lock:
            rec = self._files.get(index)
            return rec is not None and rec['state'] == PENDING

    def set_sampling(self, active):
        with self._lock:
            self._sampling = bool(active)

    def sampling_active(self):
        with self._lock:
            return self._sampling

    def set_status_text(self, text):
        with self._lock:
            self._status_text = text

    def status_text(self):
        with self._lock:
            return self._status_text

    # ------------------------------------------------------------------
    # encode side
    # ------------------------------------------------------------------

    def note_start(self, index):
        with self._lock:
            rec = self._files.get(index)
            if rec is None:
                return
            rec['state'] = RUNNING
            rec['started_at'] = time.time()
            rec['fraction'] = 0.0

    def note_progress(self, index, fraction):
        with self._lock:
            rec = self._files.get(index)
            if rec is None:
                return
            if fraction > rec['fraction']:
                rec['fraction'] = min(1.0, fraction)

    def note_complete(self, index):
        with self._lock:
            rec = self._files.get(index)
            if rec is None:
                return
            if rec['started_at'] is not None:
                rec['actual_wall'] = max(0.0, time.time() - rec['started_at'])
            rec['state'] = DONE
            rec['fraction'] = 1.0

    # ------------------------------------------------------------------
    # scheduler side
    # ------------------------------------------------------------------

    def set_slots(self, slots):
        with self._lock:
            self._slots = max(1, int(slots))

    # ------------------------------------------------------------------
    # render side
    # ------------------------------------------------------------------

    def display_eta(self):
        """Smoothed seconds remaining for the whole batch, or None."""
        with self._lock:
            raw = self._eta_raw()
            if raw is None:
                return self._displayed

            raw = min(raw, ETA_MAX_SECONDS)
            if self._displayed is None:
                self._displayed = raw
            else:
                alpha = ETA_ALPHA_DOWN if raw < self._displayed else ETA_ALPHA_UP
                self._displayed += (raw - self._displayed) * alpha
            return self._displayed

    def debug_line(self):
        with self._lock:
            sampled = sum(1 for r in self._files.values() if r['samples'])
            unsampleable = sum(1 for r in self._files.values() if r['unsampleable'])
            unit = self._sample_unit_cost()
            unit_str = f"{unit:.3e}" if unit else "n/a"
            return (
                f"files={len(self._files)} sampled={sampled} "
                f"unsampleable={unsampleable} sample_unit_cost={unit_str} "
                f"calibration={self._calibration:.3f} slots={self._effective_slots()}"
            )

    # ------------------------------------------------------------------
    # internals — all called with the lock already held
    # ------------------------------------------------------------------

    def _effective_slots(self):
        return max(1, self._slots - (1 if self._sampling else 0))

    def _unit_cost(self, rec, wall, video_or_duration):
        """Wall seconds per video second, normalised by pixels and threads.

        Dividing by pixels and multiplying by threads is what lets a sample of
        one file price a different file at another resolution or CPU share.
        """
        if video_or_duration <= 0:
            return None
        rate = wall / video_or_duration
        return rate * rec['threads'] / rec['pixels']

    def _sample_unit_cost(self):
        """Median normalised cost across sampled files, or None."""
        costs = []
        for rec in self._files.values():
            if rec['sample_video'] <= 0:
                continue
            u = self._unit_cost(rec, rec['sample_wall'], rec['sample_video'])
            if u and u > 0:
                costs.append(u)
        return median(costs) if costs else None

    def _observed_unit_cost(self):
        """Median normalised cost from the real encodes, or None.

        Completed files contribute their measured wall time; in-flight files
        past ETA_MIN_FRACTION contribute the projection elapsed/fraction.
        """
        now = time.time()
        costs = []
        for rec in self._files.values():
            if rec['duration'] is None:
                continue
            projected = None
            if rec['state'] == DONE and rec['actual_wall']:
                projected = rec['actual_wall']
            elif rec['state'] == RUNNING and rec['fraction'] >= ETA_MIN_FRACTION:
                elapsed = now - (rec['started_at'] or now)
                if elapsed > 0:
                    projected = elapsed / rec['fraction']
            if projected and projected > 0:
                u = self._unit_cost(rec, projected, rec['duration'])
                if u and u > 0:
                    costs.append(u)
        return median(costs) if costs else None

    def _refresh_calibration(self, sample_unit, observed_unit):
        if not sample_unit or not observed_unit:
            return
        low, high = ETA_CALIB_CLAMP
        target = min(high, max(low, observed_unit / sample_unit))
        self._calibration += (target - self._calibration) * ETA_CALIB_ALPHA

    def _base_cost(self, rec, sample_unit, observed_unit, calibration):
        """Predicted total wall seconds for a file that has not run yet."""
        if rec['duration'] is None:
            return None

        if rec['sample_video'] > 0:
            rate = rec['sample_wall'] / rec['sample_video']
            return rate * rec['duration'] * calibration

        # No sample for this file: price it from the pool. A real observation
        # needs no calibration; a sampled pool does.
        if observed_unit:
            unit = observed_unit
        elif sample_unit:
            unit = sample_unit * calibration
        else:
            return None
        return unit * (rec['pixels'] / rec['threads']) * rec['duration']

    def _eta_raw(self):
        if not self._files:
            return None
        if all(rec['state'] == DONE for rec in self._files.values()):
            return 0.0

        sample_unit = self._sample_unit_cost()
        observed_unit = self._observed_unit_cost()
        self._refresh_calibration(sample_unit, observed_unit)
        calibration = self._calibration

        if sample_unit is None and observed_unit is None:
            return None

        now = time.time()
        remaining_4k = 0.0
        remaining_other = 0.0
        n_other = 0
        longest = 0.0
        known_any = False

        for rec in self._files.values():
            if rec['state'] == DONE:
                continue

            base = self._base_cost(rec, sample_unit, observed_unit, calibration)

            if rec['state'] == RUNNING:
                fraction = rec['fraction']
                elapsed = now - (rec['started_at'] or now)
                if fraction >= ETA_MIN_FRACTION and elapsed > 0:
                    # Blend the file's own measurement in, weighted by how far
                    # it has come. Its own progress is unimpeachable evidence
                    # about itself, so by ETA_TRUST_FRACTION the sampled
                    # prediction for this file is discarded entirely.
                    omega = min(1.0, fraction / ETA_TRUST_FRACTION)
                    self_projected = elapsed / fraction
                    if base is None:
                        total = self_projected
                    else:
                        total = (1.0 - omega) * base + omega * self_projected
                    remaining = total * (1.0 - fraction)
                elif base is None:
                    continue
                else:
                    remaining = base * (1.0 - fraction)
            else:
                if base is None:
                    continue
                remaining = base

            known_any = True
            longest = max(longest, remaining)
            if rec['is_4k']:
                # 4K files run exclusively (see can_submit in media_encoder),
                # so their work is strictly serial rather than shared out.
                remaining_4k += remaining
            else:
                remaining_other += remaining
                n_other += 1

        if not known_any:
            return None

        slots = max(1, min(self._effective_slots(), n_other)) if n_other else 1
        eta = remaining_4k + remaining_other / slots

        # Makespan floor: a batch cannot finish before its longest remaining job.
        return max(eta, longest)
