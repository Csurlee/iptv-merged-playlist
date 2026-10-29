# IPTV Merged Playlist

A single M3U playlist merged from several public IPTV sources, sorted alphabetically by country.

**File:** [`playlist.m3u`](playlist.m3u) — ~19,700 channels across 184 countries.

## Sources

- [iptv-org/iptv](https://github.com/iptv-org/iptv)
- [Free-TV/IPTV](https://github.com/Free-TV/IPTV)
- [dmandache/my-iptv-playlist](https://github.com/dmandache/my-iptv-playlist)
- [Negal78/IPTV_2023_HUN](https://github.com/Negal78/IPTV_2023_HUN)

## How it was built

1. Each source's channel list was parsed (`#EXTINF` + stream URL).
2. Every channel's country was normalized to a single canonical name (e.g. `UK`, `uk`, `GB` all became `United Kingdom`), so entries from different sources sort into the same country group.
3. Exact-duplicate stream URLs were removed, keeping the first occurrence.
4. Channels with the same name but a **different** stream URL were kept as separate entries (different mirrors/providers).
5. The result was sorted by country, then by channel name, and written out as a single `#EXTM3U` file.

## Usage

Load `playlist.m3u` into any IPTV player that supports M3U (VLC, Kodi, TiviMate, IPTV Smarters, etc.):

```
https://raw.githubusercontent.com/Csurlee/iptv-merged-playlist/master/playlist.m3u
```

## EPG (program guide)

The playlist's `#EXTM3U` header includes a `url-tvg` pointing to [epgshare01](https://epgshare01.online)'s combined guide:

```
https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz
```

Most players (TiviMate, IPTV Smarters, VLC with an EPG plugin) pick this up automatically from the playlist header. This is the same EPG source already used by the `dmandache/my-iptv-playlist` and `Free-TV/IPTV` sources, so coverage follows their `tvg-id` values — channels without a `tvg-id` (mainly the ones from `Negal78/IPTV_2023_HUN`) won't get program data, only playback.

## Disclaimer

This repository only merges and re-sorts publicly available playlist metadata (channel names and stream URLs) from the sources listed above. It does not host any video streams itself. Stream availability and legality depend on the original providers and your local laws — use at your own responsibility.

## License

The merged metadata is provided as-is for personal use, following the licensing terms of the upstream source repositories.
