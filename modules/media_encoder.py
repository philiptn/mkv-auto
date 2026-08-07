import os
import subprocess
import sys
import re
import shutil
import platform
import time
import uuid
import math
import json
import threading
from typing import List, Union
from queue import Queue
import concurrent.futures

from modules.misc import *
from modules.file_operations import resolve_output_target, move_resolved_to_output
from modules.mkv import move_files_to_output_process_worker, print_arr_summary
from modules.encode_estimator import (
    EncodeEstimator,
    MAX_SAMPLES_PER_FILE,
    SAMPLE_MAX_WALL,
    SAMPLE_MIN_SECONDS,
    SAMPLE_MIN_SOURCE,
    SAMPLE_OFFSETS,
    SAMPLE_VIDEO_SECONDS,
    SCHED_POLL_INTERVAL,
)


def encode_with_worker_id(
    logger,
    debug,
    input_file,
    dirpath,
    per_file_cpu,
    progress,
    worker_id_pool,
    estimator=None,
    file_index=None
):
    worker_id = worker_id_pool.get()
    try:
        return encode_single_video_file(
            logger,
            debug,
            input_file,
            dirpath,
            per_file_cpu,
            progress,
            worker_id,
            estimator=estimator,
            file_index=file_index
        )
    finally:
        worker_id_pool.put(worker_id)


def resolve_quality_crf(
    quality_crf_str: str,
    input_files: List[str],
    dirpath: str
) -> str:
    parts = quality_crf_str.split(":", 1)
    base_crf = str(parts[0])

    overrides = {}
    if len(parts) == 2:
        for entry in parts[1].split(","):
            res, crf = entry.split("-", 1)
            overrides[int(res)] = int(crf)

    def get_width(filepath: str) -> int:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width",
            "-of", "json",
            filepath
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(result.stdout)
        return int(data["streams"][0]["width"])

    resolved_crfs = set()

    for filename in input_files:
        full_path = f"{dirpath.rstrip('/')}/{filename}"
        width = get_width(full_path)

        crf = overrides.get(width, base_crf)
        resolved_crfs.add(str(crf))

        if len(resolved_crfs) > 1:
            return "MiXED"

    return resolved_crfs.pop() if resolved_crfs else base_crf


def get_video_dimensions(filename):
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
           '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', filename]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"Error getting video dimensions for {filename}: {result.stderr}")
        return None, None
    try:
        # Strip any trailing 'x' and whitespace
        output = result.stdout.strip().rstrip('x')
        width, height = map(int, output.split('x'))
        return width, height
    except ValueError:
        print(f"Error parsing video dimensions for {filename}: {result.stdout}")
        return None, None
    

def get_video_duration(path):
    return probe_duration_seconds(path)


