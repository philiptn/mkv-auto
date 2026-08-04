import os
import time
import shutil
import signal
import requests
import re
import logging
import json

from livecopy import LiveCopyManager, QueueResolver


# === Setup logging ===
LOG_FILE_NAME = os.getenv('LOG_FILE')
log_handlers = [logging.StreamHandler()]  # Always log to console

if LOG_FILE_NAME:
    # Write to current working directory
    log_file_path = os.path.join(os.getcwd(), LOG_FILE_NAME)
    try:
        log_handlers.append(logging.FileHandler(log_file_path, mode='a', encoding='utf-8'))
    except Exception as e:
        print(f"❌ Failed to set up log file at {log_file_path}: {e}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=log_handlers
)

log = logging.getLogger()

TARGET_KEYS = ('input', 'output')


def parse_targets():
    """Parse TARGETS into {tag: {'input': folder, 'output': folder or None}}.

    TARGETS is the only place folders are configured. Each tag describes one
    MKV-Auto instance, and there can be as many as you like:

        {"mkv-auto": {"input": "/srv/mkv/input", "output": "/srv/mkv/output"}}

    'output' is what enables live copy for that tag, so every instance live
    copies to its own output folder. A bare string is accepted as shorthand for
    {"input": <string>}, which is the original format.
    """
    try:
        parsed = json.loads(os.getenv("TARGETS", "{}"))
        if not isinstance(parsed, dict):
            raise ValueError("TARGETS must be a JSON object mapping tags to folders.")
    except Exception as e:
        log.error(f"❌ Failed to parse TARGETS env variable: {e}")
        return {}

    targets = {}
    for tag, spec in parsed.items():
        if isinstance(spec, str):
            spec = {'input': spec}
        if not isinstance(spec, dict):
            log.error(f"❌ TARGETS['{tag}'] must be a folder path or an object, skipping.")
            continue

        unknown = [key for key in spec if key not in TARGET_KEYS]
        if unknown:
            log.warning(f"⚠️ Ignoring unknown key(s) in TARGETS['{tag}']: {unknown}")

        entry = {key: (str(spec[key]).rstrip('/') or None) if spec.get(key) else None
                 for key in TARGET_KEYS}

        if not entry['input']:
            log.error(f"❌ TARGETS['{tag}'] has no usable 'input' folder, skipping.")
            continue

        if entry['input'] == entry['output']:
            log.error(f"❌ TARGETS['{tag}'] uses the same folder for input and output. "
                      f"A live copy landing in the input folder would be reprocessed "
                      f"forever - ignoring its output folder.")
            entry['output'] = None

        targets[tag] = entry
    return targets


# === Environment variables ===
QBITTORRENT_URL = os.getenv('QBITTORRENT_URL', '').rstrip('/')
QBITTORRENT_USERNAME = os.getenv('QBITTORRENT_USERNAME')
QBITTORRENT_PASSWORD = os.getenv('QBITTORRENT_PASSWORD')
TARGETS = parse_targets()

TARGET_TAGS = list(TARGETS.keys())
DONE_TAG = os.getenv('DONE_TAG', '✔')
FAILED_TAG = os.getenv('FAILED_TAG', '✘')
MAPPINGS_FILE = os.getenv('MAPPINGS_FILE')
TRANSLATE_WINDOWS_PATHS = os.getenv('TRANSLATE_WINDOWS_PATHS', 'false').lower() == 'true'

# === Sequential live copy ===
LIVE_COPY = os.getenv('LIVE_COPY', 'false').lower() == 'true'
LIVE_COPY_SET_SEQUENTIAL = os.getenv('LIVE_COPY_SET_SEQUENTIAL', 'true').lower() == 'true'

