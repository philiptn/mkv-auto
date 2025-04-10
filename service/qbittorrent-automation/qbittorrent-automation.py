import os
import time
import shutil
import requests

QBITTORRENT_URL = os.getenv('QBITTORRENT_URL')
QBITTORRENT_USERNAME = os.getenv('QBITTORRENT_USERNAME')
QBITTORRENT_PASSWORD = os.getenv('QBITTORRENT_PASSWORD')
TARGET_TAG = os.getenv('TARGET_TAG')
INPUT_FOLDER = os.getenv('INPUT_FOLDER')
MAPPINGS_FILE = os.getenv('MAPPINGS_FILE', '/qbittorrent-automation/drive-mappings.txt')
TRANSLATE_WINDOWS_PATHS = os.getenv('TRANSLATE_WINDOWS_PATHS', 'false').lower() == 'true'

session = requests.Session()


def load_path_mappings(file_path):
    mappings = {}
    if not os.path.isfile(file_path):
        return mappings

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Use regex to match the quoted paths
            match = re.match(r'"(.+?)"\s*->\s*"(.+?)"', line)
            if match:
                win_path, linux_path = match.groups()
                mappings[win_path.strip()] = linux_path.strip()
            else:
                print(f"⚠️ Invalid mapping line, skipping: {line}")

    print(f"✅ Loaded {len(mappings)} path mappings")
    return mappings


def login():
    response = session.post(f"{QBITTORRENT_URL}/api/v2/auth/login", data={
        "username": QBITTORRENT_USERNAME,
        "password": QBITTORRENT_PASSWORD
    })
    if response.text != "Ok.":
        raise Exception("Failed to login to qBittorrent")
    print("✅ Logged in to qBittorrent")


def get_completed_torrents():
    response = session.get(f"{QBITTORRENT_URL}/api/v2/torrents/info", params={
        "filter": "completed",
        "tag": TARGET_TAG
    })
    return response.json()


def translate_path(windows_path, mappings):
    # If translation is disabled, return the path as-is
    if not TRANSLATE_WINDOWS_PATHS:
        return windows_path

    win_path_lower = windows_path.replace('/', '\\').lower()

    for win_prefix, linux_prefix in mappings.items():
        normalized_prefix = win_prefix.replace('/', '\\').lower()
        if win_path_lower.startswith(normalized_prefix):
            relative_part = windows_path[len(win_prefix):].lstrip("\\/")
            linux_path = os.path.join(linux_prefix, relative_part.replace('\\', '/'))
            print(f"🔄 Translated '{windows_path}' -> '{linux_path}'")
            return linux_path

    raise ValueError(f"No mapping found for path: {windows_path}")


def copy_torrent_content(torrent, mappings):
    try:
        source = translate_path(torrent['save_path'], mappings)
        destination = os.path.join(INPUT_FOLDER, torrent['name'])

        if os.path.exists(destination):
            print(f"⚠️ Destination already exists, skipping: {destination}")
            return

        print(f"📂 Copying: {source} -> {destination}")
        shutil.copytree(source, destination)

    except Exception as e:
        print(f"❌ Failed to copy torrent '{torrent['name']}': {e}")


def mark_torrent_done(hash_value):
    response = session.post(f"{QBITTORRENT_URL}/api/v2/torrents/setTags", data={
        "hashes": hash_value,
        "tags": "Done"
    })
    if response.status_code == 200:
        print(f"✅ Set tag 'Done' for torrent {hash_value}")
    else:
        print(f"❌ Failed to set tag for torrent {hash_value}: {response.text}")


def main():
    login()

    while True:
        try:
            # Hot-reload mappings file every loop
            mappings = {}
            if TRANSLATE_WINDOWS_PATHS:
                mappings = load_path_mappings(MAPPINGS_FILE)

            torrents = get_completed_torrents()

            for torrent in torrents:
                print(f"🎉 Processing: {torrent['name']}")
                copy_torrent_content(torrent, mappings)
                mark_torrent_done(torrent['hash'])

        except Exception as e:
            print(f"❌ Error: {e}")

        time.sleep(10)


if __name__ == "__main__":
    main()
