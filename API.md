# Bokeh Media Server API

Complete reference for the Bokeh media server HTTP API.

**Base URL:** `http://localhost:3000` (default)

**API Version:** `/api/v1/`

---

## Authentication

Authentication is required for all endpoints except `/auth/providers`, `/auth/login`, `/auth/refresh`, `/system/health`, and `/system/version`.

Authenticated requests must include the `Authorization: Bearer <token>` header with a valid JWT access token obtained from login. Access tokens expire after 15 minutes; use the refresh endpoint to obtain a new one.

### List Authentication Providers

```http
GET /api/v1/auth/providers
```

Returns available authentication methods. Currently only local login is supported.

**Response (200 OK):**
```json
{
  "providers": ["local"]
}
```

### Login

```http
POST /api/v1/auth/login
Content-Type: application/json
```

Authenticate with username and password. Returns access and refresh tokens via both cookies and JSON body.

**Request Body:**
```json
{
  "provider": "string",
  "device_uuid": "string",
  "device_name": "string",
  "credentials": {
    "username": "string",
    "password": "string"
  }
}
```

- `provider` — Auth provider name (default: `local`); currently only `local` is supported
- `device_uuid` — Unique device identifier (UUID); required; persisted for session tracking
- `device_name` — Human-readable device name (e.g., "iPhone", "Living Room TV")
- `credentials` — Provider-specific credentials (for local: `{username, password}`)

**Response (200 OK):**
```json
{
  "access_token": "string",
  "access_token_expires_in": 900,
  "refresh_token": "string",
  "refresh_token_expires_in": 7776000,
  "device_id": 123
}
```

- `access_token` — Short-lived JWT (15 min); use in `Authorization: Bearer <token>` header
- `refresh_token` — Long-lived token (90 days); use to obtain new access tokens
- Tokens are also set as httpOnly cookies: `access_token`, `refresh_token`
- `device_id` — ID of the device session

**Error Responses:**
- `400 Bad Request` — Invalid request or unknown provider
- `401 Unauthorized` — Invalid credentials
- `403 Forbidden` — Device is banned or user is local-only and request is remote
- `429 Too Many Requests` — Rate limit exceeded (login per IP)

### Refresh Access Token

```http
POST /api/v1/auth/refresh
Content-Type: application/json
```

Obtain a new access token using a refresh token. Can pass token via cookie or request body.

**Request Body:**
```json
{
  "refresh_token": "string",
  "device_uuid": "string"
}
```

- `refresh_token` — Refresh token from login (or from cookie if set)
- `device_uuid` — Device UUID from login (required; prevents token theft)

**Response (200 OK):**
```json
{
  "access_token": "string",
  "access_token_expires_in": 900,
  "refresh_token": "string",
  "refresh_token_expires_in": 7776000,
  "device_id": 123
}
```

- Returns a new refresh token (automatic rotation)
- Tokens are also set as httpOnly cookies

**Error Responses:**
- `400 Bad Request` — Missing refresh_token or device_uuid
- `401 Unauthorized` — Invalid/expired/stolen refresh token, device mismatch, or device_uuid mismatch
- `403 Forbidden` — User is local-only and request is remote

---

## Collections

Collections are hierarchical containers for media: photo albums, music libraries, video folders, etc. Each collection has a type (`image:photo`, `video:movie`, `video:home_movie`, `audio:music`, `audio:show`) and may contain subcollections or media items.

### List Top-Level Collections

```http
GET /api/v1/collections
```

Returns all root-level collections the authenticated user has access to.

**Response (200 OK):**
```json
[
  {
    "id": 123,
    "parent_collection_id": null,
    "name": "My Photos",
    "type": "image:photo"
  },
  {
    "id": 124,
    "parent_collection_id": null,
    "name": "Vacation",
    "type": "video:home_movie",
    "date": "2024-01-15"
  }
]
```

- `date` — Optional; extracted from collection name if it follows a date prefix pattern

### Get Collection Details

```http
GET /api/v1/collections/{id}
```

Retrieve metadata for a specific collection.

**Response (200 OK):**
```json
{
  "id": 123,
  "parent_collection_id": null,
  "name": "string",
  "type": "image:photo" | "video:movie" | "video:home_movie" | "audio:music" | "audio:show"
}
```

### List Subcollections

```http
GET /api/v1/collections/{id}/collections
```

Returns immediate child collections of the specified collection.

**Response (200 OK):**
```json
[
  {
    "id": 456,
    "parent_collection_id": 123,
    "name": "string",
    "type": "image:photo" | "video:movie" | "video:home_movie" | "audio:music" | "audio:show"
  }
]
```

---

## Photos