LIVE_COPY_RESOLVE_DIR = os.getenv('LIVE_COPY_RESOLVE_DIR') or ''
LIVE_COPY_RESOLVE_TIMEOUT = float(os.getenv('LIVE_COPY_RESOLVE_TIMEOUT', '120'))
LIVE_COPY_INTERVAL = float(os.getenv('LIVE_COPY_INTERVAL', '10'))
LIVE_COPY_MAX_WORKERS = int(os.getenv('LIVE_COPY_MAX_WORKERS', '4'))
LIVE_COPY_MIN_SIZE_MB = int(os.getenv('LIVE_COPY_MIN_SIZE_MB', '200'))
LIVE_COPY_EXTENSIONS = tuple(
    e.strip().lower() for e in os.getenv(
        'LIVE_COPY_EXTENSIONS',
        '.mkv,.mp4,.avi,.m4v,.ts,.mov,.wmv,.flv,.webm').split(',') if e.strip())
LIVE_COPY_CHUNK_MB = int(os.getenv('LIVE_COPY_CHUNK_MB', '8'))
LIVE_COPY_STALL_TIMEOUT = float(os.getenv('LIVE_COPY_STALL_TIMEOUT', '1800'))
LIVE_COPY_STATE_DIR = os.getenv(
    'LIVE_COPY_STATE_DIR', os.path.join(os.getcwd(), '.livecopy-state'))

# The queue MKV-Auto's resolve worker watches. It lives inside the target's
# input folder by default, which both services already share, and is hidden from
# the MKV-Auto pipeline because it starts with a dot.
RESOLVE_QUEUE_DIRNAME = '.mkv-auto-resolve'

session = requests.Session()


def load_path_mappings(file_path):
    mappings = {}
    if not os.path.isfile(file_path):
        log.info(f"ℹ️ No mapping file found at {file_path}. Skipping path translation.")
        return mappings

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            match = re.match(r'"(.+?)"\s*->\s*"(.+?)"', line)
            if match:
                win_path, linux_path = match.groups()
                mappings[win_path.strip()] = linux_path.strip()
            else:
                log.warning(f"⚠️ Invalid mapping line, skipping: {line}")

    return mappings


def login():
    log.info(f"🔌 Attempting to connect to qBittorrent at {QBITTORRENT_URL}")
    try:
        response = session.post(f"{QBITTORRENT_URL}/api/v2/auth/login", data={
            "username": QBITTORRENT_USERNAME,
            "password": QBITTORRENT_PASSWORD
        }, timeout=10)

        # qBittorrent 5.2.0 and later answer a successful login with
        # "204 No Content"; earlier versions answer "200 Ok.". Bad credentials
        # are still 200, with the body "Fails.".
        if response.status_code == 204:
            pass
        elif response.status_code == 200:
            if response.text.strip() != "Ok.":
                raise Exception(f"Rejected by qBittorrent: {response.text.strip()}")
        elif response.status_code == 403:
            raise Exception("Banned by qBittorrent after too many failed login "
                            "attempts. Restart qBittorrent or wait it out.")
        else:
            raise Exception(f"HTTP {response.status_code} - {response.text}")

        log.info(f"✅ Successfully logged in to qBittorrent!\n")

    except Exception as e:
        log.error(f"❌ Failed to connect or authenticate to qBittorrent: {e}")
        raise e


def qbittorrent_request(method, endpoint, **kwargs):
    """
    Wrapper around session requests that retries once if session expired (401/403).
    Preserves timeout=10 unless explicitly overridden.
    """
    url = f"{QBITTORRENT_URL}{endpoint}"
    timeout = kwargs.pop("timeout", 10)

    try:
        response = session.request(method, url, timeout=timeout, **kwargs)

        if response.status_code in (401, 403):
            log.warning("🔒 Session expired or unauthorized. Attempting to re-login...")
            login()
            response = session.request(method, url, timeout=timeout, **kwargs)

        return response

    except requests.RequestException as e:
        log.error(f"❌ Request to {endpoint} failed: {e}")
        raise


