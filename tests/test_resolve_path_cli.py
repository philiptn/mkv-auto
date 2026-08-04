"""Tests for the --resolve-path preview mode and the shared-folder resolve worker.

The preview must predict exactly what a real run produces: the qBittorrent
live-copy integration streams an in-progress download straight to the predicted
path, so a mismatch leaves two copies of the same media in the library. These
tests pin the rename stages the preview mirrors (modules/preview.py) and the
stdout/exit contract the worker parses.

Network-free: NORMALIZE_FILENAMES=simple never triggers a TVMaze lookup.
"""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import modules.file_operations as fo
from modules.file_operations import resolve_output_target
from modules.logger import setup_quiet_logger
from modules.preview import preview_output_target, preview_pipeline_filename

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGGER = setup_quiet_logger("mkv-auto.tests")


@pytest.fixture(autouse=True)
def naming_config():
    """Force deterministic, network-free naming and restore afterwards."""
    saved = {section: dict(fo.config[section])
             for section in ("general", "video", "media-encoder")}
    general = fo.config["general"]
    general["normalize_filenames"] = "simple"
    general["keep_original_file_structure"] = "fallback"
    general["make_season_folders"] = True
    general["file_tag"] = "default"
    # Pin the category folders too - the repo's own user.ini renames them.
    general["movies_folder"] = "Movies"
    general["movies_hdr_folder"] = "Movies (HDR)"
    general["tv_shows_folder"] = "TV Shows"
    general["tv_shows_hdr_folder"] = "TV Shows (HDR)"
    general["anime_folder"] = "Anime"
    general["others_folder"] = ""
    fo.config["media-encoder"]["enable_media_encoder"] = False
    fo.config["video"]["convert_dolby_vision_to_p8"] = False
    try:
        yield
    finally:
        for section, values in saved.items():
            fo.config[section].clear()
            fo.config[section].update(values)


def _preview(relative_path, output_folder="/out"):
    return preview_output_target(LOGGER, False, relative_path, output_folder)


def _name(original_name, relative_dir=""):
    return preview_pipeline_filename(LOGGER, False, original_name, relative_dir)


# --- stage 1: convert_all_videos_to_mkv ---------------------------------------

@pytest.mark.parametrize("extension", [
    ".mp4", ".avi", ".m4v", ".webm", ".ts", ".mov", ".wmv", ".flv",
])
def test_convertible_containers_become_mkv(extension):
    assert _name(f"Movie.Name.2020.1080p{extension}") == "Movie.Name.2020.1080p.mkv"


def test_uppercase_extension_is_converted_too():
    assert _name("Movie.Name.2020.1080p.MP4") == "Movie.Name.2020.1080p.mkv"


def test_mkv_and_non_video_are_left_alone():
    assert _name("Movie.Name.2020.1080p.mkv") == "Movie.Name.2020.1080p.mkv"
    assert _name("Movie.Name.2020.nfo") == "Movie.Name.2020.nfo"


# --- stage 3: fix_episodes_naming ---------------------------------------------

def test_season_episode_words_are_compacted():
    assert (_name("Show.Name.Season.1.Episode.2.1080p.mkv")
            == "Show.Name.S01E02.1080p.mkv")


def test_lowercase_season_episode_words_preserve_case():
    assert (_name("Show.Name.season.1.episode.2.1080p.mkv")
            == "Show.Name.s01e02.1080p.mkv")


# --- stage 4: FILE_TAG --------------------------------------------------------

def test_file_tag_replaces_release_group():
    fo.config["general"]["file_tag"] = "-MYTAG"
    assert _name("Movie.Name.2020.1080p-GROUP.mkv") == "Movie.Name.2020.1080p-MYTAG.mkv"


def test_default_file_tag_is_a_no_op():
    fo.config["general"]["file_tag"] = "default"
    assert _name("Movie.Name.2020.1080p-GROUP.mkv") == "Movie.Name.2020.1080p-GROUP.mkv"


# --- stage 5/6: encoder rename and Dolby Vision upgrade -----------------------

def test_encoder_rewrites_codec_and_strips_remux():
    fo.config["media-encoder"]["enable_media_encoder"] = True
    fo.config["media-encoder"]["output_codec"] = "h265"
    assert (_name("Movie.Name.2020.2160p.REMUX.HEVC.mkv")
            == "Movie.Name.2020.2160p.x265.mkv")


