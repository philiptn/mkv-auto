import os
import requests
from thefuzz import fuzz
import re

from modules.misc import *


def update_radarr_path(logger, movie_name, new_folder_name):
    radarr_url = check_config(config, 'integrations', 'radarr_url')
    radarr_api_key = check_config(config, 'integrations', 'radarr_api_key')

    headers = {'X-Api-Key': radarr_api_key}
    response = requests.get(f'{radarr_url}/api/v3/movie', headers=headers)
    response.raise_for_status()
    movies = response.json()

    best_match = None
    highest_score = 0
    for movie in movies:
        title = movie['title']
        score = fuzz.token_set_ratio(movie_name.lower(), title.lower())
        if score > highest_score:
            highest_score = score
            best_match = movie

    if not best_match or highest_score < 70:
        raise ValueError(f"No sufficiently close match found for '{movie_name}'")

    old_path = best_match['path']
    parent_dir = os.path.dirname(old_path)
    new_path = os.path.join(parent_dir, new_folder_name)

    if old_path == new_path:
        log_debug(logger, f"No path update needed for '{best_match['title']}'. Already at '{new_path}'")
        return None

    if not os.path.exists(new_path):
        raise FileNotFoundError(f"The folder '{new_path}' does not exist.")

    best_match['path'] = new_path
    best_match['moveOptions'] = {"moveFiles": False}  # Assume files already moved
    update_url = f"{radarr_url}/api/v3/movie/{best_match['id']}"
    response = requests.put(update_url, json=best_match, headers=headers)
    response.raise_for_status()

    log_debug(logger, f"Updated movie path for '{best_match['title']}' to '{new_path}'")
    return new_path


def update_sonarr_path(logger, episode_name, new_folder_name):
    sonarr_url = check_config(config, 'integrations', 'sonarr_url')
    sonarr_api_key = check_config(config, 'integrations', 'sonarr_api_key')

    headers = {'X-Api-Key': sonarr_api_key}
    shows = requests.get(f'{sonarr_url}/api/v3/series', headers=headers).json()

    match = re.match(r'(.+?)\s*\(.*?\)\s*-\s*S\d+E\d+', episode_name, re.IGNORECASE)
    if not match:
        raise ValueError("Invalid format. Expected: 'TV Show (2010) - S01E01'")
    series_name = match.group(1).strip()

    best_show = None
    highest_score = 0
    for show in shows:
        score = fuzz.token_set_ratio(series_name.lower(), show['title'].lower())
        if score > highest_score:
            highest_score = score
            best_show = show

    if not best_show or highest_score < 70:
        raise ValueError(f"No sufficiently close match found for '{series_name}'")

    old_path = best_show['path']
    parent_dir = os.path.dirname(old_path)
    new_path = os.path.join(parent_dir, new_folder_name)

    if old_path == new_path:
        log_debug(logger, f"No path update needed for '{best_show['title']}'. Already at '{new_path}'")
        return None

    if not os.path.exists(new_path):
        raise FileNotFoundError(f"The folder '{new_path}' does not exist.")

    best_show['path'] = new_path
    update_url = f"{sonarr_url}/api/v3/series/{best_show['id']}"
    response = requests.put(update_url, json=best_show, headers=headers)
    response.raise_for_status()

    log_debug(logger, f"Updated series path for '{best_show['title']}' to '{new_path}'")
    return new_path