Photo collections contain individual images with metadata (EXIF, dimensions, camera info, etc.), variants at different sizes for responsive UI, and Deep Zoom Image (DZI) tile pyramids for lossless infinite zoom.

### List Photos

```http
GET /api/v1/collections/{id}/photos
```

List all photos in a collection.

**Query Parameters:**
- `offset` — Pagination offset (default: 0)
- `limit` — Results per page (default: 200, max: 1000)
- `sort_order` — Sort order: `asc` or `desc` (default: `asc`)
- `recursive` — Include photos from subcollections: `true` or `false` (default: `false`)

**Response (200 OK):**
```json
{
  "items": [
    {
      "id": 1001,
      "title": "photo.jpg",
      "mime_type": "image/jpeg",
      "created_at": "2023-05-15T10:30:00Z",
      "width_px": 4000,
      "height_px": 3000,
      "camera_make": "Canon",
      "camera_model": "EOS 5D Mark IV",
      "lens_model": "Canon EF 24-70mm f/2.8L II USM",
      "shutter_speed": "1/250",
      "aperture": 5.6,
      "iso": 200,
      "focal_length_mm": 50.0,
      "focal_length_35mm_equiv": 50.0,
      "variants_generated_at": "2023-05-15T10:35:00Z",
      "ordinal": 0
    }
  ],
  "offset": 0,
  "limit": 200
}
```

- All EXIF fields are optional and may be null
- `ordinal` — Zero-based index within the result set

### Photo Stats

```http
GET /api/v1/collections/{id}/photos/stats
```

Aggregate statistics for photos in a collection (photos per month).

**Query Parameters:**
- `recursive` — Include photos from subcollections: `true` or `false` (default: `false`)

**Response (200 OK):**
```json
{
  "total": 150,
  "months": [
    {
      "year": 2023,
      "month": 5,
      "count": 42
    },
    {
      "year": 2023,
      "month": 6,
      "count": 38
    }
  ]
}
```

- Photos with missing `created_at` are excluded from counts

### Serve Image Variant

```http
GET /images/{id}/{variant}
HEAD /images/{id}/{variant}
```

Serve a photo in one of three optimized sizes.

**Path Parameters:**
- `id` — BLAKE2b content hash of the image
- `variant` — `thumb` (400px), `small` (1280px), `preview` (1920px)

**Query Parameters:**
- `accept` — `webp` (default) or `jpeg` (for Roku/legacy clients)

**Response (200 OK):**
- `Content-Type: image/webp` or `image/jpeg`
- Binary image data

The `preview` variant (1920px) is suitable for typical displays. For larger viewports or deep zoom, use the DZI manifest to access multi-resolution tile pyramids.

**Error Responses:**
- `404 Not Found` — Image not found or variants not yet generated

### Get Image EXIF Data

```http
GET /images/{id}/exif
```

Retrieve EXIF metadata for a photo.

**Response (200 OK):**
```json
{
  "camera": "Canon EOS 5D Mark IV",
  "lens": "Canon EF 24-70mm f/2.8L II USM",
  "aperture": "f/5.6",
  "shutter_speed": "1/250",
  "iso": 200,
  "focal_length": "50mm",
  "date_time": "2023-05-15T10:30:00Z",
  "gps_latitude": 51.5074,
  "gps_longitude": -0.1278,
  "width": 4000,
  "height": 3000
}
```

All fields are optional and depend on EXIF data present in the file.

### Serve DZI Manifest

```http
GET /images/{id}/tiles/image.dzi
```

Serve Deep Zoom Image manifest for multi-resolution tile access. Use with OpenSeadragon or similar viewer for lossless infinite zoom.

**Response (200 OK):**
- `Content-Type: application/xml`
- XML DZI manifest describing tile pyramid

### Serve DZI Tile

```http
GET /images/{id}/tiles/{level}/{column}/{row}.jpg
```

Individual tile within the DZI pyramid.

**Response (200 OK):**
- `Content-Type: image/jpeg`
- Binary JPEG tile

### Serve Collection Cover

```http
GET /images/collections/{id}/cover
```

Retrieve the cover image for a collection (either auto-selected or manually set).

**Response (200 OK):**
- `Content-Type: image/webp`
- Binary image data

**Error Responses:**
- `404 Not Found` — Collection has no cover

---

## Audio

Two sub-types of audio collections:

- **Music** (`audio:music`) — Hierarchical: artist → album → track
- **Radio/Shows** (`audio:show`) — Episodic: show → episode; tracks bookmark position

### Music: List Artists

```http
GET /api/v1/collections/{collectionId}/artists
```

List all artists in a music collection.