def test_encoder_promotes_dv_to_dv_hdr():
    fo.config["media-encoder"]["enable_media_encoder"] = True
    fo.config["media-encoder"]["output_codec"] = "h265"
    assert (_name("Movie.Name.2020.2160p.HEVC.DV.mkv")
            == "Movie.Name.2020.2160p.x265.DV.HDR.mkv")


def test_encoder_does_not_probe_dv_for_non_h265():
    fo.config["media-encoder"]["enable_media_encoder"] = True
    fo.config["media-encoder"]["output_codec"] = "av1"
    assert (_name("Movie.Name.2020.2160p.HEVC.DV.mkv")
            == "Movie.Name.2020.2160p.AV1.DV.mkv")


def test_dovi_conversion_upgrades_dv_to_dv_hdr():
    fo.config["video"]["convert_dolby_vision_to_p8"] = True
    assert _name("Movie.Name.2020.2160p.DV.mkv") == "Movie.Name.2020.2160p.DV.HDR.mkv"


def test_dovi_conversion_leaves_existing_dv_hdr_alone():
    fo.config["video"]["convert_dolby_vision_to_p8"] = True
    assert (_name("Movie.Name.2020.2160p.DV.HDR.mkv")
            == "Movie.Name.2020.2160p.DV.HDR.mkv")


def test_encoder_wins_over_dovi_conversion():
    # mkv_auto() only runs convert_dovi_files when the encoder is off.
    fo.config["media-encoder"]["enable_media_encoder"] = True
    fo.config["media-encoder"]["output_codec"] = "h264"
    fo.config["video"]["convert_dolby_vision_to_p8"] = True
    assert _name("Movie.Name.2020.1080p.DV.mkv") == "Movie.Name.2020.1080p.DV.mkv"


# --- the mirror must not drift into transforms the pipeline does not do -------

@pytest.mark.parametrize("original_name", [
    "Movie.Name.2020.2160p.DV.HDR.mkv",
    "Some.Show.S01E02.1080p.mkv",
    "Movie name (2020) - Featurette.mkv",
    "Unrecognisable release name.mkv",
])
def test_preview_matches_resolve_output_target_when_no_stage_applies(original_name):
    """With every rename stage disabled the preview must be a pure pass-through."""
    direct = resolve_output_target(
        LOGGER, False, original_name, "/out", "", original_name)
    previewed = _preview(original_name)
    assert previewed["previewed_name"] == original_name
    for key in ("output_folder", "restored_filename", "output_path", "media_name"):
        assert previewed[key] == direct[key]


# --- destination layout -------------------------------------------------------

def test_relative_path_joins_onto_the_output_root():
    result = _preview("Some.Show.S01E02.1080p.mkv", output_folder="/srv/media")
    assert result["output_path"] == os.path.join("/srv/media", result["relative_path"])


def test_season_folders_can_be_disabled():
    fo.config["general"]["make_season_folders"] = True
    assert "Season 1" in _preview("Some.Show.S01E02.1080p.mkv")["relative_path"]
    fo.config["general"]["make_season_folders"] = False
    assert "Season 1" not in _preview("Some.Show.S01E02.1080p.mkv")["relative_path"]


def test_keep_original_file_structure_true_passes_the_path_through():
    fo.config["general"]["keep_original_file_structure"] = "true"
    result = _preview("Pack.S01/Some.Show.S01E02.1080p.mkv")
    assert result["relative_path"] == os.path.join(
        "Pack.S01", "Some.Show.S01E02.1080p.mkv")


def test_keep_original_file_structure_false_categorises():
    fo.config["general"]["keep_original_file_structure"] = "false"
    result = _preview("Pack.S01/Some.Show.S01E02.1080p.mkv")
    assert result["relative_path"].startswith("TV Shows")
    assert "Pack.S01" not in result["relative_path"]


def test_fallback_keeps_the_subfolder_for_unrecognised_media():
    fo.config["general"]["keep_original_file_structure"] = "fallback"
    result = _preview("Some Folder/Unrecognisable release name.mkv")
    assert "Some Folder" in result["relative_path"]


