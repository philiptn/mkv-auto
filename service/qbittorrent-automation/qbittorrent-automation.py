import os
import time
import shutil
import requests
import re
import logging


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
TARGET_TAG = os.getenv('TARGET_TAG')
DONE_TAG = os.getenv('DONE_TAG', '✅')
DESTINATION_FOLDER = os.getenv('DESTINATION_FOLDER')
MAPPINGS_FILE = os.getenv('MAPPINGS_FILE')
TRANSLATE_WINDOWS_PATHS = os.getenv('TRANSLATE_WINDOWS_PATHS', 'false').lower() == 'true'

session = requests.Session()


def load_path_mappings(file_path):
    mappings = {}
    if not os.path.isfile(file_path):
        log.info(f"No mapping file found at {file_path}. Skipping path translation.")
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
                log.warning(f"Invalid mapping line, skipping: {line}")

    return mappings


def login():
    log.info(f"Attempting to connect to qBittorrent at {QBITTORRENT_URL}")
    try:
        response = session.post(f"{QBITTORRENT_URL}/api/v2/auth/login", data={
            "username": QBITTORRENT_USERNAME,
            "password": QBITTORRENT_PASSWORD
        }, timeout=10)

        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code} - {response.text}")

        if response.text.strip() != "Ok.":
            raise Exception(f"Unexpected response: {response.text}")

        log.info("Successfully logged in to qBittorrent!")

    except Exception as e:
        log.error(f"Failed to connect or authenticate to qBittorrent: {e}")
        raise e


def get_completed_torrents():
    try:
        response = session.get(f"{QBITTORRENT_URL}/api/v2/torrents/info", params={
            "filter": "completed",
            "tag": TARGET_TAG
        }, timeout=10)

        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code} - {response.text}")

        torrents = response.json()
        if torrents:
            log.info(f"Found {len(torrents)} completed torrents with tag '{TARGET_TAG}'")
        return torrents

    except Exception as e:
        log.error(f"Error fetching torrents: {e}")
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
            log.info(f"Translated '{windows_path}' -> '{linux_path}'")
            return linux_path

    raise ValueError(f"No mapping found for path: {windows_path}")


def copy_torrent_content(torrent, mappings):
    try:
        source = translate_path(os.path.join(torrent['save_path'], torrent['name']), mappings)
        destination = os.path.join(DESTINATION_FOLDER, torrent['name'])

        if os.path.exists(destination):
            log.warning(f"Destination already exists, skipping: {destination}")
            return

        if os.path.isdir(source):
            log.info(f"Copying folder: {source} -> {destination}")
            shutil.copytree(source, destination)
        elif os.path.isfile(source):
            log.info(f"Copying file: {source} -> {destination}")
            os.makedirs(destination, exist_ok=True)
            shutil.copy2(source, destination)
        else:
            log.error(f"Source does not exist: {source}")

    except Exception as e:
        log.error(f"Failed to copy torrent '{torrent['name']}': {e}")


def mark_torrent_done(hash_value):
    try:
        # Remove old tag
        response = session.post(f"{QBITTORRENT_URL}/api/v2/torrents/removeTags", data={
            "hashes": hash_value,
            "tags": TARGET_TAG
        }, timeout=10)

        if response.status_code == 200:
            log.info(f"Removed tag '{TARGET_TAG}' from torrent {hash_value}")
        else:
            log.error(f"Failed to remove tag '{TARGET_TAG}' from torrent {hash_value}: {response.status_code} - {response.text}")

        # Add done tag
        response = session.post(f"{QBITTORRENT_URL}/api/v2/torrents/addTags", data={
            "hashes": hash_value,
            "tags": DONE_TAG
        }, timeout=10)

        if response.status_code == 200:
            log.info(f"Added tag '{DONE_TAG}' to torrent {hash_value}")
        else:
            log.error(f"Failed to add tag '{DONE_TAG}' to torrent {hash_value}: {response.status_code} - {response.text}")

    except Exception as e:
        log.error(f"Exception while setting tags for torrent {hash_value}: {e}")


def main():
    # Startup info
    log.info("🚀 Starting qBittorrent Automation Service")
    log.info(f"🌐 QBITTORRENT_URL = {QBITTORRENT_URL}")
    log.info(f"👤 QBITTORRENT_USERNAME = {QBITTORRENT_USERNAME}")
    log.info(f"🏷️ TARGET_TAG = {TARGET_TAG}")
    log.info(f"🏷️ DONE_TAG = {DONE_TAG}")
    log.info(f"📂 DESTINATION_FOLDER = {DESTINATION_FOLDER}")
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
                log.info(f"Processing torrent: {torrent['name']} | Hash: {torrent['hash']}")
                copy_torrent_content(torrent, mappings)
                mark_torrent_done(torrent['hash'])

        except Exception as e:
            log.exception(f"Fatal error in main loop: {e}")

        time.sleep(3)


if __name__ == "__main__":
    main()