**Query Parameters:**
- `page` — Page number (default: 1)
- `page_size` — Items per page (default: 100, max: 500)
- `search` — Filter artists by name (optional)

**Response (200 OK):**
```json
{
  "artists": [
    {
      "id": 3001,
      "name": "The Beatles",
      "sort_name": "Beatles, The"
    }
  ],
  "total_count": 42,
  "page": 1,
  "page_size": 100
}
```

### Music: List Artist Albums

```http
GET /api/v1/collections/{collectionId}/artists/{artistId}/albums
```

List all albums for a specific artist.

**Response (200 OK):**
```json
{
  "artist": {
    "id": 3001,
    "name": "The Beatles",
    "sort_name": "Beatles, The"
  },
  "albums": [
    {
      "name": "Abbey Road",
      "album_id": 4001,
      "year": 1969,
      "track_count": 17,
      "total_duration": 3600.5
    }
  ]
}
```

### Music: List Album Tracks

```http
GET /api/v1/collections/{collectionId}/albums/{albumId}/tracks
```

List all tracks in an album.

**Response (200 OK):**
```json
{
  "album": {
    "id": 4001,
    "name": "Abbey Road",
    "artist_id": 3001,
    "year": 1969,
    "genre": "Rock",
    "is_compilation": false,
    "manual_cover": false
  },
  "tracks": [
    {
      "id": 5001,
      "title": "Come Together",
      "mime_type": "audio/mpeg",
      "track_number": 1,
      "disc_number": 1,
      "duration_seconds": 259.0,
      "artist_name": "The Beatles"
    }
  ],
  "total_duration": 3600.5,
  "disc_count": 1
}
```

### Audio Stream

```http
GET /audio/{id}/stream
HEAD /audio/{id}/stream
```

Stream audio file for a music track.

**Response (200 OK):**
- `Content-Type: audio/mpeg` (or appropriate codec)
- `Content-Length: <bytes>`
- Binary audio stream

### Serve Artist Image

```http
GET /images/artists/{id}/cover
```

Retrieve cover image for an artist.

**Response (200 OK):**
- `Content-Type: image/webp`
- Binary image data

### Serve Album Thumbnail

```http
GET /images/albums/{albumId}/thumb
```

Retrieve a small thumbnail for an album cover.

**Response (200 OK):**
- `Content-Type: image/webp`
- Binary image data

### Serve Album Cover

```http
GET /images/albums/{albumId}/cover
```

Retrieve full cover image for an album.

**Response (200 OK):**
- `Content-Type: image/webp`
- Binary image data

### Radio Shows: List Shows

```http
GET /api/v1/collections/{collectionId}/shows
```

List all shows in a radio/podcast collection.

**Response (200 OK):**
```json
{
  "shows": [
    {
      "show_id": 3002,
      "name": "The Daily Show",
      "manual_thumbnail": false
    }
  ]
}
```

### Radio Shows: List Episodes

```http
GET /api/v1/collections/{collectionId}/shows/{artistId}/episodes
```

List all episodes for a specific show, including the current bookmark.

**Response (200 OK):**
```json
{
  "show": {
    "id": 3002,
    "name": "The Daily Show",
    "sort_name": "Daily Show, The"
  },
  "episodes": [
    {
      "id": 5002,
      "title": "Episode 1",
      "mime_type": "audio/mpeg",
      "track_number": 1,
      "disc_number": 1,
      "duration_seconds": 3600.0,
      "artist_name": "The Daily Show",
      "album_name": "Season 1"
    }
  ],
  "bookmark": {
    "media_item_id": 5001,
    "position_seconds": 1800,
    "last_listened_at": "2023-05-15T10:30:00Z"
  }
}
```

- `bookmark` — null if no bookmark exists yet

### Radio Shows: Upsert Show Bookmark

```http
PUT /api/v1/audio-shows/{artistId}/bookmark
Content-Type: application/json
```

Save playback position for a show episode.

**Request Body:**
```json
{
  "media_item_id": 5001,
  "position_seconds": 1800
}
```

**Response (204 No Content)**

---

## Video

Video collections contain movies, home videos, or TV episodes. Support adaptive bitrate HLS streaming for flexible bandwidth adaptation, bookmarks for resumable playback, and manual cover art.

### List Videos

```http
GET /api/v1/collections/{id}/videos
```

List all videos in a collection.

**Query Parameters:**
- `page` — Page number (default: 1)
- `page_size` — Items per page (default: 200, max: 1000)