def test_backslash_separators_are_normalised():
    fo.config["general"]["keep_original_file_structure"] = "true"
    result = _preview(r"Pack.S01\Some.Show.S01E02.1080p.mkv")
    assert result["relative_path"] == os.path.join(
        "Pack.S01", "Some.Show.S01E02.1080p.mkv")


@pytest.mark.parametrize("bad", ["", "   ", "Pack/"])
def test_empty_or_directory_paths_are_rejected(bad):
    with pytest.raises(ValueError):
        _preview(bad)


# --- the transport folder must survive a processing run -----------------------

def test_remove_empty_dirs_leaves_dot_directories_alone(tmp_path):
    """The resolve queue and the qBittorrent integration's '.<name>' staging
    directories live in the input folder. Deleting one mid-flight breaks the
    process writing it, so the sweep that runs after every move must skip them."""
    (tmp_path / "empty release folder").mkdir()
    (tmp_path / ".mkv-auto-resolve").mkdir()
    (tmp_path / ".Some.Torrent.Name").mkdir()
    (tmp_path / ".mkv-auto-resolve" / "nested").mkdir()

    fo.remove_empty_dirs(str(tmp_path))

    assert not (tmp_path / "empty release folder").exists()
    assert (tmp_path / ".mkv-auto-resolve").is_dir()
    assert (tmp_path / ".mkv-auto-resolve" / "nested").is_dir()
    assert (tmp_path / ".Some.Torrent.Name").is_dir()


def test_remove_empty_dirs_still_prunes_nested_media_folders(tmp_path):
    (tmp_path / "Pack" / "Season 1").mkdir(parents=True)
    fo.remove_empty_dirs(str(tmp_path))
    assert not (tmp_path / "Pack").exists()


# --- CLI contract -------------------------------------------------------------
#
# Runs mkv-auto.py as a subprocess. It imports the full subtitle toolchain, which
# the minimal test venv documented in run-tests.sh does not carry, so skip rather
# than fail when a dependency is absent.

def _run_cli(tmp_path, *argv, user_ini="NORMALIZE_FILENAMES = simple\n"):
    shutil.copy(os.path.join(REPO_ROOT, "defaults.ini"), tmp_path / "defaults.ini")
    (tmp_path / "user.ini").write_text("[general]\n" + user_ini, encoding="utf-8")
    # cwd is the tmp dir, not the repo: modules/misc.py reads defaults.ini and
    # user.ini relative to the CWD at import time, so the developer's own
    # user.ini would otherwise decide the answer.
    return subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "mkv-auto.py"), *argv],
        cwd=tmp_path, capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    probe = _run_cli(tmp_path_factory.mktemp("probe"), "--resolve-path", "a.mkv")
    if "ModuleNotFoundError" in probe.stderr:
        missing = probe.stderr.strip().splitlines()[-1]
        pytest.skip(f"mkv-auto.py dependencies unavailable: {missing}")
    return _run_cli


def test_cli_prints_one_clean_line(cli, tmp_path):
    result = cli(tmp_path, "--resolve-path", "Some.Show.S01E02.1080p.mkv", "--relative")
    assert result.returncode == 0
    assert len(result.stdout.splitlines()) == 1
    assert "\x1b" not in result.stdout
    assert result.stdout.strip().endswith("Some Show - S01E02.mkv")


def test_cli_stdout_stays_clean_with_debug(cli, tmp_path):
    result = cli(tmp_path, "--resolve-path", "Some.Show.S01E02.1080p.mkv",
                 "--relative", "--debug")
    assert result.returncode == 0
    assert len(result.stdout.splitlines()) == 1
    assert "\x1b" not in result.stdout


def test_cli_writes_no_log_files(cli, tmp_path):
    cli(tmp_path, "--resolve-path", "Some.Show.S01E02.1080p.mkv", "--relative")
    assert list(tmp_path.glob("*.log")) == []