def get_completed_torrents():
    all_torrents = []
    try:
        for tag in TARGET_TAGS:
            response = qbittorrent_request(
                "get",
                "/api/v2/torrents/info",
                params={"filter": "completed", "tag": tag}
            )

            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code} - {response.text}")

            torrents = response.json()
            if torrents:
                log.info(f"ℹ️ Found {len(torrents)} completed torrents with tag '{tag}'")
                all_torrents.extend(torrents)

        # Deduplicate torrents (sometimes torrents can have multiple tags)
        unique_torrents = {torrent['hash']: torrent for torrent in all_torrents}

        if unique_torrents:
            log.info("⏳ Completed torrents found. Waiting 30 seconds to let files finish writing to disk...")
            time.sleep(30)

        return list(unique_torrents.values())

    except Exception as e:
        log.error(f"❌ Error fetching torrents: {e}")
        return []


def normalize_windows_path(path):
    return path.replace('/', '\\').rstrip('\\/').lower()


def translate_path(windows_path, mappings):
    if not TRANSLATE_WINDOWS_PATHS:
        return windows_path

    win_path_normalized = normalize_windows_path(windows_path)

    for win_prefix, linux_prefix in mappings.items():
        normalized_prefix = normalize_windows_path(win_prefix)

        if win_path_normalized.startswith(normalized_prefix):
            relative_part = windows_path[len(win_prefix):].lstrip("\\/")
            linux_path = os.path.join(linux_prefix, relative_part.replace('\\', '/'))
            log.info(f"🔄 Translated '{windows_path}' -> '{linux_path}'")
            return linux_path

    raise ValueError(f"No mapping found for path: {windows_path}")


def copy_torrent_content(torrent, mappings):
    temp_destination = None

    try:
        source = translate_path(
            os.path.join(torrent['save_path'], torrent['name']),
            mappings
        )

        matched_tag = next(
            (tag for tag in torrent.get('tags', '').split(',') if tag in TARGETS),
            None
        )
        if not matched_tag:
            log.warning(f"⚠️ No matching tag in TARGETS for torrent '{torrent['name']}', skipping.")
            return -1

        destination_folder = TARGETS[matched_tag]['input']
        final_destination = os.path.join(destination_folder, torrent['name'])

        base = os.path.basename(final_destination)
        parent = os.path.dirname(final_destination)
        temp_destination = os.path.join(parent, f".{base}")

        # ensure destination folder exists
        os.makedirs(destination_folder, exist_ok=True)

        # clean up stale temp from previous crash
        if os.path.exists(temp_destination):
            log.warning(f"🧹 Removing stale temp destination: {temp_destination}")
            if os.path.isdir(temp_destination):
                shutil.rmtree(temp_destination, ignore_errors=True)
            else:
                try:
                    os.remove(temp_destination)
                except OSError:
                    pass

        if os.path.exists(final_destination):
            log.warning(f"⚠️ Destination already exists, skipping: {final_destination}")
            return -1

        if os.path.isdir(source):
            log.info(f"📂 Copying folder: {source} -> {temp_destination}")
            shutil.copytree(source, temp_destination)

            # atomic publish
            os.rename(temp_destination, final_destination)
            log.info(f"✅ Folder copy complete: {final_destination}")
            return 0

        elif os.path.isfile(source):
            log.info(f"📄 Copying file: {source} -> {temp_destination}")
            shutil.copy2(source, temp_destination)

            # atomic publish
            os.rename(temp_destination, final_destination)
            log.info(f"✅ File copy complete: {final_destination}")
            return 0

        else:
            log.error(f"❌ Source does not exist: {source}")
            return -1

    except Exception as e:
        log.error(f"❌ Failed to copy torrent '{torrent.get('name')}': {e}")

        # cleanup temp on failure
        if temp_destination and os.path.exists(temp_destination):
            if os.path.isdir(temp_destination):
                shutil.rmtree(temp_destination, ignore_errors=True)
            else:
                try:
                    os.remove(temp_destination)
                except OSError:
                    pass

        return -1


