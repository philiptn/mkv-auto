"""Lightweight data structures that carry per-file metadata through the
processing pipeline, so the information no longer has to be encoded into
(and parsed back out of) temporary filenames.

Temp files on disk get plain, collision-safe names; the dataclass instance -
not the filename - is the source of truth for track metadata and for the
original source location used to rebuild the output folder structure.
"""

from dataclasses import dataclass, field


@dataclass
class WorkingFile:
    """A media file being processed in the temp working area.

    ``relative_dir`` and ``original_name`` capture where the file came from in
    the input tree so the output folder structure can be rebuilt without
    encoding the path into the filename.
    """
    path: str                 # current temp path (plain name, no encoded metadata)
    original_name: str        # real source filename, used to restore the output name
    relative_dir: str = ""    # source subfolder path relative to the input root ("" if top-level)


@dataclass
class SubtitleTrack:
    """A single subtitle track extracted to / staged as a file on disk."""
    path: str                 # temp file on disk; plain name
    track_id: int             # source MKV track id, or synthetic id for external/downloaded
    language: str             # 3-letter language code
    forced: bool = False
    name: str = ""            # human-readable track name (no base64)
    extension: str = ""       # srt/ass/sup/sub/idx
    source: str = "internal"  # internal | external | downloaded (drives priority merge)


@dataclass
class AudioTrack:
    """A single audio track extracted to / staged as a file on disk."""
    path: str                 # temp file on disk; plain name
    track_id: int             # source MKV track id
    language: str             # language code as carried by the pipeline
    name: str = ""            # human-readable track name
    extension: str = ""       # container/codec extension (e.g. mka)
