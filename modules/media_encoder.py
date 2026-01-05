import os
import subprocess
import sys
import re
import shutil  # Added to enable directory removal
import platform
import time
import math
import concurrent.futures

from modules.misc import *


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


def encode_single_video_file(logger, debug, input_file, dirpath, max_cpu_usage):
    crop_values = check_config(config, 'media-encoder', 'crop_values')
    limit_resolution = check_config(config, 'media-encoder', 'limit_resolution')
    output_codec = check_config(config, 'media-encoder', 'output_codec')
    quality_crf = check_config(config, 'media-encoder', 'quality_crf')
    encoding_speed = check_config(config, 'media-encoder', 'encoding_speed')
    tune = check_config(config, 'media-encoder', 'tune')
    custom_params = check_config(config, 'media-encoder', 'custom_params')

    filesize_info = {
        "initial_file_size": 0,
        "resulting_file_size": 0
    }

    media_file = os.path.join(dirpath, input_file)
    filesize_info["initial_file_size"] = os.path.getsize(media_file)

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

    # Map user-friendly codec names to ffmpeg encoder names
    codec_map = {
        'h264': 'libx264',
        'h265': 'libx265',
        'hevc': 'libx265',
        'vp9': 'libvpx-vp9',
        'av1': 'libsvtav1'
    }

    # Define encoder-specific options
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
    codec = codec_map[output_codec]
    tune_option = tune
    encoder_speed = encoding_speed
    cpu_usage_percentage = float(max_cpu_usage)
    user_custom_ffmpeg = custom_params

    # Map 'medium' and 'slow' to numerical speed values for AV1 and VP9 codecs
    if encoder_speed.lower() in ('medium', 'slow') and codec in ('libsvtav1', 'libvpx-vp9'):
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

    # CPU threads calculation
    num_cores = os.cpu_count()
    if codec.lower() == "libx265":
        divisor = 4.3
    else:
        divisor = 0.8
    number_of_threads = max(1, int(num_cores * (cpu_usage_percentage / 100) // divisor))
    # Limit to 16 threads for x264, as recommended here:
    # https://obsproject.com/forum/threads/can-you-please-explain-x264-option-threads.76917/
    if codec.lower() == "libx264":
        number_of_threads = min(16, number_of_threads)
    log_debug(logger, f"File '{input_file}' will use {number_of_threads} threads with {codec}. "
                      f"CPU usage alloc {cpu_usage_percentage}%")

    # Get original dimensions
    orig_width, orig_height = get_video_dimensions(media_file)

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

    filter_chain = []
    if cropping:
        # Crop filter
        crop_filter = f"crop=w=iw-{left}-{right}:h=ih-{top}-{bottom}:x={left}:y={top}"
        filter_chain.append(crop_filter)
    if resizing:
        scale_filter = f"scale=w={output_width}:h={output_height}"
        filter_chain.append(scale_filter)

    # Build filter string
    filter_str = ",".join(filter_chain) if filter_chain else None

    temp_video_file = os.path.join(dirpath, 'temp_video_' + os.path.basename(input_file))
    temp_file = os.path.join(dirpath, 'temp_' + os.path.basename(input_file))
    cmd_ffmpeg = ['ffmpeg', '-y', '-i', media_file]

    if filter_str:
        cmd_ffmpeg.extend(['-vf', filter_str])

    cmd_ffmpeg.extend([
        '-map', 'v:0',  # Map only video
        '-c:v', codec,
        '-crf', quality,
        '-threads', str(number_of_threads),  # Limit CPU usage
    ])

    # Apply the encoder speed/preset depending on the codec
    if codec in ['libx264', 'libx265']:
        # Use '-preset'
        cmd_ffmpeg.extend(['-preset', encoder_speed])
    elif codec == 'libvpx-vp9':
        # For VP9, use '-cpu-used'
        cmd_ffmpeg.extend(['-cpu-used', encoder_speed])
    elif codec == 'libsvtav1':
        # For AV1, use '-preset'
        cmd_ffmpeg.extend(['-preset', encoder_speed])

    # Add pix_fmt if specified for the codec
    if encoder_options[codec]['pix_fmt']:
        cmd_ffmpeg.extend(['-pix_fmt', encoder_options[codec]['pix_fmt']])

    # Add encoder-specific options
    cmd_ffmpeg.extend(encoder_options[codec]['options'])

    # Add tune option if provided
    if tune_option:
        cmd_ffmpeg.extend(['-tune', tune_option])

    # Add user-custom parameters if provided
    if user_custom_ffmpeg.strip():
        # A simple split() handles space-delimited arguments
        cmd_ffmpeg.extend(user_custom_ffmpeg.split())

    try:
        cmd_ffmpeg.append(temp_video_file)
        log_debug(logger, f"[MEDIA-ENCODER] FFmpeg command: '{' '.join(cmd_ffmpeg)}'")
        subprocess.run(cmd_ffmpeg, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        custom_print(logger, f"{RED}[ERROR]{RESET} FFmpeg failed with return code {e.returncode}")
        custom_print(logger, f"{RED}[STDERR]{RESET}\n{YELLOW}{e.stderr.strip()}{RESET}")
        custom_print(logger, f"{RED}[STDOUT]{RESET}\n{YELLOW}{e.stdout.strip()}{RESET}")
        raise

    try:
        cmd_mkvmerge = [
            'mkvmerge',
            '-o', temp_file,
            temp_video_file,
            '--no-video', media_file
        ]
        log_debug(logger, f"[MEDIA-ENCODER] MKVMERGE command: '{' '.join(cmd_mkvmerge)}'")
        subprocess.run(cmd_mkvmerge, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        custom_print(logger, f"{RED}[ERROR]{RESET} MKVMERGE failed with return code {e.returncode}")
        custom_print(logger, f"{RED}[STDERR]{RESET}\n{YELLOW}{e.stderr.strip()}{RESET}")
        custom_print(logger, f"{RED}[STDOUT]{RESET}\n{YELLOW}{e.stdout.strip()}{RESET}")
        raise

    # Cleanup
    os.remove(temp_video_file)
    os.remove(media_file)

    # Substrings to replace with codec_display_name
    replace_substrings = ['HEVC', 'AVC', 'H.265', 'H.264', 'h264', 'h265', 'x264', 'x265', 'VC-1']
    # Substrings to remove
    remove_substrings = ['.REMUX', ' REMUX', 'REMUX']
    # Determine codec display name for filename replacements
    codec_display_name_map = {
        'libx264': 'x264',
        'libx265': 'x265',
        'libvpx-vp9': 'VP9',
        'libsvtav1': 'AV1'
    }
    codec_display_name = codec_display_name_map.get(codec)
    basename = os.path.splitext(os.path.basename(input_file))[0]
    # Replace substrings with codec_display_name
    for substring in replace_substrings:
        pattern = re.compile(re.escape(substring), re.IGNORECASE)
        basename = pattern.sub(codec_display_name, basename)
    # Remove substrings
    for substring in remove_substrings:
        pattern = re.compile(re.escape(substring), re.IGNORECASE)
        basename = pattern.sub('', basename)
    cleaned_filename = os.path.join(basename + '.mkv')

    os.rename(temp_file, os.path.join(dirpath, cleaned_filename))
    filesize_info["resulting_file_size"] = os.path.getsize(os.path.join(dirpath, cleaned_filename))

    return cleaned_filename, filesize_info


def encode_media_files(logger, debug, input_files, dirpath):
    total_files = len(input_files)
    updated_filenames = [None] * total_files
    filesizes_info = [None] * total_files

    output_codec = check_config(config, 'media-encoder', 'output_codec')
    quality_crf = check_config(config, 'media-encoder', 'quality_crf')
    max_cpu_usage = check_config(config, 'general', 'max_cpu_usage')

    crop_values = check_config(config, 'media-encoder', 'crop_values')
    limit_resolution = check_config(config, 'media-encoder', 'limit_resolution')
    encoding_speed = check_config(config, 'media-encoder', 'encoding_speed')
    tune = check_config(config, 'media-encoder', 'tune')
    custom_params = check_config(config, 'media-encoder', 'custom_params')

    log_debug(logger, f"[MEDIA-ENCODER] Output codec: '{output_codec}'")
    log_debug(logger, f"[MEDIA-ENCODER] Quality CRF: '{quality_crf}'")
    log_debug(logger, f"[MEDIA-ENCODER] Max CPU usage: '{max_cpu_usage}'")
    log_debug(logger, f"[MEDIA-ENCODER] Crop values: '{crop_values}'")
    log_debug(logger, f"[MEDIA-ENCODER] Limit resolution: '{limit_resolution}'")
    log_debug(logger, f"[MEDIA-ENCODER] Encoding speed: '{encoding_speed}'")
    log_debug(logger, f"[MEDIA-ENCODER] Tune: '{tune}'")
    log_debug(logger, f"[MEDIA-ENCODER] Custom parameters: '{custom_params}'")

    max_worker_threads = get_worker_thread_count()
    num_workers = min(max_worker_threads, total_files)

    if output_codec == 'h265':
        num_workers = min(2, num_workers)
    elif output_codec == 'h264':
        num_workers = min(4, num_workers)

    per_file_cpu = float(max_cpu_usage) / num_workers

    codec_map = {
        'h265': 'H.265',
        'h264': 'H.264',
        'vp9': 'VP9',
        'av1': 'AV1'
    }
    display_codec = codec_map.get(output_codec.lower(), output_codec)
    speed_print = f"{encoding_speed}-{tune}" if tune else {encoding_speed}

    start_time = time.time()

    header = "FFMPEG"
    description = f"Encode media to {display_codec} {speed_print.upper()} CRF-{quality_crf}"

    print_with_progress(logger, 0, total_files, header=header, description=description)

    # Use ThreadPoolExecutor to handle multithreading
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(encode_single_video_file, logger, debug, input_file, dirpath, per_file_cpu): index for
                   index, input_file in enumerate(input_files)}
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), 1):
            print_with_progress(logger, completed_count, total_files, header=header, description=description)
            try:
                index = futures[future]
                updated_filename, filesize_info = future.result()
                if updated_filename is not None:
                    updated_filenames[index] = updated_filename
                if filesize_info is not None:
                    filesizes_info[index] = filesize_info
            except Exception as e:
                # Print the error and traceback
                custom_print(logger, f"\n{RED}[ERROR]{RESET} {e}")
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                print_no_timestamp(logger, f"\n{RED}[TRACEBACK]{RESET}\n{traceback_str}")
                raise

    end_time = time.time()
    processing_time = end_time - start_time

    # Calculate total initial and resulting sizes
    total_initial_size = sum(info["initial_file_size"] for info in filesizes_info if info)
    total_resulting_size = sum(info["resulting_file_size"] for info in filesizes_info if info)

    savings_percent = 0
    if total_initial_size > 0:
        savings_bytes = total_initial_size - total_resulting_size
        savings_percent = int((savings_bytes / total_initial_size) * 100)

    if savings_percent > 0:
        formatted_initial = format_size(total_initial_size, False)
        formatted_result = format_size(total_resulting_size, False)
        print()
        custom_print(logger, f"{GREY}[FFMPEG]{RESET} Encoding time: "
                             f"{format_time_short(int(processing_time))}")
        custom_print_no_newline(logger, f"{GREY}[FFMPEG]{RESET} Total savings: "
                                        f"{savings_percent}% {GREY}|{RESET}{formatted_initial} → {formatted_result}{GREY}|{RESET}")
    else:
        print()
        custom_print_no_newline(logger, f"{GREY}[FFMPEG]{RESET} Encoding time: "
                                        f"{format_time_short(int(processing_time))}")

    return updated_filenames
