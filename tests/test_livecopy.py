"""Tests for the qBittorrent live-copy piece arithmetic and file filter.

contiguous_prefix() decides how many bytes of a still-downloading file are safe
to read. Over-reporting by even one byte copies not-yet-downloaded zeroes into
the media library, so these tests care most about the "never over-reports"
property at every boundary.

Pure functions only - no network, no qBittorrent, no disk.
"""

import importlib.util
import logging
import os
import time

import pytest

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "service", "integrations", "qbittorrent-automation", "livecopy.py")

_spec = importlib.util.spec_from_file_location("livecopy", MODULE_PATH)
livecopy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(livecopy)

contiguous_prefix = livecopy.contiguous_prefix
is_live_copyable = livecopy.is_live_copyable
split_torrent_relative = livecopy.split_torrent_relative

PIECE = 1024  # small piece size keeps the expected values readable


def make_files(sizes, piece_size=PIECE):
    """Build a file list with piece_range values a real torrent would report."""
    files = []
    offset = 0
    for index, size in enumerate(sizes):
        files.append({
            'index': index,
            'name': f"file{index}.mkv",
            'size': size,
            'progress': 0.0,
            'priority': 1,
            'piece_range': [offset // piece_size, (offset + size - 1) // piece_size],
        })
        offset += size
    return files


def states(total, downloaded_through):
    """Piece states where pieces [0, downloaded_through) are complete."""
    return [livecopy.PIECE_DOWNLOADED if p < downloaded_through
            else livecopy.PIECE_NOT_DOWNLOADED for p in range(total)]


# --- single-file torrents -----------------------------------------------------

def test_nothing_downloaded_yields_nothing():
    files = make_files([10 * PIECE])
    assert contiguous_prefix(files[0], 0, files, PIECE, states(10, 0)) == 0


def test_prefix_grows_with_completed_pieces():
    files = make_files([10 * PIECE])
    assert contiguous_prefix(files[0], 0, files, PIECE, states(10, 3)) == 3 * PIECE


def test_all_pieces_complete_yields_the_whole_file():
    files = make_files([10 * PIECE])
    assert contiguous_prefix(files[0], 0, files, PIECE, states(10, 10)) == 10 * PIECE


def test_a_gap_stops_the_prefix_at_the_first_hole():
    """Sequential download is a hint, not a guarantee - a later piece may land
    first, and it must not be counted."""
    files = make_files([10 * PIECE])
    piece_states = states(10, 3)
    piece_states[7] = livecopy.PIECE_DOWNLOADED
    assert contiguous_prefix(files[0], 0, files, PIECE, piece_states) == 3 * PIECE


def test_a_piece_still_downloading_is_not_trusted():
    files = make_files([10 * PIECE])
    piece_states = states(10, 5)
    piece_states[3] = livecopy.PIECE_DOWNLOADING
    assert contiguous_prefix(files[0], 0, files, PIECE, piece_states) == 3 * PIECE


def test_short_final_piece_is_never_extrapolated():
    """The torrent's last piece is short; piece math would over-report."""
    size = 9 * PIECE + 300
    files = make_files([size])
    assert contiguous_prefix(files[0], 0, files, PIECE, states(10, 10)) == size


def test_reported_completion_short_circuits_the_piece_math():
    files = make_files([9 * PIECE + 300])
    files[0]['progress'] = 1.0
    # Deliberately inconsistent piece states - progress must win.
    assert (contiguous_prefix(files[0], 0, files, PIECE, states(10, 0))
            == files[0]['size'])


# --- multi-file torrents ------------------------------------------------------

def test_second_file_offset_is_accounted_for():
    """A season pack: file 1 starts at a piece boundary 4 pieces in."""
    files = make_files([4 * PIECE, 4 * PIECE])
    assert contiguous_prefix(files[1], 1, files, PIECE, states(8, 6)) == 2 * PIECE


def test_second_file_reports_nothing_before_its_own_pieces_land():
    files = make_files([4 * PIECE, 4 * PIECE])
    assert contiguous_prefix(files[1], 1, files, PIECE, states(8, 4)) == 0


def test_file_starting_mid_piece_is_handled_conservatively():
    """File 1 starts inside piece 3, which file 0 also occupies."""
    files = make_files([3 * PIECE + 500, 4 * PIECE])
    result = contiguous_prefix(files[1], 1, files, PIECE, states(8, 6))
    # Piece 6 is the first bad one; file 1 begins 500 bytes into piece 3, so
    # bytes up to the start of piece 6 are safe.
    assert result == 6 * PIECE - (3 * PIECE + 500)
    assert result <= files[1]['size']


def test_last_file_of_the_torrent_uses_its_real_size():
    files = make_files([4 * PIECE, 2 * PIECE + 111])
    assert contiguous_prefix(files[1], 1, files, PIECE, states(7, 7)) == 2 * PIECE + 111


def test_pad_files_do_not_cause_over_reporting():
    """libtorrent pad files are not in the API's file list, so the running sum
    of preceding sizes comes out short. The conservative branch must engage."""
    files = make_files([4 * PIECE, 4 * PIECE])
    # Simulate a hidden 2-piece pad: file 1 really starts at piece 6, not 4.
    files[1]['piece_range'] = [6, 9]
    result = contiguous_prefix(files[1], 1, files, PIECE, states(10, 8))
    assert result == PIECE                       # (8 - 6 - 1) * PIECE
    assert result <= 2 * PIECE                   # strictly less than the truth


# --- guards -------------------------------------------------------------------

def test_deselected_files_are_never_copied():
    files = make_files([10 * PIECE])
    files[0]['priority'] = 0
    assert contiguous_prefix(files[0], 0, files, PIECE, states(10, 10)) == 0


def test_unsettled_metadata_yields_nothing():
    files = make_files([10 * PIECE])
    assert contiguous_prefix(files[0], 0, files, PIECE, states(4, 4)) == 0   # hi >= len
    assert contiguous_prefix(files[0], 0, files, 0, states(10, 10)) == 0     # no piece size
    assert contiguous_prefix(files[0], 0, files, PIECE, []) == 0             # no states


def test_missing_piece_range_yields_nothing():
    files = make_files([10 * PIECE])
    del files[0]['piece_range']
    assert contiguous_prefix(files[0], 0, files, PIECE, states(10, 10)) == 0


@pytest.mark.parametrize("downloaded_through", range(0, 11))
def test_never_over_reports_at_any_progress(downloaded_through):
    """The whole safety property, swept across a mid-piece file boundary."""
    files = make_files([3 * PIECE + 500, 5 * PIECE + 200])
    piece_states = states(9, downloaded_through)
    for index, entry in enumerate(files):
        result = contiguous_prefix(entry, index, files, PIECE, piece_states)
        assert 0 <= result <= entry['size']
        # Every returned byte must be backed by a completed piece.
        if result > 0:
            offset = sum(f['size'] for f in files if f['index'] < index)
            last_byte = offset + result - 1
            assert all(piece_states[p] == livecopy.PIECE_DOWNLOADED
                       for p in range(entry['piece_range'][0],
                                      last_byte // PIECE + 1))


# --- file name handling -------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Pack/ep.mkv", ["Pack", "ep.mkv"]),
    (r"Pack\Season 1\ep.mkv", ["Pack", "Season 1", "ep.mkv"]),
    ("ep.mkv", ["ep.mkv"]),
    ("Pack//ep.mkv", ["Pack", "ep.mkv"]),
])
def test_torrent_relative_names_split_on_both_separators(name, expected):
    assert split_torrent_relative(name) == expected


@pytest.mark.parametrize("path,expected", [
    # A Windows qBittorrent sends backslashes, which os.path.dirname on Linux
    # does not split on at all.
    (r"Z:\torrents\Pack", r"Z:\torrents"),
    (r"\\server\share\Pack", r"\\server\share"),
    ("/media/share/torrents/Pack", "/media/share/torrents"),
    ("Pack", None),
    ("", None),
])
def test_parent_path_handles_both_separators(path, expected):
    assert livecopy.parent_path(path) == expected


def test_windows_content_path_yields_a_usable_root():
    """Regression: dirname() left the content_path root empty, so the fallback
    silently produced candidates rooted at the current directory."""
    manager = livecopy.LiveCopyManager.__new__(livecopy.LiveCopyManager)
    manager._translated = {}
    manager._translate = lambda path, mappings: path.replace("\\", "/")

    candidates = manager._source_candidates(
        {"save_path": r"Z:\torrents", "content_path": r"Z:\torrents\Pack"},
        {"name": r"Pack\ep.mkv"}, {})

    assert candidates
    assert all(c.startswith("Z:/torrents/") for c in candidates), candidates


def test_translation_is_cached_across_ticks():
    """tick() re-derives every job's sources each pass; without a cache that
    repeats the work and spams a 'Translated ...' line every interval."""
    manager = livecopy.LiveCopyManager.__new__(livecopy.LiveCopyManager)
    manager._translated = {}
    seen = []

    def translate(path, mappings):
        seen.append(path)
        return path

    manager._translate = translate
    torrent = {"save_path": "/dl", "download_path": "/incomplete",
               "content_path": "/dl/Pack"}
    entry = {"name": "Pack/ep.mkv"}

    for _ in range(5):
        manager._source_candidates(torrent, entry, {})
    # Three roots, but content_path's parent is save_path, so two distinct ones.
    assert sorted(seen) == ["/dl", "/incomplete"]

    # A changed mapping table must not be served from the cache.
    manager._source_candidates(torrent, entry, {"a": "b"})
    assert sorted(seen) == ["/dl", "/dl", "/incomplete", "/incomplete"]


EXTENSIONS = ('.mkv', '.mp4')
BIG = 500 * 1024 ** 2


def test_media_files_over_the_size_floor_are_eligible():
    assert is_live_copyable("Show.S01E01.mkv", 100, BIG, EXTENSIONS)


def test_non_media_and_small_files_are_skipped():
    assert not is_live_copyable("Show.S01E01.nfo", 100, BIG, EXTENSIONS)
    assert not is_live_copyable("Show.S01E01.mkv", BIG, 1024, EXTENSIONS)


@pytest.mark.parametrize("basename", [
    "sample.mkv", "Show.S01E01-sample.mkv", "Show.S01E01.sample.mkv",
])
def test_samples_are_skipped(basename):
    """mkv-auto deletes these, so a live copy would be orphaned."""
    assert not is_live_copyable(basename, 100, BIG, EXTENSIONS)


@pytest.mark.parametrize("basename", [
    "Movie (2020)-behindthescenes.mkv", "Movie (2020)-featurette.mkv",
    "Movie (2020)-trailer.mkv", "Movie (2020)-deleted.mkv",
])
def test_extras_are_skipped(basename):
    """process_extras() renames these using the sibling file list, which the
    path preview cannot model."""
    assert not is_live_copyable(basename, 100, BIG, EXTENSIONS)


# --- the tail copy, end to end ------------------------------------------------

PIECE_SIZE = 32 * 1024
PIECE_COUNT = 16
FILE_SIZE = PIECE_SIZE * PIECE_COUNT


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200
        self.text = ""

    def json(self):
        return self.payload


class _FakeQBittorrent:
    """A torrent whose pieces land one at a time, in order."""

    def __init__(self, source_path, data):
        self.source = source_path
        self.data = data
        self.done = 0
        self.toggles = {'seq': 0, 'flp': 0}

    def advance(self):
        """Write the next piece's real bytes and mark it downloaded."""
        if self.done >= PIECE_COUNT:
            return False
        start = self.done * PIECE_SIZE
        with open(self.source, 'r+b') as handle:
            handle.seek(start)
            handle.write(self.data[start:start + PIECE_SIZE])
        self.done += 1
        return True

    def _torrent(self):
        return {
            'hash': 'abc123', 'name': 'Show.S01E01.1080p.mkv',
            'save_path': os.path.dirname(self.source),
            'content_path': self.source, 'state': 'downloading',
            'seq_dl': self.toggles['seq'] > 0,
            'f_l_piece_prio': self.toggles['flp'] > 0,
            'tags': 'mkv-auto', 'progress': self.done / PIECE_COUNT,
        }

    def request(self, method, endpoint, **kwargs):
        if endpoint == "/api/v2/torrents/info":
            return _Response([self._torrent()])
        if endpoint == "/api/v2/torrents/files":
            return _Response([{
                'index': 0, 'name': 'Show.S01E01.1080p.mkv', 'size': FILE_SIZE,
                'progress': self.done / PIECE_COUNT, 'priority': 1,
                'piece_range': [0, PIECE_COUNT - 1],
            }])
        if endpoint == "/api/v2/torrents/properties":
            return _Response({'piece_size': PIECE_SIZE, 'pieces_num': PIECE_COUNT})
        if endpoint == "/api/v2/torrents/pieceStates":
            return _Response([livecopy.PIECE_DOWNLOADED if p < self.done
                              else livecopy.PIECE_NOT_DOWNLOADED
                              for p in range(PIECE_COUNT)])
        if endpoint.endswith("toggleSequentialDownload"):
            self.toggles['seq'] += 1
            return _Response("Ok.")
        if endpoint.endswith("toggleFirstLastPiecePrio"):
            self.toggles['flp'] += 1
            return _Response("Ok.")
        raise AssertionError(f"unexpected endpoint {endpoint}")


class _FakeResolver:
    relative = "TV Shows/Show (2019)/Season 1/Show (2019) - S01E01.mkv"

    def relative_output_path(self, tag, relative_path):
        return {'ok': True, 'relative_path': self.relative,
                'full_info_found': True, 'normalize_filenames': 'full'}


@pytest.fixture
def live_copy(tmp_path):
    """A manager wired to a fake qBittorrent with a sparse in-progress file."""
    downloads = tmp_path / "downloads"
    output = tmp_path / "output"
    downloads.mkdir()
    output.mkdir()

    data = os.urandom(FILE_SIZE)
    # ".!qB" is what qBittorrent leaves while the file is incomplete.
    source = downloads / "Show.S01E01.1080p.mkv.!qB"
    with open(source, 'wb') as handle:
        handle.truncate(FILE_SIZE)

    qbittorrent = _FakeQBittorrent(str(source), data)
    manager = livecopy.LiveCopyManager(
        qbittorrent.request, {'mkv-auto': str(output)}, _FakeResolver(),
        logging.getLogger("livecopy-test"), str(tmp_path / "state"),
        interval=0, max_workers=2, min_size=1024, extensions=('.mkv',),
        chunk_size=8 * 1024, stall_timeout=30,
    )
    try:
        yield manager, qbittorrent, data, output / _FakeResolver.relative, source
    finally:
        manager.shutdown()


def _drain(manager, qbittorrent, destination, timeout=30):
    """Advance the download and tick until the destination is complete."""
    sizes = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qbittorrent.advance()
        manager.tick({})
        if destination.exists():
            size = destination.stat().st_size
            if not sizes or sizes[-1] != size:
                sizes.append(size)
            if size >= FILE_SIZE:
                return sizes
        time.sleep(0.05)
    raise AssertionError(f"live copy did not complete; sizes seen: {sizes}")


def test_live_copy_reproduces_the_source_exactly(live_copy):
    manager, qbittorrent, data, destination, _ = live_copy
    _drain(manager, qbittorrent, destination)
    assert destination.read_bytes() == data


def test_live_copy_grows_while_downloading(live_copy):
    """The whole point: the file is watchable before the torrent finishes."""
    manager, qbittorrent, _, destination, _ = live_copy
    sizes = _drain(manager, qbittorrent, destination)
    assert len(sizes) > 2, f"destination appeared all at once: {sizes}"
    assert sizes[-1] == FILE_SIZE


def test_live_copy_survives_the_incomplete_suffix_being_stripped(live_copy):
    """qBittorrent renames name.mkv.!qB -> name.mkv on completion."""
    manager, qbittorrent, data, destination, source = live_copy
    final = source.with_suffix('')                       # drop ".!qB"

    deadline = time.monotonic() + 30
    renamed = False
    while time.monotonic() < deadline:
        qbittorrent.advance()
        if not renamed and qbittorrent.done > PIECE_COUNT // 2:
            os.rename(source, final)
            qbittorrent.source = str(final)
            renamed = True
        manager.tick({})
        if destination.exists() and destination.stat().st_size >= FILE_SIZE:
            break
        time.sleep(0.05)

    assert renamed
    assert destination.read_bytes() == data


def test_sequential_download_is_enabled_exactly_once(live_copy):
    """The API only offers toggles, so a missing read-back would flap."""
    manager, qbittorrent, _, destination, _ = live_copy
    _drain(manager, qbittorrent, destination)
    assert qbittorrent.toggles['seq'] == 1
    assert qbittorrent.toggles['flp'] == 1


def test_an_existing_destination_is_never_clobbered(live_copy):
    """It may be a real, already-processed MKV-Auto output."""
    manager, qbittorrent, _, destination, _ = live_copy
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"already processed by MKV-Auto")

    for _ in range(20):
        qbittorrent.advance()
        manager.tick({})
        time.sleep(0.05)

    assert destination.read_bytes() == b"already processed by MKV-Auto"


# --- folder map configuration -------------------------------------------------
#
# docker-compose cannot read the JSON tag maps to build its volume mounts, so
# the folders must be named there as well. Expanding variables in the values
# lets a path be written once instead of being repeated and drifting.

AUTOMATION_PATH = os.path.join(os.path.dirname(MODULE_PATH), "qbittorrent-automation.py")


def _load_automation(monkeypatch, env):
    for key in list(os.environ):
        if key.startswith(("TARGETS", "LIVE_COPY", "MKV_AUTO")):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    monkeypatch.syspath_prepend(os.path.dirname(AUTOMATION_PATH))
    spec = importlib.util.spec_from_file_location("qbittorrent_automation",
                                                  AUTOMATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_tag_carries_its_own_input_and_output(monkeypatch):
    """Several MKV-Auto instances, each live copying to its own output folder."""
    module = _load_automation(monkeypatch, {
        "TARGETS": '{"mkv-auto": {"input": "/srv/mkv/input",'
                   '              "output": "/srv/mkv/output/"},'
                   ' "H.265":    {"input": "/srv/enc/input",'
                   '              "output": "/srv/enc/output"}}',
    })
    assert module.TARGETS == {
        "mkv-auto": {"input": "/srv/mkv/input", "output": "/srv/mkv/output"},
        "H.265": {"input": "/srv/enc/input", "output": "/srv/enc/output"},
    }
    assert module.resolve_queue_dir("mkv-auto") == "/srv/mkv/input/.mkv-auto-resolve"
    assert module.resolve_queue_dir("H.265") == "/srv/enc/input/.mkv-auto-resolve"


def test_a_bare_string_is_still_accepted_as_the_input_folder(monkeypatch):
    """The original format, which existing deployments have in their .env."""
    module = _load_automation(monkeypatch, {
        "TARGETS": '{"mkv-auto": "/plain/input", "H.265": "/other/input"}',
    })
    assert module.TARGETS == {
        "mkv-auto": {"input": "/plain/input", "output": None},
        "H.265": {"input": "/other/input", "output": None},
    }


def test_output_is_optional_per_tag(monkeypatch):
    """Live copy is opt-in per instance: no output folder, no live copy."""
    module = _load_automation(monkeypatch, {
        "TARGETS": '{"live": {"input": "/a/input", "output": "/a/output"},'
                   ' "plain": {"input": "/b/input"}}',
    })
    assert module.TARGETS["live"]["output"] == "/a/output"
    assert module.TARGETS["plain"]["output"] is None


def test_an_output_that_is_also_an_input_is_refused(monkeypatch):
    """It would be picked up as new media and reprocessed forever."""
    module = _load_automation(monkeypatch, {
        "TARGETS": '{"mkv-auto": {"input": "/same", "output": "/same"}}',
    })
    assert module.TARGETS["mkv-auto"]["output"] is None


def test_an_output_matching_another_tags_input_is_refused(monkeypatch, tmp_path):
    """Same loop, one instance removed - only caught once all tags are known."""
    module = _load_automation(monkeypatch, {
        "LIVE_COPY": "true",
        "TARGETS": '{"a": {"input": "/in/a", "output": "/in/b"},'
                   ' "b": {"input": "/in/b"}}',
    })
    assert module.build_live_copy_manager() is None


def test_a_tag_without_an_input_folder_is_dropped(monkeypatch):
    module = _load_automation(monkeypatch, {
        "TARGETS": '{"good": "/plain/input", "bad": {"output": "/only/output"}}',
    })
    assert list(module.TARGETS) == ["good"]


def test_malformed_targets_do_not_crash_startup(monkeypatch):
    assert _load_automation(monkeypatch, {"TARGETS": "not json at all"}).TARGETS == {}
    assert _load_automation(monkeypatch, {"TARGETS": '{"tag": 42}'}).TARGETS == {}


# --- login ---------------------------------------------------------------------

class _LoginResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300


def _login_with(monkeypatch, response):
    module = _load_automation(monkeypatch, {"TARGETS": '{"t": "/in"}'})
    monkeypatch.setattr(module.session, "post", lambda *a, **k: response)
    return module


def test_login_accepts_204_from_qbittorrent_5_2(monkeypatch):
    """5.2.0 answers a successful login with 204 No Content, not 200 'Ok.'."""
    module = _login_with(monkeypatch, _LoginResponse(204))
    module.login()


def test_login_accepts_200_ok_from_older_qbittorrent(monkeypatch):
    module = _login_with(monkeypatch, _LoginResponse(200, "Ok."))
    module.login()


def test_login_rejects_bad_credentials(monkeypatch):
    """Wrong credentials are still 200, with the body 'Fails.'."""
    module = _login_with(monkeypatch, _LoginResponse(200, "Fails."))
    with pytest.raises(Exception, match="Fails."):
        module.login()


def test_login_reports_being_banned(monkeypatch):
    module = _login_with(monkeypatch, _LoginResponse(403))
    with pytest.raises(Exception, match="Banned"):
        module.login()


def test_login_rejects_other_errors(monkeypatch):
    module = _login_with(monkeypatch, _LoginResponse(500, "boom"))
    with pytest.raises(Exception, match="HTTP 500"):
        module.login()


def test_live_copy_covers_every_tag_that_defines_an_output(monkeypatch, tmp_path):
    module = _load_automation(monkeypatch, {
        "LIVE_COPY": "true",
        "TARGETS": f'{{"a": {{"input": "{tmp_path}/a/in", "output": "{tmp_path}/a/out"}},'
                   f' "b": {{"input": "{tmp_path}/b/in", "output": "{tmp_path}/b/out"}},'
                   f' "c": {{"input": "{tmp_path}/c/in"}}}}',
        "LIVE_COPY_STATE_DIR": str(tmp_path / "state"),
    })
    manager = module.build_live_copy_manager()
    try:
        assert sorted(manager._outputs) == ["a", "b"]
        assert manager._outputs["b"] == f"{tmp_path}/b/out"
    finally:
        manager.shutdown()