**Response (200 OK):**
```json
{
  "items": [
    {
      "id": 2001,
      "title": "movie.mp4",
      "mime_type": "video/mp4",
      "duration_seconds": 5400,
      "width": 1920,
      "height": 1080,
      "bitrate_kbps": 5000,
      "video_codec": "h264",
      "audio_codec": "aac",
      "transcoded_at": "2023-05-15T10:35:00Z",
      "date": "2023-05-15",
      "author": "string or null",
      "bookmark_seconds": 1800,
      "manual_thumbnail": false
    }
  ],
  "page": 1,
  "next_page": 2,
  "page_size": 200
}
```

- `next_page` — Null if no more results
- All optional fields may be null

### Get Video Item

```http
GET /api/v1/collections/{id}/items/{item_id}
```

Retrieve detailed information about a video, including playback bookmark.

**Response (200 OK):**
```json
{
  "id": 2001,
  "title": "movie.mp4",
  "mime_type": "video/mp4",
  "duration_seconds": 5400,
  "width": 1920,
  "height": 1080,
  "bitrate_kbps": 5000,
  "video_codec": "h264",
  "audio_codec": "aac",
  "transcoded_at": "2023-05-15T10:35:00Z",
  "date": "2023-05-15",
  "author": "string or null",
  "bookmark_seconds": 1800,
  "manual_thumbnail": false
}
```

### HLS Manifest

```http
GET /videos/{id}/hls/manifest.m3u8
```

Serve HLS manifest for adaptive bitrate streaming. Clients request this manifest and then fetch segments listed within it.

**Response (200 OK):**
- `Content-Type: application/vnd.apple.mpegurl`
- M3U8 playlist with segment list and bitrate variants

### HLS Segment

```http
GET /videos/{id}/hls/{segment}
```

Serve individual MPEG-TS segment from an HLS stream.

**Response (200 OK):**
- `Content-Type: video/mp2t`
- Binary TS segment

### Live HLS Manifest

```http
GET /videos/{id}/live/manifest.m3u8
```

Live HLS manifest (for compatibility; segments are identical to the on-demand HLS manifest).

### Live HLS Segment

```http
GET /videos/{id}/live/{segment}
```

Live HLS segment (for compatibility; identical to on-demand segments).

### Stream Video

```http
GET /videos/{id}/stream
```

Stream the transcoded video file for adaptive playback.

**Response (200 OK):**
- `Content-Type: video/mp4` (or appropriate codec)
- Binary video stream

### Raw Video

```http
GET /videos/{id}/raw
HEAD /videos/{id}/raw
```

Serve the original video file without transcoding.

**Response (200 OK):**
- `Content-Type: video/mp4` (or appropriate codec)
- Binary video stream

### Serve Video Cover

```http
GET /images/videos/{id}/cover
```

Retrieve cover image for a video (either auto-generated from first frame or manually uploaded).

**Response (200 OK):**
- `Content-Type: image/webp`
- Binary image data

### Upsert Video Bookmark

```http
PUT /api/v1/collections/{id}/items/{item_id}/bookmark
Content-Type: application/json
```

Save playback position for resumable video playback.

**Request Body:**
```json
{
  "position_seconds": 1800
}
```

**Response (200 OK):**
```json
{
  "position_seconds": 1800,
  "last_watched_at": "2023-05-15T10:30:00Z"
}
```

### Delete Video Bookmark

```http
DELETE /api/v1/collections/{id}/items/{item_id}/bookmark
```

Remove bookmark for a video.

**Response (204 No Content)**

---

## Search

Full-text search across the library using PostgreSQL full-text search (tsvector). All queries support boolean operators (quotes for phrases, `-` for negation, `OR` for alternatives).

### Search Photos

```http
GET /api/v1/search/photos
```

Full-text search for photos by title, filename, or EXIF metadata.

**Query Parameters:**
- `q` — Search query (required)
- `offset` — Pagination offset (default: 0)
- `limit` — Results per page (default: 50, max: 200)

**Response (200 OK):**
```json
{
  "items": [
    {
      "id": 1001,
      "title": "vacation.jpg",
      "mime_type": "image/jpeg",
      "created_at": "2023-05-15T10:30:00Z",
      "width_px": 4000,
      "height_px": 3000,
      "camera_make": "Canon",
      "camera_model": "EOS 5D Mark IV",
      "lens_model": "Canon EF 24-70mm f/2.8L II USM",
      "shutter_speed": "1/250",
      "aperture": 5.6,
      "iso": 200,
      "focal_length_mm": 50.0,
      "focal_length_35mm_equiv": 50.0,
      "variants_generated_at": "2023-05-15T10:35:00Z",
      "collection_name": "My Photos",
      "ordinal": 0
    }
  ],
  "offset": 0,
  "limit": 50
}
```

### Search Photo Collections

