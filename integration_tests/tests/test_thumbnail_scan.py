"""
Integration tests for the thumbnail_scan background job.

The job's contract is "rebuild missing thumbnails": for each piece of derived
imagery (collection cover, album cover/thumbnail, per-item video cover) it
checks whether the file exists on disk and only re-extracts when it does not.

Each test exercises one path end-to-end:
  1. Create the collection and wait for the initial scan / metadata pass.
  2. Confirm the expected thumbnails were generated and are served.
  3. Delete the derived file(s) from disk.
  4. Confirm the endpoint now returns 404.
  5. Trigger thumbnail_scan and wait for it to complete.
  6. Confirm the thumbnails come back.
"""

import os
import shutil
from typing import Any

import httpx
import psycopg
import pytest
from pydantic import BaseModel

from helpers.auth import bearer
from helpers.poll import wait_for_job
from tests.conftest import BASE_URL


# ── Shared models / helpers ───────────────────────────────────────────────────

class CreateCollectionResponse(BaseModel):
    id: int
    scan_job_id: int


def create_collection(token: str, name: str, relative_path: str, col_type: str) -> CreateCollectionResponse:
    r = httpx.post(
        f"{BASE_URL}/api/v1/admin/collections",
        headers=bearer(token),
        json={"name": name, "type": col_type, "relative_path": relative_path},
    )
    r.raise_for_status()
    return CreateCollectionResponse(**r.json())


def grant_collection_access(token: str, user_id: int, collection_id: int) -> None:
    r = httpx.patch(
        f"{BASE_URL}/api/v1/admin/users/{user_id}/collection_access",
        headers=bearer(token),
        json={"collection_ids": [collection_id]},
    )
    r.raise_for_status()


def trigger_thumbnail_scan(token: str, collection_id: int) -> int:
    r = httpx.post(
        f"{BASE_URL}/api/v1/admin/jobs",
        headers=bearer(token),
        json={"type": "thumbnail_scan", "related_id": collection_id, "related_type": "collection"},
    )
    r.raise_for_status()
    return r.json()["id"]


def fetch_image(token: str, path: str) -> tuple[int, bytes]:
    r = httpx.get(f"{BASE_URL}{path}", headers=bearer(token))
    return r.status_code, r.content


def list_album_ids(db_dsn: str, root_collection_id: int) -> list[int]:
    with psycopg.connect(db_dsn) as conn:
        cur = conn.execute(
            "SELECT id FROM audio_albums WHERE root_collection_id = %s ORDER BY id",
            (root_collection_id,),
        )
        return [row[0] for row in cur.fetchall()]


def list_video_hashes(db_dsn: str, root_collection_id: int) -> list[str]:
    with psycopg.connect(db_dsn) as conn:
        cur = conn.execute(
            """
            WITH RECURSIVE tree AS (
                SELECT id FROM collections WHERE id = %s
                UNION ALL
                SELECT c.id FROM collections c JOIN tree t ON c.parent_collection_id = t.id
            )
            SELECT DISTINCT mi.file_hash
            FROM media_items mi
            JOIN tree t ON mi.collection_id = t.id
            WHERE mi.missing_since IS NULL
              AND mi.hidden_at IS NULL
              AND mi.mime_type LIKE 'video/%%'
            """,
            (root_collection_id,),
        )
        return [row[0] for row in cur.fetchall()]


def list_video_item_ids(db_dsn: str, root_collection_id: int) -> list[int]:
    with psycopg.connect(db_dsn) as conn:
        cur = conn.execute(
            """
            WITH RECURSIVE tree AS (
                SELECT id FROM collections WHERE id = %s
                UNION ALL
                SELECT c.id FROM collections c JOIN tree t ON c.parent_collection_id = t.id
            )
            SELECT mi.id
            FROM media_items mi
            JOIN tree t ON mi.collection_id = t.id
            WHERE mi.missing_since IS NULL
              AND mi.hidden_at IS NULL
              AND mi.mime_type LIKE 'video/%%'
            ORDER BY mi.id
            """,
            (root_collection_id,),
        )
        return [row[0] for row in cur.fetchall()]


def video_cover_path(data_path: str, file_hash: str) -> str:
    """Mirrors imaging.VariantPath(dataPath, hash, 'cover', 'webp')."""
    return os.path.join(
        data_path, "derived_media",
        file_hash[0:2], file_hash[2:4], file_hash[4:],
        "cover.webp",
    )


def album_thumb_path(data_path: str, album_id: int) -> str:
    return os.path.join(data_path, "album_images", str(album_id), "thumb.webp")


