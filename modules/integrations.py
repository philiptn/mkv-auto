import os
import requests
from thefuzz import fuzz
import re
from datetime import datetime

from modules.misc import *


def extract_title_and_year(movie_name):
    match = re.match(r'^(.*?)(?:\s*\(?(\d{4})\)?)?$', movie_name)
    if match:
        title = match.group(1).strip()
        year = int(match.group(2)) if match.group(2) else None
        return title, year
    return movie_name.strip(), None


def update_radarr_path(logger, movie_name, new_folder_name):
    radarr_url = check_config(config, 'integrations', 'radarr_url')
    radarr_api_key = check_config(config, 'integrations', 'radarr_api_key')

    headers = {'X-Api-Key': radarr_api_key}
    response = requests.get(f'{radarr_url}/api/v3/movie', headers=headers)
    response.raise_for_status()
    movies = response.json()

    movie_title, movie_year = extract_title_and_year(movie_name)
    best_match = None
    highest_score = 0

    for movie in movies:
        title_candidates = [movie.get('title', '')]
        if movie.get('originalTitle'):
            title_candidates.append(movie['originalTitle'])
        title_candidates += [alt.get('title', '') for alt in movie.get('alternateTitles', [])]

        for title_option in title_candidates:
            score = fuzz.token_set_ratio(movie_title.lower(), title_option.lower())

            # Soft year boosting
            radarr_year = movie.get('year')
            if movie_year and radarr_year:
                year_diff = abs(movie_year - radarr_year)
                if year_diff == 0:
                    score += 10
                elif year_diff == 1:
                    score += 5
            elif not movie_year or not radarr_year:
                score += 2  # Slight boost if year is missing on either side

            if score > highest_score:
                highest_score = score
                best_match = movie

    if not best_match or highest_score < 70:
        log_debug(logger, f"[RADARR] No sufficiently close match found for '{movie_name}'")
        return ''

    old_path = best_match['path']
    parent_dir = os.path.dirname(old_path)
    new_path = os.path.join(parent_dir, new_folder_name)

    if old_path == new_path:
        log_debug(logger, f"[RADARR] No path update needed for '{best_match['title']}'. Already at '{new_path}'")
        return ''

    best_match['path'] = new_path
    best_match['moveOptions'] = {"moveFiles": False}
    update_url = f"{radarr_url}/api/v3/movie/{best_match['id']}"
    response = requests.put(update_url, json=best_match, headers=headers)
    response.raise_for_status()

    log_debug(logger, f"[RADARR] Updated movie path for '{best_match['title']}' to '{new_path}'")

    rescan_payload = {
        "name": "RescanMovie",
        "movieIds": [best_match['id']]
    }
    requests.post(f"{radarr_url}/api/v3/command", json=rescan_payload, headers=headers)

    log_debug(logger, f"[RADARR] Triggered rescan for movie '{best_match['title']}'")

    if old_path == new_path:
        log_debug(logger, f"[RADARR] No path update needed for '{best_match['title']}'. Already at '{new_path}'")
        return ''
    else:
        log_debug(logger, f"[RADARR] Updated movie path for '{best_match['title']}' to '{new_path}'")
        return new_path


def update_sonarr_path(logger, episode_name, new_folder_name):
    sonarr_url = check_config(config, 'integrations', 'sonarr_url')
    sonarr_api_key = check_config(config, 'integrations', 'sonarr_api_key')

    headers = {'X-Api-Key': sonarr_api_key}
    shows = requests.get(f'{sonarr_url}/api/v3/series', headers=headers).json()

    # Try to extract series name and year
    match = re.match(r'(.+?)\s*\((\d{4})\)?\s*-\s*S\d+E\d+', episode_name, re.IGNORECASE)
    if not match:
        raise ValueError("[SONARR] Invalid format. Expected: 'TV Show (2010) - S01E01'")

    series_name = match.group(1).strip()
    series_year = int(match.group(2))

    best_show = None
    highest_score = 0

    for show in shows:
        score = fuzz.token_set_ratio(series_name.lower(), show['title'].lower())

        # Optional year boost
        show_year = show.get('year')
        if show_year:
            year_diff = abs(series_year - show_year)
            if year_diff == 0:
                score += 10
            elif year_diff == 1:
                score += 5
        else:
            score += 2  # Slight boost for unknown year

        if score > highest_score:
            highest_score = score
            best_show = show

    if not best_show or highest_score < 70:
        log_debug(logger, f"[SONARR] No sufficiently close match found for '{series_name}'")
        return ''

    old_path = best_show['path']
    parent_dir = os.path.dirname(old_path)
    new_path = os.path.join(parent_dir, new_folder_name)

    best_show['path'] = new_path
    update_url = f"{sonarr_url}/api/v3/series/{best_show['id']}"
    response = requests.put(update_url, json=best_show, headers=headers)
    response.raise_for_status()

    rescan_payload = {
        "name": "RescanSeries",
        "seriesId": best_show['id']
    }
    requests.post(f"{sonarr_url}/api/v3/command", json=rescan_payload, headers=headers)

    log_debug(logger, f"[SONARR] Triggered rescan for series '{best_show['title']}'")

    if old_path == new_path:
        log_debug(logger, f"[SONARR] No path update needed for '{best_show['title']}'. Already at '{new_path}'")
        return ''
    else:
        log_debug(logger, f"[SONARR] Updated series path for '{best_show['title']}' to '{new_path}'")
        return new_path