```http
GET /api/v1/search/photos/collections
```

Search for photo collections by name.

**Query Parameters:**
- `q` — Search query (required)
- `offset` — Pagination offset (default: 0)
- `limit` — Results per page (default: 50, max: 200)

**Response (200 OK):**
```json
{
  "collections": [
    {
      "id": 123,
      "name": "Vacation Photos",
      "date": "2023-05-15",
      "collection_path": [10, 123]
    }
  ],
  "offset": 0,
  "limit": 50
}
```

- `collection_path` — Array of collection IDs from root to this collection

### Search Videos

```http
GET /api/v1/search/videos
```

Full-text search for videos by title or filename.

**Query Parameters:**
- `q` — Search query (required)
- `offset` — Pagination offset (default: 0)
- `limit` — Results per page (default: 50, max: 200)

**Response (200 OK):**
```json
{
  "video:movie": [
    {
      "id": 2001,
      "title": "Action Film",
      "collection_name": "Movies",
      "date": "2023-05-15",
      "collection_path": [20, 2001]
    }
  ],
  "video:home_movie": [
    {
      "id": 2002,
      "title": "Family Vacation",
      "collection_name": "Home Videos",
      "date": "2023-07-20",
      "collection_path": [21, 2002]
    }
  ],
  "offset": 0,
  "limit": 50
}
```

- Results are partitioned by video type

### Search Audio Artists

```http
GET /api/v1/search/audio/artists
```

Search for music artists and radio shows.

**Query Parameters:**
- `q` — Search query (required)
- `offset` — Pagination offset (default: 0)
- `limit` — Results per page (default: 50, max: 200)

**Response (200 OK):**
```json
{
  "artists": [
    {
      "id": 3001,
      "name": "The Beatles",
      "collection_id": 15
    }
  ],
  "shows": [
    {
      "id": 3002,
      "name": "The Daily Show",
      "collection_id": 16
    }
  ],
  "offset": 0,
  "limit": 50
}
```

- Results are partitioned into `artists` (from music collections) and `shows` (from audio:show collections)

### Search Audio Albums

```http
GET /api/v1/search/audio/albums
```

Search for music albums.

**Query Parameters:**
- `q` — Search query (required)
- `offset` — Pagination offset (default: 0)
- `limit` — Results per page (default: 50, max: 200)

**Response (200 OK):**
```json
{
  "albums": [
    {
      "id": 4001,
      "name": "Abbey Road",
      "artist_id": 3001,
      "year": 1969,
      "collection_id": 15
    }
  ],
  "offset": 0,
  "limit": 50
}
```

### Search Audio Tracks

```http
GET /api/v1/search/audio/tracks
```

Search for music tracks and radio episodes.

**Query Parameters:**
- `q` — Search query (required)
- `offset` — Pagination offset (default: 0)
- `limit` — Results per page (default: 50, max: 200)

**Response (200 OK):**
```json
{
  "tracks": [
    {
      "id": 5001,
      "title": "Come Together",
      "album_id": 4001,
      "album_name": "Abbey Road",
      "artist_id": 3001,
      "artist_name": "The Beatles",
      "duration_seconds": 259.0,
      "collection_id": 15,
      "collection_type": "audio:music"
    }
  ],
  "offset": 0,
  "limit": 50
}
```

---

## User & Device Management

### Current User Profile

```http
GET /api/v1/auth/me
```

Get the authenticated user's profile information.

**Response (200 OK):**
```json
{
  "id": 1,
  "name": "admin",
  "is_admin": true,
  "device_id": 123
}
```

### Logout

```http
POST /api/v1/auth/logout
```

Invalidate the current device session. Deletes the device row and revokes its access token.

**Response (204 No Content)**

### Change Credentials

```http
POST /api/v1/auth/credentials
Content-Type: application/json
```

Update the authenticated user's credentials (password for local auth).

**Request Body:**
```json
{
  "credentials": {
    "password": "new_password"
  }
}
```

**Response (204 No Content)**

**Error Responses:**
- `400 Bad Request` — Invalid or unsupported credentials format

### List User Devices

```http
GET /api/v1/auth/devices
```

List all active sessions for the authenticated user.

**Response (200 OK):**
```json
[
  {
    "id": 123,
    "device_name": "iPhone",
    "created_at": "2023-05-10T08:00:00Z",
    "last_seen_at": "2023-05-15T10:30:00Z",
    "banned_at": null,
    "access_history": [
      {
        "ip": "192.168.1.100",
        "agent": "Mozilla/5.0...",
        "last_seen": "2023-05-15T10:30:00Z"
      }
    ]
  }
]
```