def album_cover_path(data_path: str, album_id: int) -> str:
    return os.path.join(data_path, "album_images", str(album_id), "cover.webp")


def collection_cover_path(data_path: str, collection_id: int) -> str:
    return os.path.join(data_path, "collection_images", str(collection_id), "cover.webp")


# ── Audio: album cover/thumb regeneration ─────────────────────────────────────

class TestAudioAlbumArtRegen:
    """
    music-collection has two albums (Alpha, Beta). Each has one track with
    embedded album art:
      - album-alpha/01-first-light.mp3
      - album-beta/02-finale.mp3  (track 1 in this album has none)

    Initial scan extracts the art and writes thumb.webp + cover.webp under
    album_images/{albumID}/. Deleting those files should make the endpoints
    404; thumbnail_scan should then re-extract them.
    """

    collection_id: int = 0
    album_ids: list[int] = []

    def test_01_setup(self, admin_token, admin_user_id, db_dsn, data_path):
        resp = create_collection(admin_token, "Audio Thumb", "music-collection", "audio:music")
        TestAudioAlbumArtRegen.collection_id = resp.id
        grant_collection_access(admin_token, admin_user_id, resp.id)
        wait_for_job(admin_token, resp.scan_job_id, timeout=60)

        TestAudioAlbumArtRegen.album_ids = list_album_ids(db_dsn, resp.id)
        assert len(TestAudioAlbumArtRegen.album_ids) == 2, (
            f"expected 2 albums, got {TestAudioAlbumArtRegen.album_ids}"
        )

        # Initial scan should have produced both files for both albums.
        for album_id in TestAudioAlbumArtRegen.album_ids:
            assert os.path.isfile(album_thumb_path(data_path, album_id)), (
                f"thumb missing for album {album_id} after initial scan"
            )
            assert os.path.isfile(album_cover_path(data_path, album_id)), (
                f"cover missing for album {album_id} after initial scan"
            )

    def test_02_covers_served_initially(self, admin_token):
        for album_id in TestAudioAlbumArtRegen.album_ids:
            status, body = fetch_image(admin_token, f"/images/albums/{album_id}/thumb")
            assert status == 200 and len(body) > 0, f"thumb not served for album {album_id}"
            status, body = fetch_image(admin_token, f"/images/albums/{album_id}/cover")
            assert status == 200 and len(body) > 0, f"cover not served for album {album_id}"

    def test_03_delete_derived_files(self, admin_token, data_path):
        for album_id in TestAudioAlbumArtRegen.album_ids:
            album_dir = os.path.join(data_path, "album_images", str(album_id))
            shutil.rmtree(album_dir, ignore_errors=True)
            assert not os.path.exists(album_thumb_path(data_path, album_id))
            assert not os.path.exists(album_cover_path(data_path, album_id))

        # Endpoints should now 404
        for album_id in TestAudioAlbumArtRegen.album_ids:
            status, _ = fetch_image(admin_token, f"/images/albums/{album_id}/thumb")
            assert status == 404, f"expected 404 for missing thumb, got {status}"
            status, _ = fetch_image(admin_token, f"/images/albums/{album_id}/cover")
            assert status == 404, f"expected 404 for missing cover, got {status}"

    def test_04_thumbnail_scan_regenerates(self, admin_token, data_path):
        job_id = trigger_thumbnail_scan(admin_token, TestAudioAlbumArtRegen.collection_id)
        wait_for_job(admin_token, job_id, timeout=60)

        for album_id in TestAudioAlbumArtRegen.album_ids:
            assert os.path.isfile(album_thumb_path(data_path, album_id)), (
                f"thumb still missing for album {album_id} after thumbnail_scan"
            )
            assert os.path.isfile(album_cover_path(data_path, album_id)), (
                f"cover still missing for album {album_id} after thumbnail_scan"
            )
            status, body = fetch_image(admin_token, f"/images/albums/{album_id}/thumb")
            assert status == 200 and len(body) > 0
            status, body = fetch_image(admin_token, f"/images/albums/{album_id}/cover")
            assert status == 200 and len(body) > 0


# ── Video: per-item cover regeneration ────────────────────────────────────────