def mark_torrent_done(torrent):
    try:
        torrent_tags = torrent.get('tags', '').split(',')
        tags_to_remove = [tag for tag in torrent_tags if tag in TARGET_TAGS]

        if tags_to_remove:
            response = qbittorrent_request(
                "post",
                "/api/v2/torrents/removeTags",
                data={"hashes": torrent['hash'], "tags": ','.join(tags_to_remove)}
            )

            # .ok rather than == 200: qBittorrent 5.2.0 answers some endpoints
            # with 204 No Content.
            if response.ok:
                log.info(f"✅ Removed {'tag' if len(tags_to_remove) == 1 else 'tags'} '{', '.join(tags_to_remove)}' from torrent {torrent['hash']}")
            else:
                log.error(f"❌ Failed to remove tags '{', '.join(tags_to_remove)}' from torrent {torrent['hash']}: {response.status_code} - {response.text}")
        else:
            log.info(f"ℹ️ No matching tags to remove for torrent {torrent['hash']}")

        # Add done tag
        response = qbittorrent_request(
            "post",
            "/api/v2/torrents/addTags",
            data={"hashes": torrent['hash'], "tags": DONE_TAG}
        )

        if response.ok:
            log.info(f"✅ Added tag '{DONE_TAG}' to torrent {torrent['hash']}\n")
        else:
            log.error(f"❌ Failed to add tag '{DONE_TAG}' to torrent {torrent['hash']}: {response.status_code} - {response.text}")

    except Exception as e:
        log.error(f"❌ Exception while setting tags for torrent {torrent['hash']}: {e}")


def mark_torrent_failed(torrent):
    try:
        torrent_tags = torrent.get('tags', '').split(',')
        tags_to_remove = [tag for tag in torrent_tags if tag in TARGET_TAGS]

        if tags_to_remove:
            response = qbittorrent_request(
                "post",
                "/api/v2/torrents/removeTags",
                data={"hashes": torrent['hash'], "tags": ','.join(tags_to_remove)}
            )

            # .ok rather than == 200: qBittorrent 5.2.0 answers some endpoints
            # with 204 No Content.
            if response.ok:
                log.info(f"✅ Removed {'tag' if len(tags_to_remove) == 1 else 'tags'} '{', '.join(tags_to_remove)}' from torrent {torrent['hash']}")
            else:
                log.error(f"❌ Failed to remove tags '{', '.join(tags_to_remove)}' from torrent {torrent['hash']}: {response.status_code} - {response.text}")
        else:
            log.info(f"ℹ️ No matching tags to remove for torrent {torrent['hash']}")

        # Add failed tag
        response = qbittorrent_request(
            "post",
            "/api/v2/torrents/addTags",
            data={"hashes": torrent['hash'], "tags": FAILED_TAG}
        )

        if response.ok:
            log.info(f"✅ Added tag '{FAILED_TAG}' to torrent {torrent['hash']}\n")
        else:
            log.error(f"❌ Failed to add tag '{FAILED_TAG}' to torrent {torrent['hash']}: {response.status_code} - {response.text}")

    except Exception as e:
        log.error(f"❌ Exception while setting tags for torrent {torrent['hash']}: {e}")


def resolve_queue_dir(tag):
    """Folder where MKV-Auto's resolve worker answers lookups for this tag."""
    if LIVE_COPY_RESOLVE_DIR:
        return LIVE_COPY_RESOLVE_DIR
    return os.path.join(TARGETS[tag]['input'], RESOLVE_QUEUE_DIRNAME)