- `banned_at` — null if device is not banned; timestamp if banned
- `access_history` — Recent access log entries (IP, user agent, timestamps)

### Delete Device

```http
DELETE /api/v1/auth/devices/{id}
```

Remove a specific device session. Cannot delete your own active device.

**Response (204 No Content)**

**Error Responses:**
- `403 Forbidden` — Attempting to delete your own device
- `404 Not Found` — Device not found or not owned by user

### Ban Device

```http
POST /api/v1/auth/devices/{id}/ban
```

Prevent a device from authenticating. Cannot ban your own device.

**Response (204 No Content)**

Existing tokens remain valid until they expire; login attempts and token refresh attempts will fail immediately.

**Error Responses:**
- `403 Forbidden` — Attempting to ban your own device
- `500 Internal Server Error` — Database error

### Unban Device

```http
DELETE /api/v1/auth/devices/{id}/ban
```

Re-enable a banned device.

**Response (204 No Content)**

---

## Admin

Admin endpoints require the `adm` claim in the JWT. Only users with `is_admin: true` can access these.

### Collections (Admin)

#### List Collections (Admin)

```http
GET /api/v1/admin/collections
```

List all top-level collections.

**Response (200 OK):**
```json
[
  {
    "id": 10,
    "parent_collection_id": null,
    "relative_path": "/photos",
    "name": "My Photos",
    "type": "image:photo",
    "root_collection_id": 10,
    "is_enabled": true,
    "manual_thumbnail": false,
    "created_at": "2023-01-01T00:00:00Z",
    "last_scanned_at": "2023-05-15T10:35:00Z",
    "missing_since": null
  }
]
```

#### Create Collection

```http
POST /api/v1/admin/collections
Content-Type: application/json
```

Create a new top-level collection.

**Request Body:**
```json
{
  "name": "My Photos",
  "type": "image:photo" | "video:movie" | "video:home_movie" | "audio:music" | "audio:show",
  "relative_path": "photos"
}
```

- `relative_path` — Path relative to `MEDIA_PATH`

**Response (201 Created):**
```json
{
  "id": 10,
  "name": "My Photos",
  "type": "image:photo",
  "parent_collection_id": null,
  "root_collection_id": 10,
  "relative_path": "/photos",
  "is_enabled": true,
  "manual_thumbnail": false
}
```

#### Delete Collection

```http
DELETE /api/v1/admin/collections/{id}
```

Remove a collection and all cascading data (media items, child collections).

**Response (204 No Content)**

#### Upload Collection Cover

```http
POST /api/v1/admin/collections/{id}/cover
Content-Type: multipart/form-data
```

Manually set a collection cover image.

**Request:**
- `cover` — Image file (JPEG, PNG, AVIF, etc.)

**Response (200 OK):**

#### Delete Derivatives

```http
DELETE /api/v1/admin/collections/{id}/derivatives
```

Remove all derived files (photo variants, DZI tiles, video transcodes) for a collection.

**Response (204 No Content)**

#### List Collection Users

```http
GET /api/v1/admin/collections/{id}/users
```

List users with access to a collection (must be a root collection).

**Response (200 OK):**
```json
[
  {
    "id": 1,
    "name": "admin"
  }
]
```

#### Grant Collection Access

```http
POST /api/v1/admin/collections/{id}/users
Content-Type: application/json
```

Grant multiple users access to a root collection.

**Request Body:**
```json
{
  "user_ids": [1, 2]
}
```

**Response (204 No Content)**

#### Video Cover Management

```http
POST /api/v1/admin/media/{id}/cover
Content-Type: multipart/form-data
```

Manually set a video's cover image.

**Request:**
- `cover` — Image file

**Response (200 OK):**

```http
DELETE /api/v1/admin/media/{id}/hide
```

Unhide a media item.

**Response (204 No Content)**

#### Artist Image Management

```http
POST /api/v1/admin/artists/{id}/image
Content-Type: multipart/form-data
```

Upload an artist portrait/image.

**Request:**
- `image` — Image file

**Response (200 OK):**

```http
DELETE /api/v1/admin/artists/{id}/image
```

Remove artist image.

**Response (204 No Content)**

#### Album Cover Management

```http
POST /api/v1/admin/albums/{albumId}/cover
Content-Type: multipart/form-data
```

Upload an album cover.

**Request:**
- `cover` — Image file

**Response (200 OK):**

```http
DELETE /api/v1/admin/albums/{albumId}/cover
```

Remove album cover.

**Response (204 No Content)**

#### Hide/Unhide Media

```http
POST /api/v1/admin/media/{id}/hide
```

Hide a media item from user views.

**Response (204 No Content)**

#### Directory Browser