class TestVideoCoverRegen:
    """
    The three MP4s in video-collection/ have no embedded cover art, so the
    initial scan derives covers via the keyframe fallback. thumbnail_scan
    should do the same when a cover file is missing.
    """

    collection_id: int = 0
    file_hashes: list[str] = []
    item_ids: list[int] = []

    def test_01_setup(self, admin_token, admin_user_id, db_dsn, data_path):
        resp = create_collection(admin_token, "Video Thumb", "video-collection", "video:movie")
        TestVideoCoverRegen.collection_id = resp.id
        grant_collection_access(admin_token, admin_user_id, resp.id)
        wait_for_job(admin_token, resp.scan_job_id, timeout=120)

        TestVideoCoverRegen.file_hashes = list_video_hashes(db_dsn, resp.id)
        TestVideoCoverRegen.item_ids = list_video_item_ids(db_dsn, resp.id)
        assert len(TestVideoCoverRegen.file_hashes) == 3, (
            f"expected 3 video items, got {TestVideoCoverRegen.file_hashes}"
        )
        assert len(TestVideoCoverRegen.item_ids) == 3

        for h in TestVideoCoverRegen.file_hashes:
            assert os.path.isfile(video_cover_path(data_path, h)), (
                f"cover missing for hash {h} after initial scan"
            )

    def test_02_covers_served_initially(self, admin_token):
        for item_id in TestVideoCoverRegen.item_ids:
            status, body = fetch_image(admin_token, f"/images/videos/{item_id}/cover")
            assert status == 200 and len(body) > 0, f"cover not served for item {item_id}"

    def test_03_delete_derived_files(self, admin_token, data_path):
        for h in TestVideoCoverRegen.file_hashes:
            path = video_cover_path(data_path, h)
            os.remove(path)
            assert not os.path.exists(path)

        for item_id in TestVideoCoverRegen.item_ids:
            status, _ = fetch_image(admin_token, f"/images/videos/{item_id}/cover")
            assert status == 404, f"expected 404 for missing cover, got {status}"

    def test_04_thumbnail_scan_regenerates(self, admin_token, data_path):
        job_id = trigger_thumbnail_scan(admin_token, TestVideoCoverRegen.collection_id)
        wait_for_job(admin_token, job_id, timeout=120)

        for h in TestVideoCoverRegen.file_hashes:
            assert os.path.isfile(video_cover_path(data_path, h)), (
                f"cover still missing for hash {h} after thumbnail_scan"
            )
        for item_id in TestVideoCoverRegen.item_ids:
            status, body = fetch_image(admin_token, f"/images/videos/{item_id}/cover")
            assert status == 200 and len(body) > 0


# ── Photo: collection thumbnail regeneration ──────────────────────────────────

class TestPhotoCollectionThumbnailRegen:
    """
    Photo collections get a per-collection cover thumbnail picked from one of
    their items during the initial scan. Per-item variants are NOT in scope for
    thumbnail_scan, but the per-collection cover is.
    """

    collection_id: int = 0

    def test_01_setup(self, admin_token, admin_user_id, data_path):
        resp = create_collection(admin_token, "Photo Thumb", "photo-album-1", "image:photo")
        TestPhotoCollectionThumbnailRegen.collection_id = resp.id
        grant_collection_access(admin_token, admin_user_id, resp.id)
        wait_for_job(admin_token, resp.scan_job_id, timeout=60)

        assert os.path.isfile(collection_cover_path(data_path, resp.id)), (
            "collection cover missing after initial scan"
        )

    def test_02_cover_served_initially(self, admin_token):
        status, body = fetch_image(
            admin_token,
            f"/images/collections/{TestPhotoCollectionThumbnailRegen.collection_id}/cover",
        )
        assert status == 200 and len(body) > 0

    def test_03_delete_derived_files(self, admin_token, data_path):
        coll_id = TestPhotoCollectionThumbnailRegen.collection_id
        coll_dir = os.path.join(data_path, "collection_images", str(coll_id))
        shutil.rmtree(coll_dir, ignore_errors=True)
        assert not os.path.exists(collection_cover_path(data_path, coll_id))

        status, _ = fetch_image(admin_token, f"/images/collections/{coll_id}/cover")
        assert status == 404

    def test_04_thumbnail_scan_regenerates(self, admin_token, data_path):
        coll_id = TestPhotoCollectionThumbnailRegen.collection_id
        job_id = trigger_thumbnail_scan(admin_token, coll_id)
        wait_for_job(admin_token, job_id, timeout=60)

        assert os.path.isfile(collection_cover_path(data_path, coll_id)), (
            "collection cover still missing after thumbnail_scan"
        )
        status, body = fetch_image(admin_token, f"/images/collections/{coll_id}/cover")
        assert status == 200 and len(body) > 0
