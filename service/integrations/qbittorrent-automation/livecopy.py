"""Streaming live copy of in-progress torrents to the MKV-Auto output folder.

Continuously appends the contiguous-complete byte prefix of each downloading
media file to its final MKV-Auto destination, so the file appears in the library
and is watchable long before the torrent finishes.

The result is a *placeholder*. When the torrent completes, the normal flow in
qbittorrent-automation.py copies it to the MKV-Auto input folder and MKV-Auto's
processed output overwrites the live copy at the same path. This is the same
contract encode_media_files() uses in modules/media_encoder.py, where a
placeholder is copied to the destination before encoding so Plex/Sonarr pick the
media up immediately and the real output replaces it afterwards.

The destination path comes from MKV-Auto itself, over a request/response file
queue on the folder the two services already share (see resolve-worker.py). It
is never computed here: a second copy of the naming logic would drift and leave
two copies of every release in the library.

Known limitation: if qBittorrent rechecks a torrent and invalidates pieces we
already copied, the live copy is briefly wrong. It is a placeholder that gets
overwritten by MKV-Auto's real output, so the corruption never reaches the final
artifact.
"""

import json
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

# libtorrent piece states as reported by /api/v2/torrents/pieceStates
PIECE_NOT_DOWNLOADED = 0
PIECE_DOWNLOADING = 1
PIECE_DOWNLOADED = 2

# Torrent states where the piece map is in flux or no progress is possible.
CHECKING_STATES = {'checkingDL', 'checkingUP', 'checkingResumeData', 'moving'}
DEAD_STATES = {'error', 'missingFiles'}

SAMPLE_PATTERN = re.compile(r'(?:^sample|[-_.]sample)$', re.IGNORECASE)

# Extras keywords, mirroring modules/misc.py extras_definitions. process_extras()
# renames these using the sibling file list, which the preview cannot model, so
# they are excluded from live copy entirely.
EXTRAS_KEYWORDS = (
    'behindthescenes', 'deleted', 'featurette', 'interview', 'scene',
    'short', 'trailer', 'other', 'extra',
)