def _tag_lookup(tags, key):
    # Matroska statistics tags are per-track and often carry a language suffix
    # (BPS-eng, NUMBER_OF_BYTES-eng), so match the bare key or any suffixed form.
    if not tags:
        return None
    prefix = key.lower() + "-"
    for name, value in tags.items():
        lowered = name.lower()
        if lowered == key.lower() or lowered.startswith(prefix):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def get_video_stream_bytes(logger, path, duration, total_size):
    """Size of the source video stream alone, in bytes, or None if undeterminable.

    Savings are reported on a video-only basis: the encoder only re-encodes video,
    while audio/subtitles/attachments are remuxed through untouched and occupy the
    same space before and after. Comparing against the whole container would credit
    the encoder for bytes it never touched.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-of", "json",
        path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        streams = json.loads(result.stdout).get("streams", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
        log_debug(logger, f"[MEDIA-ENCODER] Video stream size probe failed for {path}: {e}")
        return None

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return None

    def clamp(value, source):
        size = int(max(0, min(value, total_size)))
        log_debug(logger, f"[MEDIA-ENCODER] Source video stream size ({source}): "
                          f"{size} of {total_size} bytes")
        return size

    # Exact, when mkvmerge wrote its track statistics tags.
    exact = _tag_lookup(video.get("tags"), "NUMBER_OF_BYTES")
    if exact:
        return clamp(exact, "NUMBER_OF_BYTES tag")

    if duration > 0:
        video_bps = _tag_lookup(video.get("tags"), "BPS")
        if not video_bps:
            try:
                video_bps = int(video.get("bit_rate"))
            except (TypeError, ValueError):
                video_bps = None
        if video_bps:
            return clamp(video_bps * duration / 8, "video bitrate")

        # Complement: everything the encoder will not touch, subtracted from the file.
        other_bps = 0
        for stream in streams:
            if stream.get("codec_type") == "video":
                continue
            bps = _tag_lookup(stream.get("tags"), "BPS")
            if not bps:
                try:
                    bps = int(stream.get("bit_rate"))
                except (TypeError, ValueError):
                    bps = None
            if bps:
                other_bps += bps
        if other_bps:
            return clamp(total_size - (other_bps * duration / 8), "container complement")

    log_debug(logger, f"[MEDIA-ENCODER] Could not determine video stream size for {path}, "
                      f"falling back to full container size")
    return None


def auto_crop(file):
    try:
        hb_output = subprocess.check_output(
            f'HandBrakeCLI -i "{file}" --scan -t 0',
            stderr=subprocess.STDOUT,
            shell=True
        ).decode()

        autocrop_str = re.search(r"\+ autocrop: (.+)", hb_output).group(1)
        top, bottom, left, right = map(int, autocrop_str.split('/'))

        # Get max values for vertical and horizontal crop
        vertical_crop = max(top, bottom)
        horizontal_crop = max(left, right)

        # Round up to nearest multiple of 4
        vertical_crop = int(math.ceil(vertical_crop / 4.0)) * 4
        horizontal_crop = int(math.ceil(horizontal_crop / 4.0)) * 4

        top = bottom = vertical_crop
        left = right = horizontal_crop

        return f"{left},{right},{top},{bottom}"

    except Exception as e:
        return "0,0,0,0"


def calculate_output_dimensions(cropped_width, cropped_height, desired_ar):
    scale = False
    # First, try to fix output width as cropped_width
    output_width = cropped_width
    output_height = int(round(output_width / desired_ar))
    if output_height >= cropped_height:
        # Need to pad top and bottom
        pad_left = 0
        pad_right = 0
        pad_top = int((output_height - cropped_height) / 2)
        pad_bottom = output_height - cropped_height - pad_top
    else:
        # Try to fix output height as cropped_height
        output_height = cropped_height
        output_width = int(round(output_height * desired_ar))
        if output_width >= cropped_width:
            # Need to pad left and right
            pad_top = 0
            pad_bottom = 0
            pad_left = int((output_width - cropped_width) / 2)
            pad_right = output_width - cropped_width - pad_left
        else:
            # Output dimensions are smaller than cropped dimensions
            # Need to scale down the video
            scale = True
            output_width = int(round(min(cropped_width, output_width)))
            output_height = int(round(min(cropped_height, output_height)))
            pad_left = 0
            pad_right = 0
            pad_top = 0
            pad_bottom = 0
    return output_width, output_height, pad_left, pad_right, pad_top, pad_bottom, scale


def detect_dolby_vision(input_file, logger=None):
    """
    Detect Dolby Vision using MediaInfo.
    Returns: (is_dovi: bool, dv_profile: str or None)
    """
    try:
        cmd = [
            'mediainfo',
            '--Output=JSON',
            input_file
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        data = json.loads(result.stdout)

        tracks = data.get("media", {}).get("track", [])

        for track in tracks:
            if track.get("@type") != "Video":
                continue

            hdr_format = track.get("HDR_Format", "")
            hdr_format_string = track.get("HDR_Format_String", "")
            hdr_format_profile = track.get("HDR_Format_Profile", "")

            combined = f"{hdr_format} {hdr_format_string}".lower()

            if "dolby vision" in combined:
                profile = hdr_format_profile or None
                return True, profile

        return False, None

    except Exception as e:
        return False, None


def extract_rpu(input_file, crop_rpu, rpu_file, logger, work_dir):
    """
    Robust Dolby Vision preparation.

    For Profile 7:
        mkvextract → dovi_tool convert (P8 BL+RPU)
        then extract RPU from the converted stream

    For Profile 8 and Profile 5:
        extract directly

    Returns path to RPU or None.
    """
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    temp_raw = os.path.join(work_dir, f"{base_name}_source.hevc")
    temp_p8 = os.path.join(work_dir, f"{base_name}_p8.hevc")

    # -------------------------------------------------
    # Step 1: Detect DV profile
    # -------------------------------------------------
    is_dovi, dv_profile = detect_dolby_vision(input_file, logger)

    if not is_dovi:
        return None

    # -------------------------------------------------
    # Step 2: Extract raw HEVC from container
    # -------------------------------------------------
    log_debug(logger, "[DOVI] Extracting raw HEVC")

    # Find video track id
    identify = subprocess.run(
        ["mkvmerge", "-J", input_file],
        capture_output=True,
        text=True
    )

    try:
        data = json.loads(identify.stdout)
        video_track = next(t for t in data["tracks"] if t["type"] == "video")
        track_id = video_track["id"]
    except Exception:
        log_debug(logger, "[DOVI] Failed to detect video track")
        return None

    extract_cmd = [
        "mkvextract",
        "tracks",
        input_file,
        f"{track_id}:{temp_raw}"
    ]

    result = subprocess.run(
        extract_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0 or not os.path.exists(temp_raw):
        log_debug(logger, "[DOVI] mkvextract failed")
        return None

    # -------------------------------------------------
    # Step 3: Profile-specific stream conversion
    # -------------------------------------------------
    source_for_rpu = temp_raw
    profile = dv_profile or ""

    # Profile 7 (Blu-ray)
    if "07" in profile:
        log_debug(logger, "[DOVI] Profile 7 detected — converting to Profile 8.1")

        convert_cmd = [
            "dovi_tool",
            "-m", "2",
            "convert",
            "--discard",
            temp_raw,
            "-o",
            temp_p8
        ]

        conv = subprocess.run(
            convert_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )

        if conv.returncode != 0 or not os.path.exists(temp_p8):
            log_debug(logger, f"[DOVI] P7 conversion failed: {conv.stderr.strip()}")
            os.remove(temp_raw)
            return None

        os.remove(temp_raw)
        source_for_rpu = temp_p8
    else:
        log_debug(logger, "[DOVI] Profile already 8 or unsupported — no stream conversion")

    # -------------------------------------------------
    # Step 4: Extract RPU from clean stream
    # -------------------------------------------------
    extract_rpu_cmd = [
        "dovi_tool",
    ]

    if crop_rpu:
        extract_rpu_cmd += ["-c"]
        log_debug(logger, "[DOVI] Extracting RPU from prepared stream + set no black bars flag")
    else:
        log_debug(logger, "[DOVI] Extracting RPU from prepared stream")
    
    extract_rpu_cmd += [
        "extract-rpu",
        "-i", source_for_rpu,
        "-o", rpu_file
    ]

    rpu_proc = subprocess.run(
        extract_rpu_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    # Cleanup temp streams
    try:
        os.remove(source_for_rpu)
    except:
        pass

    if rpu_proc.returncode != 0 or not os.path.exists(rpu_file):
        log_debug(logger, f"[DOVI] RPU extraction failed: {rpu_proc.stderr.strip()}")
        return None

    log_debug(logger, "[DOVI] RPU ready for injection")
    return rpu_file


def inject_rpu(video_file, rpu_file, output_file, logger):
    log_debug(logger, "[DOVI] Injecting RPU")
    cmd = [
        'dovi_tool',
        'inject-rpu',
        '-i', video_file,
        '--rpu-in', rpu_file,
        '-o', output_file
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


CODEC_DISPLAY_NAME_MAP = {
    'h264': 'x264',
    'h265': 'x265',
    'hevc': 'x265',
    'vp9': 'VP9',
    'av1': 'AV1',
    'libx264': 'x264',
    'libx265': 'x265',
    'libvpx-vp9': 'VP9',
    'libsvtav1': 'AV1',
}


class OrderedRelease:
    """Hand back items in strictly increasing index order.

    Encodes finish in runtime order, not episode order: a short episode
    submitted second can easily overtake a long one submitted first. Holding an
    early finisher back until every lower index has been released is what makes
    the destination folder see ep1, ep2, ep3 ... in order.

    Pure bookkeeping - no threads, no I/O. The caller owns the concurrency.
    """

    def __init__(self, start=0):
        self._pending = {}
        self._next = start

    def add(self, index, payload):
        self._pending[index] = payload

    def ready(self):
        """Yield every payload now unblocked, in index order.

        Yields nothing while the head index is still missing, and never yields
        the same index twice.
        """
        while self._next in self._pending:
            yield self._pending.pop(self._next)
            self._next += 1

    def outstanding(self):
        """Indices held back because an earlier one has not arrived yet."""
        return sorted(self._pending)


def compute_post_encode_filename(input_file, output_codec, is_dovi):
    """Return the .mkv basename the encoder will produce for `input_file`.

    Mirrors the rename block at the tail of encode_single_video_file().
    Caller decides whether DV detection has run (passes is_dovi).
    """
    codec_display_name = CODEC_DISPLAY_NAME_MAP.get(output_codec.lower())

    replace_substrings = ['HEVC', 'AVC', 'H.265', 'H.264', 'h264', 'h265', 'x264', 'x265', 'VC-1']
    remove_substrings = ['.REMUX', ' REMUX', 'REMUX']

    basename = os.path.splitext(os.path.basename(input_file))[0]
    for substring in replace_substrings:
        pattern = re.compile(re.escape(substring), re.IGNORECASE)
        basename = pattern.sub(codec_display_name, basename)
    for substring in remove_substrings:
        pattern = re.compile(re.escape(substring), re.IGNORECASE)
        basename = pattern.sub('', basename)
    if is_dovi:
        basename = upgrade_filename_to_dv_hdr(basename)
    return basename + '.mkv'


def upgrade_filename_to_dv_hdr(basename):
    # Match separator + DV and capture the separator
    dv_pattern = re.compile(r'(?i)(?P<sep>[.\- _])dv(?=$|[.\- _])')
    hdr_pattern = re.compile(r'(?i)(?:^|[.\- _])hdr(?=$|[.\- _])')

    has_dv = dv_pattern.search(basename)
    has_hdr = hdr_pattern.search(basename)

    if has_dv and not has_hdr:
        def repl(match):
            sep = match.group('sep')
            return f"{sep}DV{sep}HDR"

        basename = dv_pattern.sub(repl, basename, count=1)

    return basename


# Map user-friendly codec names to ffmpeg encoder names. Immutable lookup —
# unlike the encoder option lists below, which are mutated per call and must
# stay function-local.
FFMPEG_CODEC_NAMES = {
    'h264': 'libx264',
    'h265': 'libx265',
    'hevc': 'libx265',
    'vp9': 'libvpx-vp9',
    'av1': 'libsvtav1'
}


def video_thread_count(codec, orig_width, cpu_usage_percentage):
    """ffmpeg -threads value for a file, from its codec and CPU allowance.

    Also used to price sample encodes, so a sample and the real encode of the
    same file are measured under the same thread budget.
    """
    num_cores = os.cpu_count()
    if codec.lower() == "libx265":
        if orig_width >= 3840:
            divisor = 4.3
        else:
            divisor = 3.3
    else:
        divisor = 0.8
    number_of_threads = max(1, int(num_cores * (cpu_usage_percentage / 100) // divisor))
    # Limit to 16 threads for x264, as recommended here:
    # https://obsproject.com/forum/threads/can-you-please-explain-x264-option-threads.76917/
    if codec.lower() == "libx264":
        number_of_threads = min(16, number_of_threads)
    return number_of_threads


def resolve_encode_settings(logger, input_file, dirpath, max_cpu_usage,
                            dims=None, skip_auto_crop=False):
    """Everything needed to build an ffmpeg video-encode command for a file.

    Shared by the real encode and by the sampler, so a timed sample runs with
    byte-identical encoder settings and its measurement means something.
    """
    crop_values = check_config(config, 'media-encoder', 'crop_values')
    limit_resolution = check_config(config, 'media-encoder', 'limit_resolution')
    output_codec = check_config(config, 'media-encoder', 'output_codec')
    input_quality_crf = check_config(config, 'media-encoder', 'quality_crf')
    encoding_speed = check_config(config, 'media-encoder', 'encoding_speed')
    tune = check_config(config, 'media-encoder', 'tune')
    custom_params = check_config(config, 'media-encoder', 'custom_params')

    quality_crf = resolve_quality_crf(input_quality_crf, [input_file], dirpath)

    media_file = os.path.join(dirpath, input_file)

    perform_auto_crop = False
    left = right = top = bottom = 0
    if crop_values == 'auto':
        perform_auto_crop = True
        cropping = True
    elif crop_values and crop_values != 'auto':
        left, right, top, bottom = map(int, crop_values.split(','))
        cropping = True
    else:
        cropping = False

    resizing = False
    custom_width = -2
    custom_height = -2
    if limit_resolution:
        custom_width = limit_resolution
        resizing = True

    # Define encoder-specific options. Built fresh on every call because the
    # blocks below mutate these lists and strings in place; a module-level
    # literal would accumulate those suffixes across files in a batch.
    encoder_options = {
        'libx264': {
            # -bf 4: Use up to 4 consecutive B-frames, increasing compression efficiency
            # -rc-lookahead 32: Pre-scan 32 upcoming frames
            # -aq-mode 3: Employ advanced adaptive quantization
            # -b-pyramid normal: Allow B-frames to serve as references
            # -coder 1: Enable CABAC entropy coding
            'options': ['-bf', '4', '-rc-lookahead', '32', '-aq-mode', '3', '-b-pyramid', 'normal', '-coder', '1'],
            'pix_fmt': None,
        },
        'libx265': {
            'options': ['-x265-params', 'rc-lookahead=32:aq-mode=3:bframes=4:no-sao=1:strong-intra-smoothing=0:deblock=-2:-2'],
            'pix_fmt': None,
        },
        'libvpx-vp9': {
            'options': [],
            'pix_fmt': None,
        },
        'libsvtav1': {
            'options': [],
            'pix_fmt': None,
        },
    }

    # Map to variables
    quality = quality_crf
    codec = FFMPEG_CODEC_NAMES[output_codec]
    tune_option = tune
    encoder_speed = encoding_speed
    cpu_usage_percentage = float(max_cpu_usage)
    user_custom_ffmpeg = custom_params

    # Map 'medium' and 'slow' to numerical speed values for AV1 and VP9 codecs
    if encoder_speed.lower() in ('medium', 'slow', 'slower') and codec in ('libsvtav1', 'libvpx-vp9'):
        if encoder_speed.lower() == 'medium':
            if codec == 'libsvtav1':
                encoder_speed = '6'
            elif codec == 'libvpx-vp9':
                encoder_speed = '2'
        elif encoder_speed.lower() == 'slow':
            if codec == 'libsvtav1':
                encoder_speed = '4'
            elif codec == 'libvpx-vp9':
                encoder_speed = '0'
        elif encoder_speed.lower() == 'slower':
            if codec == 'libsvtav1':
                encoder_speed = '2'
            elif codec == 'libvpx-vp9':
                encoder_speed = '0'

    # Fine-tune psy-rd if using x264 or x265
    if codec in ['libx264', 'libx265']:
        if codec == 'libx264':
            encoder_options[codec]['options'].extend(['-psy-rd', '3.0:0.0'])
        elif codec == 'libx265':
            for i, opt in enumerate(encoder_options[codec]['options']):
                if opt == '-x265-params':
                    encoder_options[codec]['options'][i + 1] += ':psy-rd=2:psy-rdoq=1.5'
                    break

    # Remove 'encoding settings' metadata
    if codec == 'libx264':
        encoder_options[codec]['options'].extend(['-x264-params', 'no-info=1'])
    elif codec == 'libx265':
        for i, opt in enumerate(encoder_options[codec]['options']):
            if opt == '-x265-params':
                encoder_options[codec]['options'][i + 1] += ':no-info=1'
                break

    # Get original dimensions
    if dims:
        orig_width, orig_height = dims
    else:
        orig_width, orig_height = get_video_dimensions(media_file)

    number_of_threads = video_thread_count(codec, orig_width, cpu_usage_percentage)
    log_debug(logger, f"File '{input_file}' will use {number_of_threads} threads with {codec}. CRF value {quality}, encoder speed {encoder_speed}, tune '{tune}'. "
                      f"CPU usage alloc {cpu_usage_percentage}%")

    # The sampler skips the HandBrakeCLI scan: paying it per file would cost
    # more than the sample itself, and the estimator's calibration factor
    # absorbs the resulting (systematic) pixel-count bias.
    if cropping and perform_auto_crop and skip_auto_crop:
        cropping = False

    if cropping:
        if perform_auto_crop:
            auto_crop_values = auto_crop(media_file)
            left, right, top, bottom = map(int, auto_crop_values.split(','))
        cropped_width = orig_width - left - right
        cropped_height = orig_height - top - bottom
    else:
        cropped_width = orig_width
        cropped_height = orig_height

    if resizing:
        output_width, output_height = custom_width, custom_height
    else:
        # If no resizing, output dimensions are the same as cropped dimensions
        output_width = cropped_width
        output_height = cropped_height

    crop_filter = None
    filter_chain = []
    if cropping:
        # Crop filter
        crop_filter = f"crop=w=iw-{left}-{right}:h=ih-{top}-{bottom}:x={left}:y={top}"
        filter_chain.append(crop_filter)
    if resizing:
        scale_filter = f"scale=w={output_width}:h={output_height}"
        filter_chain.append(scale_filter)

    return {
        'media_file': media_file,
        'codec': codec,
        'output_codec': output_codec,
        'quality': quality,
        'encoder_speed': encoder_speed,
        'tune_option': tune_option,
        'user_custom_ffmpeg': user_custom_ffmpeg,
        'encoder_options': encoder_options[codec],
        'number_of_threads': number_of_threads,
        'orig_width': orig_width,
        'orig_height': orig_height,
        'cropping': cropping,
        'crop_filter': crop_filter,
        'filter_chain': filter_chain,
    }


def build_video_filter_chain(settings):
    """Comma-joined -vf argument, or None when no filters apply."""
    return ",".join(settings['filter_chain']) if settings['filter_chain'] else None


def build_ffmpeg_cmd(settings, out_target, filter_str,
                     dovi_raw_hevc=False, sample=None):
    """The full ffmpeg argv for a video-only encode.

    sample=(offset_seconds, length_seconds) turns this into a timed probe:
    `-ss` goes before `-i` for a cheap keyframe seek, `-t` bounds the work, and
    the output is discarded through the null muxer. With sample=None the argv
    is identical to what the encoder has always produced.
    """
    codec = settings['codec']

    cmd_ffmpeg = ['ffmpeg', '-y', '-progress', 'pipe:1', '-nostats']
    if sample:
        cmd_ffmpeg.extend(['-ss', f"{sample[0]:.3f}"])
    cmd_ffmpeg.extend(['-i', settings['media_file']])
    if sample:
        cmd_ffmpeg.extend(['-t', f"{sample[1]:.3f}"])

    if filter_str:
        cmd_ffmpeg.extend(['-vf', filter_str])

    cmd_ffmpeg.extend([
        '-map', 'v:0',  # Map only video
        '-c:v', codec,
        '-crf', settings['quality'],
        '-threads', str(settings['number_of_threads']),  # Limit CPU usage
    ])

    if dovi_raw_hevc and codec.lower() == "libx265":
        cmd_ffmpeg.extend([
            '-an',
            '-sn',
            '-dn',
            '-f', 'hevc'
        ])

    # Apply the encoder speed/preset depending on the codec
    if codec in ['libx264', 'libx265']:
        # Use '-preset'
        cmd_ffmpeg.extend(['-preset', settings['encoder_speed']])
    elif codec == 'libvpx-vp9':
        # For VP9, use '-cpu-used'
        cmd_ffmpeg.extend(['-cpu-used', settings['encoder_speed']])
    elif codec == 'libsvtav1':
        # For AV1, use '-preset'
        cmd_ffmpeg.extend(['-preset', settings['encoder_speed']])

    # Add pix_fmt if specified for the codec
    if settings['encoder_options']['pix_fmt']:
        cmd_ffmpeg.extend(['-pix_fmt', settings['encoder_options']['pix_fmt']])

    # Add encoder-specific options
    cmd_ffmpeg.extend(settings['encoder_options']['options'])

    # Add tune option if provided
    if settings['tune_option'] and codec != "libvpx-vp9":
        cmd_ffmpeg.extend(['-tune', settings['tune_option']])

    # Add user-custom parameters if provided
    if settings['user_custom_ffmpeg'].strip():
        # A simple split() handles space-delimited arguments
        cmd_ffmpeg.extend(settings['user_custom_ffmpeg'].split())

    if sample:
        cmd_ffmpeg.extend(['-f', 'null', '-'])
    else:
        cmd_ffmpeg.append(out_target)

    return cmd_ffmpeg


def encode_single_video_file(logger, debug, input_file, dirpath, max_cpu_usage,
                             progress: ProgressState, worker_id,
                             estimator=None, file_index=None):
    filesize_info = {
        "initial_file_size": 0,
        "resulting_file_size": 0,
        "initial_video_size": 0,
        "resulting_video_size": 0
    }

    FFMPEG_WEIGHT = 0.99
    MKVMERGE_WEIGHT = 0.01

    settings = resolve_encode_settings(logger, input_file, dirpath, max_cpu_usage)
    codec = settings['codec']
    media_file = settings['media_file']

    filesize_info["initial_file_size"] = os.path.getsize(media_file)

    base_name = os.path.splitext(os.path.basename(input_file))[0]
    safe_base = re.sub(r'[^a-zA-Z0-9._-]', '_', base_name)
    job_id = uuid.uuid4().hex[:8]

    temp_dir = os.path.join(dirpath, f"_tmp_{job_id}")
    os.makedirs(temp_dir, exist_ok=True)

    # ---- Dolby Vision detection ----
    is_dovi = False
    if codec.lower() == "libx265":
        is_dovi, dv_profile = detect_dolby_vision(media_file, logger)

    rpu_file = None
    crop_rpu = False
    if settings['cropping'] and settings['crop_filter'] != "crop=w=iw-0-0:h=ih-0-0:x=0:y=0":
        crop_rpu = True

    if is_dovi and codec.lower() == "libx265":
        log_debug(logger, f"[DOVI] Dolby Vision detected (Profile: {dv_profile})")

        if dv_profile and "7" in dv_profile:
            log_debug(logger, "[DOVI] UHD Blu-ray profile detected (BL+EL possible)")

        temp_rpu_path = os.path.join(temp_dir, "rpu.bin")

        try:
            rpu_file = extract_rpu(media_file, crop_rpu, temp_rpu_path, logger, dirpath)

            if rpu_file is None:
                log_debug(logger, "[DOVI] No usable RPU — falling back to HDR10")
                is_dovi = False

        except Exception as e:
            log_debug(logger, f"[DOVI] RPU extraction error: {e}")
            is_dovi = False
            rpu_file = None


    # Build filter string
    filter_str = build_video_filter_chain(settings)

    if is_dovi and codec.lower() == "libx265":
        temp_video_file = os.path.join(temp_dir, "video.hevc")
    else:
        temp_video_file = os.path.join(temp_dir, f"video_{safe_base}.mkv")

    temp_file = os.path.join(temp_dir, "output.mkv")
    cmd_ffmpeg = build_ffmpeg_cmd(settings, temp_video_file, filter_str,
                                  dovi_raw_hevc=is_dovi)

    duration = get_video_duration(media_file)  # seconds

    # Savings are measured against the video stream alone, since every other track is
    # remuxed through untouched below. Probed once here, never inside the progress loop.
    source_video_size = get_video_stream_bytes(
        logger, media_file, duration, filesize_info["initial_file_size"]
    )
    filesize_info["initial_video_size"] = (
        source_video_size if source_video_size else filesize_info["initial_file_size"]
    )

    log_debug(logger, f"[MEDIA-ENCODER] FFmpeg command: '{' '.join(cmd_ffmpeg)}'")

    process = subprocess.Popen(
        cmd_ffmpeg,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True
    )

    progress.start_worker(worker_id)
    if estimator is not None:
        estimator.note_start(file_index)

    stdout_lines = []
    stderr_lines = []

    for line in process.stdout:
        stdout_lines.append(line)

        if not line.startswith("out_time_ms="):
            continue

        value = line.split("=", 1)[1].strip()
        if value == "N/A":
            continue

        try:
            out_time = int(value) / 1_000_000
        except ValueError:
            continue

        if duration > 0:
            fraction = out_time / duration
            progress.update_worker_progress(
                worker_id,
                fraction * FFMPEG_WEIGHT
            )
            if estimator is not None:
                estimator.note_progress(file_index, fraction * FFMPEG_WEIGHT)

            # Live size estimate: project the encoded video's final size from
            # how much has been written so far. Skip the noisy early fraction.
            if fraction > 0.02 and os.path.exists(temp_video_file):
                cur = os.path.getsize(temp_video_file)
                projected_video = cur / fraction
                progress.update_size_estimate(
                    worker_id,
                    projected_video,
                    filesize_info["initial_video_size"],
                )
    process.wait()

    if process.returncode != 0:
        e = subprocess.CalledProcessError(
            process.returncode,
            cmd_ffmpeg
        )
        custom_print(logger, f"{RED}[ERROR]{RESET} FFmpeg failed with return code {e.returncode}")
        raise e

    progress.update_worker_progress(worker_id, FFMPEG_WEIGHT)

    # ---- Re-inject Dolby Vision after encode ----
    if is_dovi and os.path.exists(rpu_file) and codec.lower() == "libx265":
        injected_file = os.path.join(temp_dir, "video_dovi.hevc")

        try:
            inject_rpu(temp_video_file, rpu_file, injected_file, logger)
            os.remove(temp_video_file)
            temp_video_file = injected_file
            log_debug(logger, "[DOVI] RPU injection successful")
        except Exception as e:
            log_debug(logger, f"[DOVI] Injection failed, continuing without DV: {e}")

        # Cleanup RPU
        try:
            os.remove(rpu_file)
        except:
            pass

    cmd_mkvmerge = [
        'mkvmerge',
        '-o', temp_file,
        temp_video_file,
        '--no-video', media_file
    ]

    log_debug(logger, f"[MEDIA-ENCODER] MKVMERGE command: '{' '.join(cmd_mkvmerge)}'")

    process = subprocess.Popen(
        cmd_mkvmerge,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    stderr_lines = []

    for line in process.stderr:
        stderr_lines.append(line)

        line = line.strip()
        if not line.startswith("Progress:"):
            continue

        try:
            percent = float(line.split(":", 1)[1].strip().rstrip("%"))
        except ValueError:
            continue

        progress.update_worker_progress(worker_id, FFMPEG_WEIGHT + (percent / 100.0) * MKVMERGE_WEIGHT)

    process.wait()

    if process.returncode != 0:
        e = subprocess.CalledProcessError(
            process.returncode,
            cmd_mkvmerge,
            stderr="".join(stderr_lines)
        )
        custom_print(logger, f"{RED}[ERROR]{RESET} MKVMERGE failed with return code {e.returncode}")
        custom_print(logger, f"{RED}[STDERR]{RESET}\n{YELLOW}{e.stderr.strip()}{RESET}")
        raise e

    # Measured before cleanup. On the Dolby Vision path temp_video_file has already
    # been reassigned to the post-injection stream, so this is the real video payload.
    filesize_info["resulting_video_size"] = os.path.getsize(temp_video_file)

    # Folded in before finish_worker, which drops this worker's live estimate:
    # recording afterwards would leave the file counted in neither term for a moment.
    progress.record_completion(
        filesize_info["initial_video_size"],
        filesize_info["resulting_video_size"],
    )
    progress.finish_worker(worker_id)
    # Recorded after the remux, so the measured cost includes the mkvmerge tail
    # that a sample cannot capture — the calibration factor learns that gap.
    if estimator is not None:
        estimator.note_complete(file_index)

    # Cleanup
    os.remove(temp_video_file)
    os.remove(media_file)

    cleaned_filename = compute_post_encode_filename(input_file, codec, is_dovi)

    os.rename(temp_file, os.path.join(dirpath, cleaned_filename))
    filesize_info["resulting_file_size"] = os.path.getsize(os.path.join(dirpath, cleaned_filename))

    # Cleanup temp workspace
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:
        log_debug(logger, f"[DOVI] Temp cleanup failed: {e}")

    return cleaned_filename, filesize_info


class SamplerGate:
    """Handshake between the encode scheduler and the sampler thread.

    The sampler never breaks 4K exclusivity: it waits for any 4K encode to
    clear before starting a sample, and an admitted 4K encode kills a sample
    already in flight. The reverse is deliberately not true — a 4K encode never
    waits on the sampler.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._four_k = False
        self._in_flight = 0
        self._cap = 1
        self._proc = None

    def _kill_proc_locked(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def set_four_k_inflight(self, flag):
        flag = bool(flag)
        with self._lock:
            was_set = self._four_k
            self._four_k = flag
            # Only on the transition: a running sample is cut short so the 4K
            # encode gets the machine. Its partial measurement is still valid.
            if flag and not was_set:
                self._kill_proc_locked()

    def set_load(self, in_flight, cap):
        with self._lock:
            self._in_flight = in_flight
            self._cap = cap

    def has_idle_slot(self):
        with self._lock:
            return not self._four_k and self._in_flight < self._cap

    def four_k_in_flight(self):
        with self._lock:
            return self._four_k

    def stopping(self):
        return self._stop.is_set()

    def wait_until_no_4k(self):
        """Block until no 4K encode is in flight. False if we are stopping.

        No timeout: while a 4K file runs it holds the machine exclusively, so
        the sampler's reserved slot costs nothing during the wait.
        """
        while not self._stop.is_set():
            if not self.four_k_in_flight():
                return True
            self._stop.wait(SCHED_POLL_INTERVAL)
        return False

    def register_proc(self, proc):
        with self._lock:
            self._proc = proc

    def clear_proc(self):
        with self._lock:
            self._proc = None

    def stop(self):
        self._stop.set()
        with self._lock:
            self._kill_proc_locked()


def sample_file_cost(logger, input_file, dirpath, cpu_share, gate,
                     duration, width, height, offset_fraction):
    """Time a short real encode of one file.

    Returns (wall_seconds, video_seconds_encoded), or None if the file cannot
    be sampled usefully. Never raises — a sampling failure must not disturb the
    batch, it only leaves that file priced from the pool instead.
    """
    if not duration or duration < SAMPLE_MIN_SOURCE:
        return None

    proc = None
    watchdog = None
    try:
        settings = resolve_encode_settings(
            logger, input_file, dirpath, cpu_share,
            dims=(width, height) if width and height else None,
            skip_auto_crop=True,
        )
        filter_str = build_video_filter_chain(settings)

        length = min(SAMPLE_VIDEO_SECONDS, max(SAMPLE_MIN_SECONDS, duration * 0.5))
        offset = max(0.0, min(duration * offset_fraction, duration - length))

        cmd = build_ffmpeg_cmd(settings, None, filter_str, sample=(offset, length))
        log_debug(logger, f"[ENCODER] Sample command: '{' '.join(cmd)}'")

        started = time.time()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
        gate.register_proc(proc)

        # A wall-clock guard rather than a check inside the read loop: if the
        # encoder stalls, no more progress lines arrive to check on.
        watchdog = threading.Timer(SAMPLE_MAX_WALL, proc.kill)
        watchdog.daemon = True
        watchdog.start()

        out_time = 0.0
        for line in proc.stdout:
            if not line.startswith("out_time_ms="):
                continue
            value = line.split("=", 1)[1].strip()
            if value == "N/A":
                continue
            try:
                out_time = int(value) / 1_000_000
            except ValueError:
                continue
        proc.wait()
        wall = time.time() - started

        # Deliberately not gated on the return code: a sample killed by the
        # watchdog or by an incoming 4K encode is still a valid measurement of
        # however much it did manage to encode.
        if out_time < SAMPLE_MIN_SECONDS or wall <= 0:
            return None
        return wall, out_time

    except Exception as e:
        log_debug(logger, f"[ENCODER] Sampling '{input_file}' failed: {e}")
        return None
    finally:
        if watchdog is not None:
            watchdog.cancel()
        gate.clear_proc()


def run_sampler(logger, estimator, gate, jobs, dirpath, cpu_for,
                first_pass_only=False):
    """Sampler thread body: a fast pass over every file, then refinement.

    Pass 1 holds the reserved encode slot and gives every file a measurement as
    quickly as possible. Pass 2 is strictly opportunistic — it only samples
    when an encode slot is genuinely idle, so refinement never delays encoding.

    first_pass_only is for the one-slot batch, where sampling runs to completion
    up front instead of alongside encoding.
    """

    def take(meta, offset_fraction):
        path = os.path.join(dirpath, meta['name'])
        # The encode that owns this file removes it on completion; between the
        # pending check and here it may already be gone.
        if not os.path.exists(path):
            estimator.mark_unsampleable(meta['index'])
            return
        result = sample_file_cost(
            logger, meta['name'], dirpath, cpu_for(meta), gate,
            meta['duration'], meta['width'], meta['height'], offset_fraction,
        )
        if result:
            estimator.record_sample(meta['index'], result[0], result[1])
        else:
            estimator.mark_unsampleable(meta['index'])

    def await_idle_slot(index):
        while not gate.has_idle_slot():
            if gate.stopping() or not estimator.wants_sample(index):
                return False
            time.sleep(SCHED_POLL_INTERVAL)
        return True

    try:
        # Pass 1 — one measurement per file. Files already encoding or done are
        # skipped: their real progress is better evidence than any sample, so
        # the reserved slot naturally works the tail of the queue.
        for meta in jobs:
            if gate.stopping():
                return
            if not estimator.wants_sample(meta['index']):
                continue
            if not gate.wait_until_no_4k():
                return
            take(meta, SAMPLE_OFFSETS[0])
    finally:
        # Releases the reserved slot back to encoding even if pass 1 aborted.
        estimator.set_sampling(False)

    if first_pass_only:
        return

    # Pass 2 — extra offsets on files that have still not started, to catch
    # scenes the first window missed.
    for offset_fraction in SAMPLE_OFFSETS[1:MAX_SAMPLES_PER_FILE]:
        for meta in jobs:
            if gate.stopping():
                return
            if not estimator.wants_sample(meta['index']):
                continue
            if not await_idle_slot(meta['index']):
                continue
            if not gate.wait_until_no_4k():
                return
            if estimator.wants_sample(meta['index']):
                take(meta, offset_fraction)


def encode_media_files(logger, debug, input_files, dirpath, output_dir, origins=None, moved_out=None):
    """Encode every file, delivering each to its destination as soon as it is done.

    ``moved_out``, when given, is a set the caller owns: every basename this
    function delivers is added to it as it lands. A return value cannot serve
    that purpose, because an encode failure unwinds before the return and the
    caller's error handler still needs to know what has already been delivered.
    """
    total_files = len(input_files)
    updated_filenames = [None] * total_files
    filesizes_info = [None] * total_files
    origins = origins or {}

    output_codec = check_config(config, 'media-encoder', 'output_codec')
    input_quality_crf = check_config(config, 'media-encoder', 'quality_crf')
    max_cpu_usage = check_config(config, 'general', 'max_cpu_usage')

    crop_values = check_config(config, 'media-encoder', 'crop_values')
    limit_resolution = check_config(config, 'media-encoder', 'limit_resolution')
    encoding_speed = check_config(config, 'media-encoder', 'encoding_speed')
    tune = check_config(config, 'media-encoder', 'tune')
    custom_params = check_config(config, 'media-encoder', 'custom_params')

    if output_codec.upper() == "VP9":
        log_debug(logger, f"[MEDIA-ENCODER] Tune '{tune}' requested. This is not possible with VP9. Ignoring.")
        tune = ''

    quality_crf = resolve_quality_crf(input_quality_crf, input_files, dirpath)

    log_debug(logger, f"[MEDIA-ENCODER] Output codec: '{output_codec}'")
    log_debug(logger, f"[MEDIA-ENCODER] Quality CRF: '{quality_crf}'")
    log_debug(logger, f"[MEDIA-ENCODER] Max CPU usage: '{max_cpu_usage}'")
    log_debug(logger, f"[MEDIA-ENCODER] Crop values: '{crop_values}'")
    log_debug(logger, f"[MEDIA-ENCODER] Limit resolution: '{limit_resolution}'")
    log_debug(logger, f"[MEDIA-ENCODER] Encoding speed: '{encoding_speed}'")
    log_debug(logger, f"[MEDIA-ENCODER] Tune: '{tune}'")
    log_debug(logger, f"[MEDIA-ENCODER] Custom parameters: '{custom_params}'")

    max_worker_threads = get_worker_thread_count()
    codec = output_codec.lower()

    if codec in ('h265', 'h264'):
        codec_cap = min(4, max_worker_threads)
    else:
        codec_cap = max_worker_threads

    upper_bound = min(codec_cap, total_files) if total_files else 1

    codec_map = {
        'h265': 'H.265',
        'h264': 'H.264',
        'vp9': 'VP9',
        'av1': 'AV1'
    }
    display_codec = codec_map.get(output_codec.lower(), output_codec)

    if crop_values:
        crop_banner = " AUTOCROP" if crop_values == 'auto' else f" CROP:{crop_values}"
    else:
        crop_banner = ''

    print()
    custom_print_no_newline(logger, f"{GREY}[ENCODER]{RESET} {display_codec} CRF-{quality_crf} {encoding_speed.upper()}{f" {tune.upper()}" if tune else ''}{crop_banner}")

    # Pre-encode copy: rename sources to their final post-encode filenames and
    # copy them to the output destination so Plex/Sonarr can pick the media up
    # immediately. The real encode runs against the renamed source in dirpath
    # and the post-encode move overwrites the placeholder with the same name.
    final_names = []
    for f in input_files:
        is_dovi_pre = False
        if output_codec.lower() == 'h265':
            is_dovi_pre, _ = detect_dolby_vision(os.path.join(dirpath, f), logger)
        final_names.append(compute_post_encode_filename(f, output_codec, is_dovi_pre))

    # Carry each file's original subfolder path forward onto its post-encode
    # name so the destination can be resolved without parsing the filename.
    relative_dir_by_final = {}
    for original, final in zip(list(input_files), final_names):
        wf = origins.get(original)
        relative_dir_by_final[final] = wf.relative_dir if wf else ""
        if original != final:
            os.rename(os.path.join(dirpath, original), os.path.join(dirpath, final))
    input_files = list(final_names)

    # Resolve final destination paths once per file. Capped at 2 workers to
    # match move_files_to_output_process()'s TVMaze rate-limit cap, so the
    # pre-copy and post-encode move converge on the same destination path.
    resolved_targets = {}
    resolution_workers = max(1, min(2, total_files))
    with concurrent.futures.ThreadPoolExecutor(max_workers=resolution_workers) as resolver:
        resolve_futures = {
            resolver.submit(resolve_output_target, logger, debug,
                            os.path.join(dirpath, f), output_dir,
                            relative_dir_by_final.get(f, ""), f): f
            for f in input_files
        }
        for fut in concurrent.futures.as_completed(resolve_futures):
            resolved_targets[resolve_futures[fut]] = fut.result()

    # Keyed by index rather than by post-encode basename. The pre-copy resolves
    # each target from the name compute_post_encode_filename() predicted, but the
    # encode recomputes that name itself and can disagree in one case: DV was
    # detected up front, then RPU extraction failed mid-encode, so the final name
    # loses its 'DV HDR' upgrade. A name-keyed lookup would miss, resolve a second
    # destination path, and leave the un-encoded placeholder behind as a duplicate.
    target_by_index = {idx: resolved_targets[name] for idx, name in enumerate(input_files)}

    copy_workers = max(1, min(get_worker_thread_count(), total_files))
    copy_description = f"Copying {print_multi_or_single(total_files, 'file')}"
    copy_done = [0]

    # Full-size sources going to the destination: the longest purely byte-bound
    # wait in the run, and the one most worth an estimate.
    copy_progress = ByteProgress(total_file_size(dirpath, input_files))
    copy_estimator = ThroughputEstimator(copy_progress.total_bytes(), copy_progress.done_bytes)

    def _copy_line():
        metrics = system_metrics_chip(disk_paths=(dirpath, output_dir))
        return (f"[ENCODER]{metrics}{eta_chip(copy_estimator)}{RESET} "
                f"{copy_description} ({copy_done[0]}/{total_files}) ")

    def _copy_one(name):
        source = os.path.join(dirpath, name)
        target = resolved_targets[name]
        size = os.path.getsize(source) if os.path.exists(source) else 0
        copy_progress.start(name, target["output_path"])
        try:
            return move_resolved_to_output(logger, source, target, True)
        finally:
            copy_progress.finish(name, size)

    print()
    copy_spinner = ContinuousSpinner(interval=0.15)
    copy_spinner.set_line_func(_copy_line)
    copy_spinner.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=copy_workers) as executor:
            copy_futures = [executor.submit(_copy_one, f) for f in input_files]
            for future in concurrent.futures.as_completed(copy_futures):
                future.result()
                copy_done[0] += 1
    finally:
        copy_spinner.stop()

    start_time = time.time()

    header = "ENCODER"
    description = f"Encoding"

    progress = ProgressState(total_files, upper_bound)
    worker_id_pool = Queue()
    for wid in range(upper_bound):
        worker_id_pool.put(wid)

    def per_file_cpu_for(meta):
        if meta['is_4k']:
            return float(max_cpu_usage)
        if codec in ('h265', 'h264'):
            return float(max_cpu_usage) / min(4, max_worker_threads)
        return float(max_cpu_usage) / max_worker_threads

    ffmpeg_codec = FFMPEG_CODEC_NAMES.get(codec, codec)

    file_meta = []
    for idx, f in enumerate(input_files):
        full = os.path.join(dirpath, f)
        try:
            w, h = get_video_dimensions(full)
        except Exception:
            w = h = None
        try:
            duration = get_video_duration(full)
        except Exception:
            duration = None
        meta = {
            'index': idx,
            'name': f,
            'width': w,
            'height': h,
            'duration': duration if duration and duration > 0 else None,
            'is_4k': (w or 0) >= 3840,
        }
        # The thread count the real encode will use, so sampled and predicted
        # costs are expressed in the same units.
        meta['threads'] = video_thread_count(ffmpeg_codec, w or 0, per_file_cpu_for(meta))
        file_meta.append(meta)

    estimator = EncodeEstimator(file_meta, codec_cap)
    gate = SamplerGate()
    sampler_thread = None

    # A sampler only pays for itself when it has a spare slot to run in and
    # files it can sample before they start encoding.
    all_4k = bool(file_meta) and all(m['is_4k'] for m in file_meta)
    sample_first = total_files >= 2 and codec_cap <= 1
    sample_alongside = total_files >= 2 and codec_cap > 1 and not all_4k
    # A single file, or an all-4K batch, gets no sampler: 4K runs exclusively so
    # there is never a spare slot, and a lone file reaches a trustworthy
    # self-measured ETA within seconds of starting. Both fall back to the
    # estimator's observation-only path.

    SPINNER = ContinuousSpinner(interval=0.15)
    SPINNER.set_line_func(
        make_progress_line(progress, header, description, start_time, estimator)
    )
    SPINNER.start()

    def effective_cap():
        return max(1, codec_cap - (1 if estimator.sampling_active() else 0))

    def can_submit(candidate, in_flight_metas):
        if candidate['is_4k']:
            return len(in_flight_metas) == 0
        if any(m['is_4k'] for m in in_flight_metas):
            return False
        return len(in_flight_metas) < effective_cap()

    pending = list(file_meta)
    in_flight = {}

    # Encoding is the last stage that touches the media, so a finished file has
    # no reason to wait on the rest of the batch. One mover thread, fed strictly
    # in index order, delivers them as they land: submission order is execution
    # order with a single worker, which is what keeps ep1 ahead of ep2 at the
    # destination. It also keeps the move - usually a cross-filesystem copy - off
    # the scheduler thread and caps its disk contention with the live encodes.
    release = OrderedRelease()
    move_futures = []
    mover = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    moves_done = [0]

    # Only the drain at the end is watched, but the tracker has to span the whole
    # stage: it is fed as each file lands, so by the time the line appears it
    # already knows the throughput and can quote a figure immediately.
    #
    # Sized as it goes rather than up front. What gets moved is the *encoded*
    # file, which is nothing like the size of the source it replaced, and whose
    # size is only known once its encode finishes. Every file has been dispatched
    # by the time the drain line appears, so the total is complete there.
    move_progress = ByteProgress()

    def _move_one(idx, name):
        result = move_files_to_output_process_worker(
            logger, debug, name, dirpath, origins.get(name), output_dir,
            target_by_index.get(idx), move_progress)
        if moved_out is not None:
            moved_out.add(name)
        moves_done[0] += 1
        return result

    def dispatch_ready_moves():
        for idx, name in release.ready():
            if name is None:
                continue
            # Sized here, on the scheduler thread, rather than inside the job:
            # the mover runs one at a time, so a job queued behind others would
            # not have contributed its size yet when the drain line appears.
            source = os.path.join(dirpath, name)
            move_progress.add_total(os.path.getsize(source) if os.path.exists(source) else 0)
            move_futures.append(mover.submit(_move_one, idx, name))

    def sync_gate():
        cap = effective_cap()
        gate.set_load(len(in_flight), cap)
        gate.set_four_k_inflight(any(m['is_4k'] for m in in_flight.values()))
        estimator.set_slots(cap)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=upper_bound)
    encode_failed = True
    try:
        if sample_first:
            # Only one encode slot exists, so there is no spare worker to
            # sample on: pay for the estimate up front, then encode.
            sync_gate()
            run_sampler(logger, estimator, gate, file_meta, dirpath,
                        per_file_cpu_for, first_pass_only=True)
        elif sample_alongside:
            estimator.set_sampling(True)
            sync_gate()
            sampler_thread = threading.Thread(
                target=run_sampler,
                args=(logger, estimator, gate, file_meta, dirpath, per_file_cpu_for),
                daemon=True,
            )
            sampler_thread.start()

        while pending or in_flight:
            progress_made = True
            while progress_made and pending:
                progress_made = False
                for i, meta in enumerate(pending):
                    if can_submit(meta, in_flight.values()):
                        fut = executor.submit(
                            encode_with_worker_id,
                            logger,
                            debug,
                            meta['name'],
                            dirpath,
                            per_file_cpu_for(meta),
                            progress,
                            worker_id_pool,
                            estimator,
                            meta['index'],
                        )
                        in_flight[fut] = meta
                        pending.pop(i)
                        progress_made = True
                        sync_gate()
                        break

            if not in_flight:
                break

            # Timed rather than open-ended: when the sampler finishes it hands
            # its slot back, and without a timeout that extra capacity would go
            # unused until some encode happened to finish.
            done, _ = concurrent.futures.wait(
                in_flight.keys(),
                timeout=SCHED_POLL_INTERVAL,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for fut in done:
                meta = in_flight.pop(fut)
                try:
                    updated_filename, filesize_info = fut.result()
                    if updated_filename is not None:
                        updated_filenames[meta['index']] = updated_filename
                    if filesize_info is not None:
                        filesizes_info[meta['index']] = filesize_info
                    release.add(meta['index'], (meta['index'], updated_filename))
                except Exception as e:
                    custom_print(logger, f"\n{RED}[ERROR]{RESET} {e}")
                    traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                    print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                    raise
            dispatch_ready_moves()
            sync_gate()
        encode_failed = False
    finally:
        # Before shutdown(wait=True), not after: on the failure path that wait
        # can last for the remaining encodes, and a live sampler would burn a
        # core throughout and outlive a Ctrl-C.
        gate.stop()
        if sampler_thread is not None:
            sampler_thread.join(timeout=30)
        executor.shutdown(wait=True)
        if encode_failed:
            # Let the moves already in flight finish rather than abandoning a
            # half-written destination file. Whatever the release buffer is still
            # holding back stays in TEMP for the caller's error path to move.
            mover.shutdown(wait=True)

    log_debug(logger, f"[ENCODER] ETA estimator: {estimator.debug_line()}")

    end_time = time.time()
    processing_time = end_time - start_time
    done_description = (
        f"Encoding time: "
        f"{format_time(int(processing_time), conjunction=False, include_seconds=False)}"
    )

    SPINNER.stop(
        f"{GREY}[UTC {get_timestamp_short()}] [{header}]{RESET} "
        f"{done_description} {DONE}{CHECK}{RESET}"
    )
    SPINNER = None
    logger.info(f"[UTC {get_timestamp()}] [{header}] {done_description} {CHECK}")
    logger.debug(f"[UTC {get_timestamp()}] [{header}] {done_description} {CHECK}")
    logger.color(f"{GREY}[UTC {get_timestamp_short()}] [{header}]{RESET} {done_description} {DONE}{CHECK}{RESET}")

    # Every file but the last was delivered while the batch was still encoding, so
    # what is drained here is usually just the tail: the final file's move, which
    # cannot start until its own encode ends. A local spinner, never the module
    # global one - and only started after the encode spinner has stopped, so a
    # single spinner owns the line at any moment.
    move_total = len(move_futures)
    if move_total:
        move_header = "INFO"
        move_description = f"Move {print_multi_or_single(move_total, 'file')} to destination folder"
        move_spinner = None

        if moves_done[0] < move_total:
            move_estimator = ThroughputEstimator(move_progress.total_bytes(),
                                                 move_progress.done_bytes)

            def _move_line():
                metrics = system_metrics_chip(disk_paths=(dirpath, output_dir))
                return (f"[{move_header}]{metrics}{eta_chip(move_estimator)}{RESET} "
                        f"{move_description} ({moves_done[0]}/{move_total}) ")

            print()
            move_spinner = ContinuousSpinner(interval=0.15)
            move_spinner.set_line_func(_move_line)
            move_spinner.start()

        mover.shutdown(wait=True)

        move_final_line = (f"{GREY}[UTC {get_timestamp_short()}] [{move_header}]{RESET} "
                           f"{move_description} {DONE}{CHECK}{RESET}")
        if move_spinner is not None:
            move_spinner.stop(move_final_line)
        else:
            print()
            sys.stdout.write(f"\r{move_final_line}\033[K\r")
            sys.stdout.flush()
        logger.info(f"[UTC {get_timestamp()}] [{move_header}] {move_description} {CHECK}")
        logger.debug(f"[UTC {get_timestamp()}] [{move_header}] {move_description} {CHECK}")
        logger.color(move_final_line)

        print_arr_summary(logger, (fut.result() for fut in move_futures))
    else:
        mover.shutdown(wait=True)

    # Reported on a video-only basis: audio, subtitles and attachments are remuxed
    # through untouched, so counting them would dilute the encoder's actual result.
    total_initial_size = sum(info["initial_video_size"] for info in filesizes_info if info)
    total_resulting_size = sum(info["resulting_video_size"] for info in filesizes_info if info)

    if total_initial_size > 0:
        savings_bytes = total_initial_size - total_resulting_size
        savings_percent = int((savings_bytes / total_initial_size) * 100)

        formatted_initial = format_size(total_initial_size, False)
        formatted_result = format_size(total_resulting_size, False)
        label = "Total video savings" if savings_percent >= 0 else "Total video increase"
        print()
        custom_print_no_newline(logger, f"{GREY}[ENCODER]{RESET} {label}: "
                                        f"{abs(savings_percent)}% {GREY}|{RESET}{formatted_initial} → {formatted_result}{GREY}|{RESET}")

    return updated_filenames, resolved_targets
