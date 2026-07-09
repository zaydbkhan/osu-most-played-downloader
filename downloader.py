import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["OSU_CLIENT_ID"]
CLIENT_SECRET = os.environ["OSU_CLIENT_SECRET"]
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
API_BASE = "https://osu.ppy.sh/api/v2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MIRRORS = [
    "https://api.nerinyan.moe/d/{}",  # no UA restrictions
    "https://catboy.best/d/{}",        # blocks non-browser UAs
]


def get_token() -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "public",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_most_played(
    client: httpx.Client, user_id: int, limit: int, offset: int
) -> list[dict]:
    resp = client.get(
        f"{API_BASE}/users/{user_id}/beatmapsets/most_played",
        params={"limit": limit, "offset": offset},
    )
    resp.raise_for_status()
    return resp.json()


def get_beatmap(client: httpx.Client, beatmap_id: int) -> dict:
    resp = client.get(f"{API_BASE}/beatmaps/{beatmap_id}")
    resp.raise_for_status()
    return resp.json()


def try_download(url: str) -> bytes | None:
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30, headers={"User-Agent": UA})
        if not resp.is_success:
            return None
        ct = resp.headers.get("content-type", "")
        if "json" in ct:
            data = resp.json()
            if "download_url" in data:
                return try_download(data["download_url"])
            return None
        if any(t in ct for t in ("osz", "octet-stream", "zip", "x-osu-beatmap-archive")):
            return resp.content
        return None
    except httpx.HTTPError:
        return None


def download_osz(set_id: int, dest_dir: Path) -> bool:
    dest_file = dest_dir / f"{set_id}.osz"
    if dest_file.exists() and dest_file.stat().st_size > 1000:
        print(f"  already exists: {set_id}.osz")
        return True

    for template in MIRRORS:
        url = template.format(set_id)
        data = try_download(url)
        if data is not None and len(data) > 1000:
            dest_file.write_bytes(data)
            return True

    dest_file.unlink(missing_ok=True)
    return False


def main() -> None:
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} USER_ID NUM_BEATMAPS DOWNLOAD_DIRECTORY")
        sys.exit(1)

    user_id = int(sys.argv[1])
    num = int(sys.argv[2])
    dest_dir = Path(sys.argv[3])
    dest_dir.mkdir(parents=True, exist_ok=True)

    print("Getting OAuth token...")
    token = get_token()
    seen_set_ids: set[int] = set()
    total_downloaded = 0

    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as client:
        offset = 0
        page_size = 100

        while len(seen_set_ids) < num:
            print(f"Fetching most played (offset={offset})...")
            entries = fetch_most_played(client, user_id, page_size, offset)
            if not entries:
                print("No more beatmaps found.")
                break

            for entry in entries:
                beatmap_id = entry["beatmap_id"]
                if len(seen_set_ids) >= num:
                    break

                print(f"  Resolving beatmap {beatmap_id}...")
                bm = get_beatmap(client, beatmap_id)
                set_id = bm["beatmapset_id"]

                if set_id in seen_set_ids:
                    continue

                seen_set_ids.add(set_id)
                bms = bm.get("beatmapset", {})
                name = f"{bms.get('artist', '?')} - {bms.get('title', '?')}"
                print(f"  ({entry['count']} plays) {name} (set {set_id})...")
                if download_osz(set_id, dest_dir):
                    total_downloaded += 1
                else:
                    print(f"  Failed to download {set_id} from all mirrors")
                time.sleep(0.5)

            if len(entries) < page_size:
                break

            offset += page_size

    print(f"Done. Downloaded {total_downloaded} beatmapsets to {dest_dir}")


if __name__ == "__main__":
    main()