def contiguous_prefix(file_entry, index, files, piece_size, piece_states):
    """Bytes of this file that are guaranteed on disk, counted from its start.

    Returns a length in [0, file size]. Never over-reports: every byte in the
    returned range is backed by a piece in state PIECE_DOWNLOADED.
    """
    size = file_entry['size']
    piece_range = file_entry.get('piece_range') or []
    if len(piece_range) != 2:
        return 0
    lo, hi = piece_range                                 # INCLUSIVE on both ends

    if piece_size <= 0 or not piece_states:
        return 0
    if lo < 0 or hi >= len(piece_states) or hi < lo:     # metadata not settled
        return 0
    if file_entry.get('priority', 1) == 0:               # "do not download"
        return 0

    # The torrent's last piece is short and a file can end mid-piece, so once
    # qBittorrent reports the file complete, trust that rather than piece math.
    if file_entry.get('progress', 0) >= 1.0:
        return size

    first_bad = next(
        (p for p in range(lo, hi + 1) if piece_states[p] != PIECE_DOWNLOADED), None)
    if first_bad is None:
        return size
    if first_bad == lo:
        return 0

    # Torrent-global byte offset of this file's first byte. The file list is in
    # index order, so preceding entries are exactly the preceding bytes.
    offset = sum(f['size'] for f in files if f['index'] < index)

    # libtorrent pad files are not reported by the API, so that running sum can
    # come out short. Check it against the piece range we were handed.
    consistent = (offset // piece_size == lo
                  and (offset + size - 1) // piece_size == hi)

    if consistent:
        # first_bad * piece_size is the first byte we cannot trust. It and
        # `offset` are both torrent-global, so the difference is directly a
        # file-relative length - no `lo` term belongs here.
        safe = first_bad * piece_size - offset
    else:
        # The file starts somewhere inside piece `lo` but we do not know where,
        # so drop a whole piece. Lags by under two pieces, which is a few MiB.
        safe = (first_bad - lo - 1) * piece_size

    return max(0, min(safe, size))


def split_torrent_relative(name):
    """Split a qBittorrent file name into path components.

    Names are relative to the torrent root and use '/' on Linux and '\\' on
    Windows regardless of where the automation runs.
    """
    return [part for part in re.split(r'[\\/]', name) if part not in ('', '.')]


def is_live_copyable(basename, min_size, size, extensions):
    """Whether a torrent file is eligible for live copy.

    Excludes everything whose final name the preview cannot predict: samples get
    deleted by MKV-Auto, and extras get renamed by process_extras() using the
    sibling file list.
    """
    stem, extension = os.path.splitext(basename)
    if extension.lower() not in extensions:
        return False
    if size < min_size:
        return False
    if SAMPLE_PATTERN.search(stem):
        return False
    lowered = stem.lower()
    if any(lowered.endswith(keyword) for keyword in EXTRAS_KEYWORDS):
        return False
    return True


class QueueResolver:
    """Ask MKV-Auto where a file will end up, over the shared folder.

    Writes a request and waits for resolve-worker.py inside the MKV-Auto service
    container to answer. Answers are cached: resolution is deterministic for a
    given config, and under NORMALIZE_FILENAMES=full each one costs a TVMaze
    lookup.
    """

    def __init__(self, queue_dir_for, timeout, log, poll_interval=0.5):
        self._queue_dir_for = queue_dir_for
        self._timeout = timeout
        self._log = log
        self._poll_interval = poll_interval
        self._cache = {}
        self._lock = threading.Lock()

    def relative_output_path(self, tag, relative_path):
        """Return the destination relative to the MKV-Auto output root, or None."""
        key = (tag, relative_path)
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        answer = self._ask(tag, relative_path)
        with self._lock:
            self._cache[key] = answer
        return answer

    def _ask(self, tag, relative_path):
        queue_dir = self._queue_dir_for(tag)
        request_id = uuid.uuid4().hex
        request = os.path.join(queue_dir, f"{request_id}.req")
        response_name = f"{request_id}.res"
        response = os.path.join(queue_dir, response_name)

        try:
            # Created here rather than once at startup so a lookup still works
            # if the queue is removed underneath us, or the mount was not up
            # when the service started.
            os.makedirs(queue_dir, exist_ok=True)
            temp = os.path.join(queue_dir, f".{request_id}.req.tmp")
            with open(temp, 'w', encoding='utf-8') as handle:
                json.dump({'v': 1, 'path': relative_path}, handle, ensure_ascii=False)
            os.rename(temp, request)
        except OSError as e:
            self._log.error(f"❌ Could not queue a path lookup in {queue_dir}: {e}")
            return None

        deadline = time.monotonic() + self._timeout
        try:
            while time.monotonic() < deadline:
                # listdir rather than exists(): over NFS a negative lookup can
                # stay cached for acdirmax, while a readdir revalidates.
                try:
                    present = response_name in os.listdir(queue_dir)
                except OSError:
                    present = False

                if present:
                    with open(response, 'r', encoding='utf-8') as handle:
                        payload = json.load(handle)
                    _unlink(response)
                    if not payload.get('ok'):
                        self._log.warning(
                            f"⚠️ MKV-Auto could not resolve '{relative_path}': "
                            f"{payload.get('error')}")
                        return None
                    return payload

                time.sleep(self._poll_interval)

            self._log.warning(
                f"⚠️ No answer from MKV-Auto for '{relative_path}' within "
                f"{self._timeout:.0f}s. Is RESOLVE_WORKER enabled on the service?")
            return None
        except Exception as e:
            self._log.error(f"❌ Path lookup for '{relative_path}' failed: {e}")
            return None
        finally:
            _unlink(request)


class LiveJob:
    """One file being tailed from the torrent into the output folder."""

    def __init__(self, key, torrent_hash, file_index, relative_path, dest_path, size):
        self.key = key
        self.torrent_hash = torrent_hash
        self.file_index = file_index
        self.relative_path = relative_path
        self.dest_path = dest_path
        self.size = size
        self.copied = 0
        # Written by tick(), read by the worker. Plain int assignment, atomic
        # under the GIL - no lock needed.
        self.watermark = 0
        self.sources = []
        self.state = 'running'
        self.last_progress = time.monotonic()
        self.stop = threading.Event()


class LiveCopyManager:
    def __init__(self, request_fn, outputs, resolver, log, state_dir,
                 interval=10, max_workers=4, min_size=200 * 1024 ** 2,
                 extensions=('.mkv',), chunk_size=8 * 1024 ** 2,
                 stall_timeout=1800, set_sequential=True, translate=None):
        self._request = request_fn
        self._outputs = outputs
        self._resolver = resolver
        self._log = log
        self._state_dir = state_dir
        self._interval = interval
        self._min_size = min_size
        self._extensions = tuple(e.lower() for e in extensions)
        self._chunk_size = chunk_size
        self._stall_timeout = stall_timeout
        self._set_sequential = set_sequential
        self._translate = translate or (lambda path, mappings: path)

        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix='livecopy')
        self._jobs = {}
        self._skip_until = {}
        self._next_tick = 0.0
        self._shutting_down = threading.Event()

        os.makedirs(self._state_dir, exist_ok=True)

    # -- main loop hook --------------------------------------------------------

    def tick(self, mappings):
        """Refresh watermarks and start workers for newly eligible files.

        Called from the automation's 3s poll loop; rate-limits itself so that
        loop's cadence is preserved.
        """
        if self._shutting_down.is_set() or time.monotonic() < self._next_tick:
            return
        self._next_tick = time.monotonic() + self._interval

        try:
            torrents = self._downloading_torrents()
        except Exception as e:
            self._log.error(f"❌ Could not list downloading torrents: {e}")
            return

        live_hashes = set()
        for torrent, tag in torrents:
            live_hashes.add(torrent['hash'])
            try:
                self._process_torrent(torrent, tag, mappings)
            except Exception as e:
                self._log.error(
                    f"❌ Live copy failed for '{torrent.get('name')}': {e}")

        self._retire_vanished(live_hashes)

    def shutdown(self, timeout=30):
        self._shutting_down.set()
        for job in list(self._jobs.values()):
            job.stop.set()
        self._executor.shutdown(wait=True, cancel_futures=True)

    # -- torrent discovery -----------------------------------------------------

    def _downloading_torrents(self):
        """Return (torrent, tag) for every in-progress torrent we live copy."""
        found = {}
        for tag in self._outputs:
            response = self._request(
                "get", "/api/v2/torrents/info",
                params={"filter": "downloading", "tag": tag})
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} - {response.text}")
            for torrent in response.json():
                found.setdefault(torrent['hash'], (torrent, tag))
        return list(found.values())

    def _process_torrent(self, torrent, tag, mappings):
        torrent_hash = torrent['hash']
        state = torrent.get('state', '')

        if state in DEAD_STATES:
            self._stop_jobs(torrent_hash, delete=False, reason=f"torrent {state}")
            return
        if state in CHECKING_STATES:
            # Freeze every watermark: the piece map is not trustworthy mid-check.
            return

        self._ensure_sequential(torrent)

        files = self._request(
            "get", "/api/v2/torrents/files", params={"hash": torrent_hash}).json()
        properties = self._request(
            "get", "/api/v2/torrents/properties", params={"hash": torrent_hash}).json()
        piece_states = self._request(
            "get", "/api/v2/torrents/pieceStates", params={"hash": torrent_hash}).json()
        piece_size = properties.get('piece_size', 0)

        for index, entry in enumerate(files):
            entry.setdefault('index', index)

        for index, entry in enumerate(files):
            key = (torrent_hash, entry['index'])
            job = self._jobs.get(key)

            if job is None:
                job = self._maybe_start(torrent, tag, entry, files, mappings)
                if job is None:
                    continue

            job.sources = self._source_candidates(torrent, entry, mappings)
            job.watermark = contiguous_prefix(
                entry, entry['index'], files, piece_size, piece_states)

    def _ensure_sequential(self, torrent):
        """Turn on sequential download and first/last piece priority.

        Both endpoints are toggles, not setters, so the current value has to be
        read first. A missing field means an API that does not report it - do
        nothing rather than flip it on every tick.
        """
        if not self._set_sequential:
            return
        if 'seq_dl' in torrent and not torrent['seq_dl']:
            self._request("post", "/api/v2/torrents/toggleSequentialDownload",
                          data={"hashes": torrent['hash']})
            self._log.info(f"⏩ Enabled sequential download for '{torrent['name']}'")
        if 'f_l_piece_prio' in torrent and not torrent['f_l_piece_prio']:
            self._request("post", "/api/v2/torrents/toggleFirstLastPiecePrio",
                          data={"hashes": torrent['hash']})

    # -- job creation ----------------------------------------------------------

    def _maybe_start(self, torrent, tag, entry, files, mappings):
        torrent_hash = torrent['hash']
        key = (torrent_hash, entry['index'])

        if self._skip_until.get(key, 0) > time.monotonic():
            return None

        parts = split_torrent_relative(entry['name'])
        if not parts:
            return None
        basename = parts[-1]

        if not is_live_copyable(basename, self._min_size, entry['size'],
                                self._extensions):
            self._skip(key)
            return None

        relative_path = self._mkv_auto_relative_path(torrent, files, parts)
        if relative_path is None:
            self._skip(key)
            return None

        answer = self._resolver.relative_output_path(tag, relative_path)
        if answer is None:
            # Bounded retry - the worker may just be starting up.
            self._skip(key, seconds=self._interval * 3)
            return None

        # Under NORMALIZE_FILENAMES=full a failed TVMaze lookup means the real
        # run may well settle on a different name, which would leave the live
        # copy behind as a duplicate.
        if answer.get('full_info_found') is False and _is_full_mode(answer):
            self._log.warning(
                f"⚠️ Skipping live copy of '{basename}': MKV-Auto could not look "
                f"up its metadata, so the final name is not yet certain")
            self._skip(key, seconds=3600)
            return None

        dest_path = os.path.join(self._outputs[tag], answer['relative_path'])
        job = LiveJob(key, torrent_hash, entry['index'], relative_path,
                      dest_path, entry['size'])
        job.sources = self._source_candidates(torrent, entry, mappings)

        self._jobs[key] = job
        self._executor.submit(self._run_job, job)
        self._log.info(f"📡 Live copying '{basename}' -> {dest_path}")
        return job

    def _mkv_auto_relative_path(self, torrent, files, parts):
        """The path MKV-Auto will see once the torrent is copied to its input.

        copy_torrent_content() publishes the torrent under torrent['name'], so
        that has to be the root here too. For a single-file torrent whose name
        differs from the file's own name the two disagree and the destination
        would be a guess - skip rather than guess.
        """
        if len(files) == 1 and len(parts) == 1:
            if parts[0] != torrent['name']:
                self._log.warning(
                    f"⚠️ Skipping live copy of '{torrent['name']}': the torrent "
                    f"name does not match its file name ('{parts[0]}')")
                return None
            return parts[0]
        return '/'.join([torrent['name']] + parts[1:]) if len(parts) > 1 else parts[0]

    def _skip(self, key, seconds=None):
        self._skip_until[key] = time.monotonic() + (
            seconds if seconds is not None else 3600)

    # -- source location -------------------------------------------------------

    def _source_candidates(self, torrent, entry, mappings):
        """On-disk paths to try for an in-progress file, best first."""
        parts = split_torrent_relative(entry['name'])
        roots = []

        # download_path first: with "keep incomplete torrents in" set, the bytes
        # are there and not in save_path yet.
        for key in ('download_path', 'save_path'):
            value = torrent.get(key)
            if value:
                roots.append(value)

        content_path = torrent.get('content_path')
        if content_path:
            roots.append(content_path if len(parts) == 1
                         else os.path.dirname(content_path))

        candidates = []
        for root in roots:
            try:
                # Translate the root, not the joined path: the mapping table is
                # prefix-based and the relative part carries no drive letter.
                translated = self._translate(root, mappings)
            except Exception:
                continue
            path = os.path.join(translated, *parts)
            # ".!qB" first while incomplete - that is the live copy's whole point.
            for candidate in (path + '.!qB', path):
                if candidate not in candidates:
                    candidates.append(candidate)
        return candidates

    # -- the copy worker -------------------------------------------------------

    def _state_path(self, job):
        return os.path.join(self._state_dir,
                            f"{job.torrent_hash}-{job.file_index}.json")

    def _run_job(self, job):
        state_path = self._state_path(job)
        source = None
        destination = None

        try:
            resumed = self._resume_offset(job, state_path)
            if resumed is None:
                return

            free = shutil.disk_usage(os.path.dirname(job.dest_path) or '.').free
            if free < job.size - job.copied + 1024 ** 3:
                self._log.error(
                    f"❌ Not enough space for the live copy of "
                    f"'{os.path.basename(job.dest_path)}', skipping")
                job.state = 'no-space'
                return

            destination = open(job.dest_path, 'r+b' if resumed else 'wb')
            destination.seek(job.copied)

            while not job.stop.is_set():
                target = min(job.watermark, job.size)

                if target > job.copied:
                    if source is None:
                        source = self._open_source(job)
                        if source is None:
                            job.stop.wait(self._interval)
                            continue
                        source.seek(job.copied)

                    while job.copied < target and not job.stop.is_set():
                        chunk = source.read(min(self._chunk_size, target - job.copied))
                        if not chunk:
                            # The source is shorter than advertised - most likely
                            # qBittorrent moved it. Re-open by path next pass.
                            source.close()
                            source = None
                            break
                        destination.write(chunk)
                        job.copied += len(chunk)

                    destination.flush()
                    job.last_progress = time.monotonic()
                    self._write_state(job, state_path)

                if job.copied >= job.size:
                    job.state = 'complete'
                    break

                if time.monotonic() - job.last_progress > self._stall_timeout:
                    self._log.warning(
                        f"⚠️ Live copy of '{os.path.basename(job.dest_path)}' "
                        f"stalled, giving up (partial file kept)")
                    job.state = 'stalled'
                    break

                job.stop.wait(self._interval)

            if job.state == 'complete':
                # Re-open the source by path for a final read: qBittorrent may
                # have moved the file from download_path to save_path, and a
                # cross-filesystem move is a copy, not a rename.
                self._log.info(
                    f"✅ Live copy complete: {job.dest_path} "
                    f"({destination.tell()}/{job.size} bytes)")
                if destination.tell() != job.size:
                    self._log.warning(
                        f"⚠️ Live copy size mismatch for {job.dest_path}")
                _unlink(state_path)

        except OSError as e:
            self._log.error(f"❌ Live copy of {job.dest_path} failed: {e}")
            job.state = 'failed'
            if getattr(e, 'errno', None) == 28:  # ENOSPC
                _unlink(job.dest_path)
                _unlink(state_path)
        except Exception as e:
            self._log.error(f"❌ Live copy of {job.dest_path} failed: {e}")
            job.state = 'failed'
        finally:
            for handle in (source, destination):
                try:
                    if handle:
                        handle.close()
                except OSError:
                    pass
            self._jobs.pop(job.key, None)
            if job.state in ('failed', 'no-space', 'skipped', 'stalled'):
                self._skip(job.key)

    def _resume_offset(self, job, state_path):
        """Prepare the destination. Returns True if resuming, False if fresh,
        None if this file must not be touched."""
        os.makedirs(os.path.dirname(job.dest_path), exist_ok=True)

        state = _read_json(state_path)
        if state and state.get('dest') == job.dest_path and os.path.exists(job.dest_path):
            job.copied = min(int(state.get('copied', 0)), os.path.getsize(job.dest_path))
            with open(job.dest_path, 'r+b') as handle:
                handle.truncate(job.copied)
            self._log.info(
                f"↻ Resuming live copy of {job.dest_path} at {job.copied} bytes")
            return True

        if os.path.exists(job.dest_path):
            # Not ours. It may be a real, already-processed MKV-Auto output -
            # never clobber it.
            self._log.info(
                f"ℹ️ Destination already exists, skipping live copy: {job.dest_path}")
            job.state = 'skipped'
            return None

        job.copied = 0
        return False

    def _open_source(self, job):
        for candidate in job.sources:
            try:
                if os.path.isfile(candidate):
                    # buffering=0 is required, not an optimisation. A buffered
                    # reader reads ahead past the watermark into the sparse
                    # region, caches those zeroes in userspace, and serves them
                    # once qBittorrent has filled the range - silently writing
                    # a hole into the middle of the media file. We do our own
                    # chunking, so there is nothing to gain from the buffer.
                    return open(candidate, 'rb', buffering=0)
            except OSError:
                continue
        return None

    def _write_state(self, job, state_path):
        try:
            temp = state_path + '.tmp'
            with open(temp, 'w', encoding='utf-8') as handle:
                json.dump({'dest': job.dest_path, 'size': job.size,
                           'copied': job.copied}, handle)
            os.replace(temp, state_path)
        except OSError:
            pass

    # -- retirement ------------------------------------------------------------

    def _stop_jobs(self, torrent_hash, delete, reason):
        for key, job in list(self._jobs.items()):
            if key[0] != torrent_hash:
                continue
            job.stop.set()
            self._log.info(f"⏹️ Stopping live copy of {job.dest_path}: {reason}")
            if delete:
                _unlink(job.dest_path)
                _unlink(self._state_path(job))

    def _retire_vanished(self, live_hashes):
        """Drop jobs whose torrent is gone from qBittorrent entirely.

        A completed torrent leaves the "downloading" filter too, so only delete
        the partial once we are sure the torrent no longer exists - otherwise a
        finished download would have its live copy removed.
        """
        stale = {key[0] for key in self._jobs if key[0] not in live_hashes}
        for torrent_hash in stale:
            try:
                response = self._request("get", "/api/v2/torrents/info",
                                         params={"hashes": torrent_hash})
                still_exists = bool(response.json())
            except Exception:
                continue

            if still_exists:
                self._stop_jobs(torrent_hash, delete=False, reason="no longer downloading")
            else:
                self._stop_jobs(torrent_hash, delete=True, reason="torrent removed")


def _is_full_mode(answer):
    """Whether the answer came from a NORMALIZE_FILENAMES=full resolution.

    Only 'full'/'full-jf' populate episode titles, so full_info_found is the
    meaningful signal there; in the other modes it is always False.
    """
    return answer.get('normalize_filenames', '').lower() in ('full', 'full-jf')


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass
