import os
import time
import shutil
import requests
import re

QBITTORRENT_URL = os.getenv('QBITTORRENT_URL')
QBITTORRENT_USERNAME = os.getenv('QBITTORRENT_USERNAME')
QBITTORRENT_PASSWORD = os.getenv('QBITTORRENT_PASSWORD')
TARGET_TAG = os.getenv('TARGET_TAG')
INPUT_FOLDER = os.getenv('INPUT_FOLDER')
MAPPINGS_FILE = os.getenv('MAPPINGS_FILE')
TRANSLATE_WINDOWS_PATHS = os.getenv('TRANSLATE_WINDOWS_PATHS', 'false').lower() == 'true'

session = requests.Session()


def load_path_mappings(file_path):
    mappings = {}
    if not os.path.isfile(file_path):
        print(f"ℹ️ No mapping file found at {file_path}. Skipping path translation.")
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
                print(f"⚠️ Invalid mapping line, skipping: {line}")

    print(f"✅ Loaded {len(mappings)} path mappings from {file_path}")
    return mappings


def login():
    print(f"🔌 Attempting to connect to qBittorrent at {QBITTORRENT_URL}")
    try:
        response = session.post(f"{QBITTORRENT_URL}/api/v2/auth/login", data={
            "username": QBITTORRENT_USERNAME,
            "password": QBITTORRENT_PASSWORD
        }, timeout=10)

        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code} - {response.text}")

        if response.text.strip() != "Ok.":
            raise Exception(f"Unexpected response: {response.text}")

        print("✅ Successfully logged in to qBittorrent")

    except Exception as e:
        print(f"❌ Failed to connect or authenticate to qBittorrent: {e}")
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
        print(f"ℹ️ Found {len(torrents)} completed torrents with tag '{TARGET_TAG}'")
        return torrents

    except Exception as e:
        print(f"❌ Error fetching torrents: {e}")
        return []


def normalize_windows_path(path):
    # Normalize slashes and lower case
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
            print(f"🔄 Translated '{windows_path}' -> '{linux_path}'")
            return linux_path

    raise ValueError(f"No mapping found for path: {windows_path}")


def copy_torrent_content(torrent, mappings):
    try:
        # Build source path (parent + name)
        source = translate_path(os.path.join(torrent['save_path'], torrent['name']), mappings)
        destination = os.path.join(INPUT_FOLDER, torrent['name'])

        if os.path.exists(destination):
            print(f"⚠️ Destination already exists, skipping: {destination}")
            return

        # Check if source is a file or directory
        if os.path.isdir(source):
            print(f"📂 Copying folder: {source} -> {destination}")
            shutil.copytree(source, destination)
        elif os.path.isfile(source):
            print(f"📄 Copying file: {source} -> {destination}")
            os.makedirs(destination, exist_ok=True)  # create target dir
            shutil.copy2(source, destination)
        else:
            print(f"❌ Source does not exist: {source}")

    except Exception as e:
        print(f"❌ Failed to copy torrent '{torrent['name']}': {e}")


def mark_torrent_done(hash_value):
    try:
        response = session.post(f"{QBITTORRENT_URL}/api/v2/torrents/setTags", data={
            "hashes": hash_value,
            "tags": "✅"
        }, timeout=10)

        if response.status_code == 200:
            print(f"✅ Set tag '✅' for torrent {hash_value}")
        else:
            print(f"❌ Failed to set tag for torrent {hash_value}: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Exception while setting tag for torrent {hash_value}: {e}")


def main():
    # Print startup config for debugging
    print(f"🚀 Starting qBittorrent Automation Service")
    print(f"🌐 QBITTORRENT_URL = {QBITTORRENT_URL}")
    print(f"👤 QBITTORRENT_USERNAME = {QBITTORRENT_USERNAME}")
    print(f"🏷️ TARGET_TAG = {TARGET_TAG}")
    print(f"📂 INPUT_FOLDER = {INPUT_FOLDER}")
    print(f"🗺️ MAPPINGS_FILE = {MAPPINGS_FILE}")
    print(f"🧩 TRANSLATE_WINDOWS_PATHS = {TRANSLATE_WINDOWS_PATHS}")
    print()

    login()

    while True:
        try:
            mappings = {}
            if TRANSLATE_WINDOWS_PATHS:
                mappings = load_path_mappings(MAPPINGS_FILE)

            torrents = get_completed_torrents()

            for torrent in torrents:
                print(f"🎉 Processing: {torrent['name']}")
                copy_torrent_content(torrent, mappings)
                mark_torrent_done(torrent['hash'])
                print()

        except Exception as e:
            print(f"❌ Fatal error in main loop: {e}")

        time.sleep(10)


if __name__ == "__main__":
    main()