```http
GET /api/v1/admin/directories
GET /api/v1/admin/directories/{path}
```

Browse filesystem directories for collection creation.

**Query Parameters:**
- `path` — Directory path (relative to `MEDIA_PATH`)

**Response (200 OK):**
```json
[
  {
    "name": "photos",
    "path": "photos",
    "is_dir": true
  },
  {
    "name": "movies",
    "path": "movies",
    "is_dir": true
  }
]
```

---

### Users (Admin)

#### List Users

```http
GET /api/v1/admin/users
```

List all users.

**Response (200 OK):**
```json
[
  {
    "id": 1,
    "name": "admin"
  },
  {
    "id": 2,
    "name": "user1"
  }
]
```

#### Create User

```http
POST /api/v1/admin/users
Content-Type: application/json
```

Create a new user account.

**Request Body:**
```json
{
  "name": "newuser",
  "auth_provider": "local",
  "credentials": {
    "password": "password123"
  },
  "is_admin": false,
  "local_access_only": false
}
```

- `auth_provider` — Default: `local`
- `local_access_only` — If true, user can only authenticate from local network

**Response (201 Created):**
```json
{
  "id": 3
}
```

#### Delete User

```http
DELETE /api/v1/admin/users/{id}
```

Remove a user account. Cannot delete your own account.

**Response (204 No Content)**

**Error Responses:**
- `403 Forbidden` — Attempting to delete your own account

#### Change User Credentials

```http
POST /api/v1/admin/users/{id}/credentials
Content-Type: application/json
```

Reset a user's password.

**Request Body:**
```json
{
  "credentials": {
    "password": "newpassword"
  }
}
```

**Response (204 No Content)**

#### List User Devices

```http
GET /api/v1/admin/users/{id}/devices
```

List all sessions for a specific user.

**Response (200 OK):**
```json
[
  {
    "id": 123,
    "device_name": "iPhone",
    "created_at": "2023-05-10T08:00:00Z",
    "last_seen_at": "2023-05-15T10:30:00Z",
    "banned_at": null,
    "access_history": [
      {
        "ip": "192.168.1.100",
        "agent": "Mozilla/5.0...",
        "last_seen": "2023-05-15T10:30:00Z"
      }
    ]
  }
]
```

#### Revoke All User Devices

```http
DELETE /api/v1/admin/users/{id}/devices
```

Invalidate all sessions for a user.

**Response (204 No Content)**

#### Revoke User Device

```http
DELETE /api/v1/admin/users/{id}/devices/{deviceId}
```

Remove a specific session for a user.

**Response (204 No Content)**

#### Get User Collection Access

```http
GET /api/v1/admin/users/{userId}/collection_access
```

List all collections a user has access to.

**Response (200 OK):**
```json
[
  {
    "collection_id": 10,
    "collection_name": "My Photos"
  }
]
```

#### Grant Collection Access (Add)

```http
PATCH /api/v1/admin/users/{userId}/collection_access
Content-Type: application/json
```

Add collection access to a user (without removing existing access).

**Request Body:**
```json
{
  "collection_ids": [10, 20]
}
```

**Response (204 No Content)**

#### Set Collection Access (Replace)

```http
POST /api/v1/admin/users/{userId}/collection_access
Content-Type: application/json
```

Replace all collection access for a user.

**Request Body:**
```json
{
  "collection_ids": [10, 20]
}
```

**Response (204 No Content)**

#### Revoke Collection Access

```http
DELETE /api/v1/admin/users/{userId}/collection_access/{collectionId}
```

Remove a user's access to a specific collection.

**Response (204 No Content)**

---

### Jobs (Admin)

Background jobs handle library scanning, media processing, transcoding, and maintenance.

#### List Jobs

```http
GET /api/v1/admin/jobs
```

List recent background jobs.

**Query Parameters:**
- `page` — Page number (default: 1)
- `limit` — Jobs per page (default: 50, max: 200)
- `inactive` — Include inactive jobs: `true` or `false` (default: `false`)

**Response (200 OK):**
```json
{
  "jobs": [
    {
      "id": 1001,
      "type": "library_scan",
      "status": "done",
      "step": 3,
      "total_steps": 3,
      "subjobs_enqueued": 42,
      "subjobs_completed": 42,
      "total_sub_jobs": 42,
      "supports_sub_jobs": true,
      "related_id": 10,
      "related_type": "collection",
      "related_name": "My Photos",
      "created_at": "2023-05-15T10:30:00Z",
      "updated_at": "2023-05-15T10:35:00Z",
      "log": "Started scan..."
    }
  ],
  "total": 50,
  "page": 1
}
```

#### Create Job

```http
POST /api/v1/admin/jobs
Content-Type: application/json
```

Manually trigger a background job.

**Request Body:**
```json
{
  "type": "library_scan" | "orphan_cleanup" | "integrity_check",
  "related_id": 10,
  "related_type": "collection"
}
```

**Response (201 Created):**
```json
{
  "id": 1002,
  "type": "library_scan",
  "status": "queued",
  "step": 0,
  "total_steps": 1,
  "subjobs_enqueued": 0,
  "supports_sub_jobs": true,
  "created_at": "2023-05-15T10:30:00Z",
  "updated_at": "2023-05-15T10:30:00Z"
}
```

#### Get Job

```http
GET /api/v1/admin/jobs/{id}
```

Retrieve details for a specific job.

**Response (200 OK):**
```json
{
  "id": 1001,
  "type": "library_scan",
  "status": "done",
  "step": 3,
  "total_steps": 3,
  "subjobs_enqueued": 42,
  "subjobs_completed": 42,
  "total_sub_jobs": 42,
  "supports_sub_jobs": true,
  "created_at": "2023-05-15T10:30:00Z",
  "updated_at": "2023-05-15T10:35:00Z"
}
```

#### Job Events

```http
GET /api/v1/admin/jobs/{id}/events
```

Stream job progress events as server-sent events (SSE).

**Response (200 OK):**
- `Content-Type: text/event-stream`
- One-line events with job progress

#### List Schedules

```http
GET /api/v1/admin/schedules
```

List configured job schedules.

**Response (200 OK):**
```json
[
  {
    "name": "library_scan",
    "cron_expression": "0 2 * * *",
    "enabled": true
  },
  {
    "name": "integrity_check",
    "cron_expression": "0 3 * * 0",
    "enabled": true
  }
]
```

#### Upsert Schedule

```http
PUT /api/v1/admin/schedules/{name}
Content-Type: application/json
```

Create or update a job schedule.

**Request Body:**
```json
{
  "cron_expression": "0 2 * * *",
  "enabled": true
}
```

**Response (200 OK):**
```json
{
  "name": "library_scan",
  "cron_expression": "0 2 * * *",
  "enabled": true
}
```

#### Delete Schedule

```http
DELETE /api/v1/admin/schedules/{name}
```

Remove a schedule.

**Response (204 No Content)**

---

## Common Response Codes

| Code | Meaning |
|---|---|
| 200 OK | Request succeeded |
| 201 Created | Resource created successfully |
| 204 No Content | Request succeeded; no response body |
| 400 Bad Request | Malformed request or validation error |
| 401 Unauthorized | Missing or invalid authentication token |
| 403 Forbidden | Insufficient permissions (not admin, device banned, collection access denied) |
| 404 Not Found | Resource not found |
| 429 Too Many Requests | Rate limit exceeded (login endpoint) |
| 500 Internal Server Error | Server error; check logs |
| 503 Service Unavailable | Database or critical service unavailable |

---

## Error Response Format

All error responses follow a standard format:

```json
{
  "error": "string",
  "message": "string (optional details)"
}
```

Example:
```json
{
  "error": "unauthorized",
  "message": "Device has been banned"
}
```

---

## Pagination

Endpoints that return multiple results support pagination via `offset` and `limit` query parameters:

- `offset` — Zero-based starting index (default: 0)
- `limit` — Maximum results per page (default: 50)

Example:
```
GET /api/v1/collections/abc-123/photos?offset=50&limit=25
```

The response includes a `total` field indicating the total number of items available.

---

## Device Management & LRU Eviction

When a user authenticates with a new device, a session is created. Users can have up to 15 active devices. When a 16th device logs in, the oldest device (by `last_seen_at`) is silently evicted, and subsequent requests from that device will fail with a 403 error.

Evicted devices can re-authenticate by logging in again with their `device_uuid`.

---

## Token Expiry & Refresh

- **Access tokens** expire after 15 minutes
- **Refresh tokens** expire after 90 days

When an access token expires, the client receives a 401 response. Use the `/api/v1/auth/refresh` endpoint with the refresh token to obtain a new access token. When the refresh token expires, the user must log in again.

---

## Rate Limiting

The `/api/v1/auth/login` endpoint is rate limited per username to prevent brute-force attacks. Clients that exceed the limit receive a 429 response and should wait before retrying.

---

## Notes

- All timestamps are in UTC (ISO 8601 format)
- Image hashes are BLAKE2b-256 content hashes, represented as hexadecimal strings
- File paths in responses are always relative to `MEDIA_PATH`; full paths are not exposed
- The server supports both WEBP (preferred) and JPEG formats for images (via `Accept` header or query parameter)
