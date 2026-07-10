# osu! Most Played Beatmap Downloader

Download a user's most-played osu! beatmaps from most-played to least-played.

## Usage

```sh
uv run downloader.py <user_id> <num_beatmaps> <download_directory> [--workers N]
```

Stops early if `num_beatmaps` exceeds the user's total played beatmaps.

Existing `.osz` files in the download directory are detected by their filename (the beatmapset ID) and skipped automatically.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-w`, `--workers` | `10` | Number of concurrent downloads |

### Behaviour

- **Retries**: Each beatmap is attempted up to 7 times per mirror on transient errors (timeout, connection reset, rate limiting). Non-2xx server errors (502, 404, etc.) fail fast and fall through to the next mirror.
- **Second pass**: Any beatmaps that fail in the first pass are automatically retried once at the end of the run.
- **Caching**: Existing `.osz` files are detected and skipped on subsequent runs.

## Setup

Copy `example.env` to `.env` and fill in your osu! API credentials:

```
OSU_CLIENT_ID=your_client_id
OSU_CLIENT_SECRET=your_client_secret
```

Get credentials at https://osu.ppy.sh/home/account/edit (OAuth Application).
