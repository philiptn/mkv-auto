"""Pre-flight preview of the output path MKV-Auto would produce for an input file.

Mirrors the renames the pipeline performs *before* resolve_output_target() sees a
file, then calls resolve_output_target() itself. Every step is a pure function of
the filename, its subfolder and the config - no file I/O, no media probing - so a
path can be answered for a file that is still downloading, or does not exist yet.

Stages that are deliberately NOT mirrored, because they cannot be derived from a
filename alone:

  extract_archives              archive contents are unknown pre-flight
  process_extras                needs the sibling file list in the same folder
  remove_sample_files_and_dirs  deletes rather than renames
  detect_dolby_vision()         reads the video stream; the filename is used as a
                                proxy here, so a DV file whose name carries no DV
                                token (or vice versa) can be predicted wrongly
  TVMaze availability           under NORMALIZE_FILENAMES=full a lookup failure
                                changes the name; callers should check the
                                returned 'full_info_found' before relying on it

Callers that act on the prediction (the qBittorrent live copier) filter out
extras and samples up front so the first three can never apply.
"""

import os

from modules.misc import config, check_config, reformat_filename
from modules.file_operations import (
    CONVERTIBLE_VIDEO_EXTENSIONS,
    detect_dynamic_range_from_filename,
    fix_episode_naming_name,
    replace_tags_in_file,
    resolve_output_target,
    upgrade_dv_to_dv_hdr_filename,
)
from modules.media_encoder import compute_post_encode_filename


def preview_pipeline_filename(logger, debug, original_name, relative_dir):
    """Return the name resolve_output_target() will be handed for original_name.

    Follows the same order as mkv_auto(): the pre-flatten renames (mkv-auto.py
    lines 163-166), then the post-processing renames that feed back into naming
    through remap_origins() (lines 324-333).
    """
    name = original_name

    # 1. convert_all_videos_to_mkv() remuxes other containers into .mkv
    if name.lower().endswith(CONVERTIBLE_VIDEO_EXTENSIONS):
        name = os.path.splitext(name)[0] + '.mkv'

    # 2. rename_others_file_to_folder(): an .mkv that classifies as "others"
    #    inherits its parent folder's name. Mirrors the original's use of the
    #    second return value of reformat_filename() on both checks. With
    #    relative_dir empty the real run sees the temp dir as the parent, which
    #    always classifies as others and hits the early continue.
    if name.endswith('.mkv') and relative_dir:
        others_folder = check_config(config, 'general', 'others_folder')
        parent_folder_name = os.path.basename(relative_dir.rstrip('/'))
        _, parent_reformatted = reformat_filename(
            parent_folder_name + '.mkv', False, False, False, logger)
        if not parent_reformatted.startswith(others_folder):
            _, new_filename = reformat_filename(name, False, False, False, logger)
            if new_filename.startswith(others_folder):
                name = f"{parent_folder_name}.{name.split('.')[-1]}"

    # 3. fix_episodes_naming()
    name = fix_episode_naming_name(name)

    # 4. remove_clutter_process() rewrites the release tag; remap_origins()
    #    carries the new name into resolution.
    file_tag = check_config(config, 'general', 'file_tag')
    if file_tag.lower() != 'default' and not name.lower().startswith('snapchat'):
        name = replace_tags_in_file(name, file_tag)

    # 5/6. The encoder rename and the Dolby Vision rename are mutually exclusive
    #      in mkv_auto() (convert_dovi_files only runs when the encoder is off).
    if check_config(config, 'media-encoder', 'enable_media_encoder'):
        output_codec = check_config(config, 'media-encoder', 'output_codec')
        # encode_media_files() only probes for Dolby Vision on h265; the
        # filename stands in for the stream probe here.
        is_dovi = (output_codec.lower() == 'h265'
                   and detect_dynamic_range_from_filename(name)['is_dv'])
        name = compute_post_encode_filename(name, output_codec, is_dovi)
    elif check_config(config, 'video', 'convert_dolby_vision_to_p8'):
        dynamic = detect_dynamic_range_from_filename(name)
        if dynamic['is_dv'] and not dynamic['is_hdr']:
            name = upgrade_dv_to_dv_hdr_filename(name)

    return name


def preview_output_target(logger, debug, relative_path, output_folder):
    """Resolve the output target for a file at `relative_path` under the input root.

    Returns the resolve_output_target() dict plus 'previewed_name' (the name the
    pipeline renames to before resolution) and 'relative_path' (the destination
    relative to output_folder, for callers whose output root differs from ours).
    """
    if not relative_path or not relative_path.strip():
        raise ValueError("a non-empty path is required")

    normalized = relative_path.replace('\\', '/')
    if normalized.rstrip().endswith('/'):
        raise ValueError(f"'{relative_path}' names a directory, not a file")

    normalized = normalized.strip('/')
    relative_dir = os.path.dirname(normalized)
    original_name = os.path.basename(normalized)
    if not original_name:
        raise ValueError(f"'{relative_path}' does not name a file")

    name = preview_pipeline_filename(logger, debug, original_name, relative_dir)

    # resolve_output_target() takes input_file_path only to satisfy its
    # signature - it never reads it, and the file need not exist.
    target = resolve_output_target(
        logger, debug, relative_path, output_folder, relative_dir, name)

    target['previewed_name'] = name
    target['relative_path'] = os.path.join(
        target['output_folder'], target['restored_filename'])
    return target