def test_cli_json_carries_the_full_result(cli, tmp_path):
    result = cli(tmp_path, "--resolve-path", "Some.Show.S01E02.1080p.mkv",
                 "--relative", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    for key in ("output_path", "relative_path", "restored_filename",
                "media_name", "full_info_found", "is_extra", "previewed_name"):
        assert key in payload


def test_cli_relative_and_absolute_agree(cli, tmp_path):
    args = ("--resolve-path", "Some.Show.S01E02.1080p.mkv",
            "--output_folder", "/srv/media")
    absolute = cli(tmp_path, *args).stdout.strip()
    relative = cli(tmp_path, *args, "--relative").stdout.strip()
    assert absolute == os.path.join("/srv/media", relative)


def test_cli_handles_spaces_and_quotes(cli, tmp_path):
    result = cli(tmp_path, "--resolve-path", 'A file with spaces.mkv', "--relative")
    assert result.returncode == 0
    assert "A file with spaces.mkv" in result.stdout


def test_cli_fails_loudly_with_empty_stdout(cli, tmp_path):
    result = cli(tmp_path, "--resolve-path", "", "--relative")
    assert result.returncode != 0
    assert result.stdout == ""


def test_cli_without_resolve_path_still_runs_the_pipeline(cli, tmp_path):
    """Guards the dispatch change in main()."""
    (tmp_path / "input").mkdir()
    result = cli(tmp_path, "--input_folder", str(tmp_path / "input"))
    assert result.returncode == 0
    assert "No media files found" in result.stdout


# --- resolve worker protocol --------------------------------------------------
#
# Driven against a stub mkv-auto.py so the protocol is covered without the
# subtitle toolchain. The real resolver is covered by the CLI tests above.

STUB_MKV_AUTO = '''\
import json, sys
path = sys.argv[sys.argv.index("--resolve-path") + 1]
if path == "boom.mkv":
    sys.stderr.write("[ERROR] simulated failure\\n")
    sys.exit(1)
sys.stdout.write(json.dumps({"relative_path": "Movies/" + path}) + "\\n")
'''


@pytest.fixture
def worker(tmp_path):
    """Start resolve-worker.py against a stub resolver; yield its queue dir."""
    fake_root = tmp_path / "mkv-auto"
    fake_root.mkdir()
    (fake_root / "mkv-auto.py").write_text(STUB_MKV_AUTO, encoding="utf-8")
    queue_dir = tmp_path / "queue"

    process = subprocess.Popen(
        [sys.executable, os.path.join(REPO_ROOT, "resolve-worker.py")],
        env={**os.environ,
             "MKV_AUTO_DIR": str(fake_root),
             "RESOLVE_QUEUE_DIR": str(queue_dir),
             "RESOLVE_POLL_INTERVAL": "0.1"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield queue_dir
    finally:
        process.terminate()
        process.wait(timeout=10)


def _ask(queue_dir, uuid, path, timeout=15):
    """Publish a request by rename and wait for the answer."""
    for _ in range(int(timeout / 0.1)):
        if queue_dir.is_dir():
            break
        time.sleep(0.1)

    tmp = queue_dir / f"{uuid}.req.tmp"
    tmp.write_text(json.dumps({"v": 1, "path": path}), encoding="utf-8")
    os.rename(tmp, queue_dir / f"{uuid}.req")

    response = queue_dir / f"{uuid}.res"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if response.name in os.listdir(queue_dir):
            return json.loads(response.read_text(encoding="utf-8"))
        time.sleep(0.1)
    raise AssertionError(f"no response for {uuid} within {timeout}s")


def test_worker_answers_a_request(worker):
    assert _ask(worker, "aaa", "Some.mkv") == {
        "ok": True, "relative_path": "Movies/Some.mkv"}


def test_worker_consumes_the_request(worker):
    _ask(worker, "aaa", "Some.mkv")
    assert not (worker / "aaa.req").exists()


@pytest.mark.parametrize("path", [
    "../../etc/passwd", "/etc/passwd", "Pack/../../escape.mkv", "",
])
def test_worker_rejects_paths_that_escape_the_input_root(worker, path):
    answer = _ask(worker, "bad", path)
    assert answer["ok"] is False
    assert "invalid path" in answer["error"]


def test_worker_reports_resolver_failure(worker):
    answer = _ask(worker, "err", "boom.mkv")
    assert answer["ok"] is False
    assert "simulated failure" in answer["error"]


def test_worker_recreates_a_deleted_queue_dir(worker):
    """The worker must recover if the queue is removed underneath it."""
    _ask(worker, "aaa", "Some.mkv")
    shutil.rmtree(worker)
    assert _ask(worker, "bbb", "Other.mkv")["ok"] is True