def build_live_copy_manager():
    """Validate the live copy configuration and build the manager, or None."""
    if not LIVE_COPY:
        return None

    configured = {tag: entry['output'] for tag, entry in TARGETS.items()
                  if entry['output']}
    if not configured:
        log.error("❌ LIVE_COPY is enabled but no TARGETS entry has an 'output' folder. "
                  "Disabling live copy.")
        return None

    # A live copy landing in any input folder would be picked up as new media and
    # reprocessed forever - including another tag's input folder.
    inputs = {entry['input'] for entry in TARGETS.values()}
    outputs = {}
    for tag, folder in configured.items():
        if folder in inputs:
            log.error(f"❌ Live copy for tag '{tag}' disabled: its output folder "
                      f"'{folder}' is also a TARGETS input folder.")
            continue
        try:
            os.makedirs(folder, exist_ok=True)
            os.makedirs(resolve_queue_dir(tag), exist_ok=True)
        except OSError as e:
            log.error(f"❌ Live copy for tag '{tag}' disabled, cannot use '{folder}': {e}")
            continue
        outputs[tag] = folder

    if not outputs:
        return None

    log.info(f"📡 Live copying {len(outputs)} of {len(TARGETS)} tag(s): "
             f"{', '.join(sorted(outputs))}")

    resolver = QueueResolver(resolve_queue_dir, LIVE_COPY_RESOLVE_TIMEOUT, log)
    return LiveCopyManager(
        qbittorrent_request, outputs, resolver, log, LIVE_COPY_STATE_DIR,
        interval=LIVE_COPY_INTERVAL,
        max_workers=LIVE_COPY_MAX_WORKERS,
        min_size=LIVE_COPY_MIN_SIZE_MB * 1024 ** 2,
        extensions=LIVE_COPY_EXTENSIONS,
        chunk_size=LIVE_COPY_CHUNK_MB * 1024 ** 2,
        stall_timeout=LIVE_COPY_STALL_TIMEOUT,
        set_sequential=LIVE_COPY_SET_SEQUENTIAL,
        translate=translate_path,
    )


def main():
    # Startup info
    log.info("🚀 Starting qBittorrent Automation Service")
    log.info(f"🌐 QBITTORRENT_URL = {QBITTORRENT_URL}")
    log.info(f"👤 QBITTORRENT_USERNAME = {QBITTORRENT_USERNAME}")
    log.info(f"🎯 TARGETS = {json.dumps(TARGETS, indent=2)}")
    log.info(f"🏷️ DONE_TAG = {DONE_TAG}")
    log.info(f"🏷️ FAILED_TAG = {FAILED_TAG}")
    log.info(f"🗺️ MAPPINGS_FILE = {MAPPINGS_FILE}")
    log.info(f"🧩 TRANSLATE_WINDOWS_PATHS = {TRANSLATE_WINDOWS_PATHS}")
    log.info(f"📡 LIVE_COPY = {LIVE_COPY}")
    if LIVE_COPY:
        log.info(f"📡 LIVE_COPY_SET_SEQUENTIAL = {LIVE_COPY_SET_SEQUENTIAL}")
        log.info(f"📡 LIVE_COPY_MIN_SIZE_MB = {LIVE_COPY_MIN_SIZE_MB}")
    log.info("")

    login()

    manager = build_live_copy_manager()
    if manager:
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, lambda *_: (manager.shutdown(), os._exit(0)))

    while True:
        try:
            mappings = {}
            if TRANSLATE_WINDOWS_PATHS:
                mappings = load_path_mappings(MAPPINGS_FILE)

            # Before get_completed_torrents(), which sleeps 30s whenever it finds
            # anything and would otherwise starve the live copier.
            if manager:
                manager.tick(mappings)

            torrents = get_completed_torrents()

            for torrent in torrents:
                log.info(f"🔍 Processing torrent: {torrent['name']} | Hash: {torrent['hash']}")
                return_code = copy_torrent_content(torrent, mappings)
                if return_code == 0:
                    mark_torrent_done(torrent)
                else:
                    mark_torrent_failed(torrent)

        except Exception as e:
            log.exception(f"❌ Fatal error in main loop: {e}")

        time.sleep(3)


if __name__ == "__main__":
    main()
