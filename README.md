# spotbak
Spotify Backup Tool

## Notes

- In order for the Spotify OAuth workflow to complete, ensure that `Redirect URIs` on the Spotify developer profile for your application include all forms of your callback, including URIs that reference local/private addresses.

## Web Viewer

A beautiful web interface to view and explore your Spotify backup data locally with **automatic discovery** of all your backup files.

### Features

- 🎨 **Beautiful Interface**: Modern, responsive design with Bootstrap
- 🔍 **Search & Filter**: Find content across all your datasets quickly
- 🖼️ **Image Display**: Shows artist photos, album artwork, and playlist covers
- 📊 **Rich Data**: Displays popularity, followers, genres, and more
- 📱 **Mobile Friendly**: Responsive design works on all devices
- 🚀 **Fast Loading**: Data loaded at startup for quick browsing
- 🔄 **Auto-Discovery**: Automatically finds and loads ALL spotbak JSON files
- 📈 **Dynamic Views**: Adapts interface based on data type (artists, tracks, playlists, etc.)

### Quick Start

1. **Install and Start**:
   ```bash
   ./start.sh
   ```

2. **Or manually**:
   ```bash
   npm install
   npm start
   ```

3. **Open your browser** to: http://localhost:3000

### Automatic Data Discovery

The viewer automatically discovers and loads **ANY** JSON file in the current directory that starts with "spotbak":

- `spotbak_artists.json` ✅
- `spotbak_top_artists.json` ✅  
- `spotbak_tracks.json` ✅
- `spotbak_top_tracks.json` ✅
- `spotbak_playlists.json` ✅
- `spotbak_playlists_tracks.json` ✅
- `spotbak_albums.json` ✅
- `spotbak_shows.json` ✅
- `spotbak_[anything].json` ✅

**No configuration needed!** Just place your backup files in the same directory and they'll appear automatically.

### Smart Data Recognition

The viewer intelligently recognizes different data types and provides optimized views:

- **Artists**: Table view with images, follower counts, popularity bars, and genres
- **Tracks**: Detailed table with album covers, duration, and artist info  
- **Playlists**: Card-based grid with cover images and track counts
- **Albums**: Album artwork grid with release dates and track counts
- **Shows/Podcasts**: Card layout with publisher and description info
- **Generic Data**: Flexible table view for any other JSON structure

### API Endpoints

- `GET /api/datasets` - Information about all discovered datasets
- `GET /api/dataset/:name` - Raw JSON data for specific dataset
- `GET /dataset/:name` - Web view for specific dataset

### Legacy API Support

For backward compatibility:
- `GET /api/artists` - First artists dataset found
- `GET /api/tracks` - First tracks dataset found  
- `GET /api/playlists` - First playlists dataset found
- `GET /api/albums` - First albums dataset found

### Sample Postgres Queries

```sql
-- select spotify_json->>'name' from spotbak_artists
-- select spotify_json->'album'->>'name' from spotbak_albums
-- select spotify_json->>'name' from spotbak_playlists
-- select spotify_json->'track'->>'name' as track_name, spotify_json->'track'->'artists'->0->>'name' as artist from spotbak_tracks
```

### Technology Stack

- **Backend**: Node.js + Express
- **Frontend**: Bootstrap 5 + EJS templates
- **Icons**: Font Awesome
- **Styling**: Custom CSS with Spotify-inspired colors
- **Data**: Dynamic JSON file discovery and analysis
