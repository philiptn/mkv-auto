import os
import time
import shutil
import requests
import re
import logging
import json


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

# === Environment variables ===
QBITTORRENT_URL = os.getenv('QBITTORRENT_URL', '').rstrip('/')
QBITTORRENT_USERNAME = os.getenv('QBITTORRENT_USERNAME')
QBITTORRENT_PASSWORD = os.getenv('QBITTORRENT_PASSWORD')
try:
    TARGETS = json.loads(os.getenv("TARGETS", "{}"))
    if not isinstance(TARGETS, dict):
        raise ValueError("TARGETS must be a JSON object mapping tags to destination folders.")
except Exception as e:
    log.error(f"❌ Failed to parse TARGETS env variable: {e}")
    TARGETS = {}

TARGET_TAGS = list(TARGETS.keys())
DONE_TAG = os.getenv('DONE_TAG', '✅')
MAPPINGS_FILE = os.getenv('MAPPINGS_FILE')
TRANSLATE_WINDOWS_PATHS = os.getenv('TRANSLATE_WINDOWS_PATHS', 'false').lower() == 'true'

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

        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code} - {response.text}")

        if response.text.strip() != "Ok.":
            raise Exception(f"Unexpected response: {response.text}")

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
    try:
        source = translate_path(os.path.join(torrent['save_path'], torrent['name']), mappings)

        matched_tag = next((tag for tag in torrent.get('tags', '').split(',') if tag in TARGETS), None)
        if not matched_tag:
            log.warning(f"⚠️ No matching tag in TARGETS for torrent '{torrent['name']}', skipping.")
            return

        destination_folder = TARGETS[matched_tag]
        final_destination = os.path.join(destination_folder, torrent['name'])

        if os.path.exists(final_destination):
            log.warning(f"⚠️ Destination already exists, skipping: {final_destination}")
            return

        if os.path.isdir(source):
            log.info(f"📂 Copying folder: {source} -> {final_destination}")
            shutil.copytree(source, final_destination)
            return 0

        elif os.path.isfile(source):
            log.info(f"📄 Copying file: {source} -> {final_destination}")
            os.makedirs(os.path.dirname(final_destination), exist_ok=True)
            shutil.copy2(source, final_destination)
            return 0

        else:
            log.error(f"❌ Source does not exist: {source}")
            return -1

    except Exception as e:
        log.error(f"❌ Failed to copy torrent '{torrent['name']}': {e}")
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

            if response.status_code == 200:
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

        if response.status_code == 200:
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

            if response.status_code == 200:
                log.info(f"✅ Removed {'tag' if len(tags_to_remove) == 1 else 'tags'} '{', '.join(tags_to_remove)}' from torrent {torrent['hash']}")
            else:
                log.error(f"❌ Failed to remove tags '{', '.join(tags_to_remove)}' from torrent {torrent['hash']}: {response.status_code} - {response.text}")
        else:
            log.info(f"ℹ️ No matching tags to remove for torrent {torrent['hash']}")

        # Add failed tag
        response = qbittorrent_request(
            "post",
            "/api/v2/torrents/addTags",
            data={"hashes": torrent['hash'], "tags": "✘"}
        )

        if response.status_code == 200:
            log.info(f"✅ Added tag '✘' to torrent {torrent['hash']}\n")
        else:
            log.error(f"❌ Failed to add tag '✘' to torrent {torrent['hash']}: {response.status_code} - {response.text}")

    except Exception as e:
        log.error(f"❌ Exception while setting tags for torrent {torrent['hash']}: {e}")


def main():
    # Startup info
    log.info("🚀 Starting qBittorrent Automation Service")
    log.info(f"🌐 QBITTORRENT_URL = {QBITTORRENT_URL}")
    log.info(f"👤 QBITTORRENT_USERNAME = {QBITTORRENT_USERNAME}")
    log.info(f"🎯 TARGETS = {json.dumps(TARGETS, indent=2)}")
    log.info(f"🏷️ DONE_TAG = {DONE_TAG}")
    log.info(f"🗺️ MAPPINGS_FILE = {MAPPINGS_FILE}")
    log.info(f"🧩 TRANSLATE_WINDOWS_PATHS = {TRANSLATE_WINDOWS_PATHS}\n")

    login()

    while True:
        try:
            mappings = {}
            if TRANSLATE_WINDOWS_PATHS:
                mappings = load_path_mappings(MAPPINGS_FILE)

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
