"""
Test script for Jellyfin and Emby Media Server API integrations.
Tests connection, system info, library sections, and genre retrieval.
"""

import os
import sys
from typing import Dict, Any, List
import requests
from dotenv import load_dotenv

# Load environment
load_dotenv()


def test_jellyfin() -> Dict[str, Any]:
    url = (os.getenv("JELLYFIN_URL") or os.getenv("JELLTFIN_URL") or "").rstrip("/")
    api_key = os.getenv("JELLYFIN_KEY") or os.getenv("JELLYFIN_API_KEY") or ""

    result = {
        "server": "Jellyfin",
        "url": url,
        "connected": False,
        "name": None,
        "version": None,
        "genres": [],
        "libraries": [],
        "error": None,
    }

    if not url or not api_key:
        result["error"] = "JELLYFIN_URL or JELLYFIN_KEY is missing in .env"
        return result

    headers = {
        "X-Emby-Token": api_key,
        "Accept": "application/json",
    }

    # Try multiple auth methods for Jellyfin
    auth_methods = [
        ("X-Emby-Token header", {"X-Emby-Token": api_key}),
        ("X-MediaBrowser-Token header", {"X-MediaBrowser-Token": api_key}),
        ("Authorization header", {"Authorization": f'MediaBrowser Token="{api_key}"'}),
        ("api_key query param", {}),
    ]

    working_headers = None
    working_params = None

    try:
        for name, h in auth_methods:
            params = {"api_key": api_key} if name == "api_key query param" else {}
            resp = requests.get(f"{url}/System/Info", headers=h, params=params, timeout=5)
            if resp.status_code == 200:
                working_headers = h
                working_params = params
                result["auth_method"] = name
                info_data = resp.json()
                result["connected"] = True
                result["name"] = info_data.get("ServerName")
                result["version"] = info_data.get("Version")
                break
            elif resp.status_code != 401:
                result["error"] = f"HTTP {resp.status_code}: {resp.text[:100]}"

        if not result["connected"]:
            result["error"] = f"Authentication failed (HTTP 401) with all header formats. Check JELLYFIN_KEY."
            return result

        # 2. Libraries
        lib_resp = requests.get(f"{url}/Library/MediaFolders", headers=working_headers, params=working_params, timeout=6)
        if lib_resp.status_code == 200:
            result["libraries"] = [item.get("Name") for item in lib_resp.json().get("Items", [])]

        # 3. Genres
        genre_resp = requests.get(f"{url}/Genres", headers=working_headers, params=working_params, timeout=6)
        if genre_resp.status_code == 200:
            genre_items = genre_resp.json().get("Items", [])
            result["genres"] = [g.get("Name") for g in genre_items if g.get("Name")]

    except requests.exceptions.ConnectionError:
        result["error"] = f"Connection refused or host unreachable at {url}"
    except Exception as e:
        result["error"] = str(e)

    return result


def test_emby() -> Dict[str, Any]:
    url = (os.getenv("EMBY_URL") or "").rstrip("/")
    api_key = os.getenv("EMBY_KEY") or os.getenv("EMBY_API_KEY") or ""

    result = {
        "server": "Emby",
        "url": url,
        "connected": False,
        "name": None,
        "version": None,
        "genres": [],
        "libraries": [],
        "error": None,
    }

    if not url or not api_key:
        result["error"] = "EMBY_URL or EMBY_KEY is missing in .env"
        return result

    headers = {
        "X-Emby-Token": api_key,
        "Accept": "application/json",
    }

    try:
        # 1. System Info
        info_resp = requests.get(f"{url}/System/Info", headers=headers, timeout=6)
        if info_resp.status_code == 401:
            result["error"] = "Authentication failed (HTTP 401). Check EMBY_KEY."
            return result
        info_resp.raise_for_status()
        info_data = info_resp.json()
        result["connected"] = True
        result["name"] = info_data.get("ServerName")
        result["version"] = info_data.get("Version")

        # 2. Libraries
        lib_resp = requests.get(f"{url}/Library/MediaFolders", headers=headers, timeout=6)
        if lib_resp.status_code == 200:
            result["libraries"] = [item.get("Name") for item in lib_resp.json().get("Items", [])]

        # 3. Genres
        genre_resp = requests.get(f"{url}/Genres", headers=headers, timeout=6)
        if genre_resp.status_code == 200:
            genre_items = genre_resp.json().get("Items", [])
            result["genres"] = [g.get("Name") for g in genre_items if g.get("Name")]

    except requests.exceptions.ConnectionError:
        result["error"] = f"Connection refused or host unreachable at {url}"
    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    print("=" * 65)
    print("      Testing Jellyfin & Emby Media Server Integrations")
    print("=" * 65)

    # Jellyfin
    jf = test_jellyfin()
    print(f"\n[+] JELLYFIN: {jf['url']}")
    if jf["connected"]:
        print(f"    Status   : Connected successfully!")
        print(f"    Server   : {jf['name']} (v{jf['version']})")
        print(f"    Auth     : Accepted via {jf.get('auth_method')}")
        if jf["libraries"]:
            print(f"    Libraries: {', '.join(jf['libraries'])}")
        print(f"    Genres ({len(jf['genres'])} found):")
        if jf["genres"]:
            # Display formatted columns
            for i in range(0, len(jf["genres"]), 4):
                chunk = jf["genres"][i:i+4]
                print("      " + "".join(f"{g:<18}" for g in chunk))
        else:
            print("      (No genres found in library yet)")
    else:
        print(f"    Status   : Failed!")
        print(f"    Error    : {jf['error']}")

    # Emby
    emby = test_emby()
    print(f"\n[+] EMBY: {emby['url']}")
    if emby["connected"]:
        print(f"    Status   : Connected successfully!")
        print(f"    Server   : {emby['name']} (v{emby['version']})")
        if emby["libraries"]:
            print(f"    Libraries: {', '.join(emby['libraries'])}")
        print(f"    Genres ({len(emby['genres'])} found):")
        if emby["genres"]:
            for i in range(0, len(emby["genres"]), 4):
                chunk = emby["genres"][i:i+4]
                print("      " + "".join(f"{g:<18}" for g in chunk))
        else:
            print("      (No genres found in library yet)")
    else:
        print(f"    Status   : Failed!")
        print(f"    Error    : {emby['error']}")

    print("\n" + "=" * 65)


if __name__ == "__main__":
    main()
