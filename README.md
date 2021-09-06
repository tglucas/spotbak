# spotbak
Spotify Backup Tool

Sample Postgres queries:

```
-- select spotify_json->>'name' from spotbak_artists
-- select spotify_json->'album'->>'name' from spotbak_albums
-- select spotify_json->>'name' from spotbak_playlists
-- select spotify_json->'track'->>'name' as track_name, spotify_json->'track'->'artists'->0->>'name' as artist from spotbak_tracks
```
