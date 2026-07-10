# osu! Most Played Beatmap Downloader

Download a user's most-played osu! beatmaps from most-played to least-played.

## Usage

```sh
uv run downloader.py <user_id> <num_beatmaps> <download_directory> [--workers N]
```

Stops early if `num_beatmaps` exceeds the user's total played beatmaps.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-w`, `--workers` | `10` | Number of concurrent downloads |

## Setup

Copy `example.env` to `.env` and fill in your osu! API credentials:

```
OSU_CLIENT_ID=your_client_id
OSU_CLIENT_SECRET=your_client_secret
```

Get credentials at https://osu.ppy.sh/home/account/edit (OAuth Application).
