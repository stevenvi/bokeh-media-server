"""
Integration tests for admin DELETE endpoints that remove manually-uploaded
cover/thumbnail imagery:

  - DELETE /api/v1/admin/collections/{id}/cover
  - DELETE /api/v1/admin/media/{id}/cover
  - DELETE /api/v1/admin/artists/{id}/image
  - DELETE /api/v1/admin/albums/{albumId}/cover

Each test class follows the same shape:
  1. Create the parent collection (photo, video, or music) and wait for
     the initial scan + processing to finish.
  2. POST a manual cover/image, confirm the manual flag flips to true and
     the file is on disk.
  3. DELETE the manual cover, confirm the response is 204, the file is gone,
     and the manual flag flips back to false.
  4. Confirm a second DELETE on the same target is still 204 (idempotent).
"""

import os
from pathlib import Path

import httpx
import psycopg
import pytest

from helpers.auth import bearer
from helpers.poll import wait_for_job
from tests.conftest import BASE_URL


# ── Fixtures / helpers ────────────────────────────────────────────────────────

# A real image file from the photo testdata, used as the upload payload for
# every endpoint. JPEG so libvips/govips has no trouble decoding.
SAMPLE_IMAGE = Path(__file__).resolve().parent.parent / "testdata" / "media" / "photo-album-1" / "rollercoaster.jpg"


@pytest.fixture(scope="module")
def sample_image_bytes() -> bytes:
    assert SAMPLE_IMAGE.is_file(), f"missing sample image: {SAMPLE_IMAGE}"
    return SAMPLE_IMAGE.read_bytes()


def create_collection(token: str, name: str, relative_path: str, col_type: str) -> tuple[int, int]:
    r = httpx.post(
        f"{BASE_URL}/api/v1/admin/collections",
        headers=bearer(token),
        json={"name": name, "type": col_type, "relative_path": relative_path},
    )
    r.raise_for_status()
    body = r.json()
    return body["id"], body["scan_job_id"]


def grant_collection_access(token: str, user_id: int, collection_id: int) -> None:
    r = httpx.patch(
        f"{BASE_URL}/api/v1/admin/users/{user_id}/collection_access",
        headers=bearer(token),
        json={"collection_ids": [collection_id]},
    )
    r.raise_for_status()


def collection_cover_path(data_path: str, collection_id: int) -> str:
    return os.path.join(data_path, "collection_images", str(collection_id), "cover.webp")


def video_cover_path(data_path: str, file_hash: str) -> str:
    return os.path.join(
        data_path, "derived_media",
        file_hash[0:2], file_hash[2:4], file_hash[4:],
        "cover.webp",
    )


def artist_image_path(data_path: str, artist_id: int) -> str:
    return os.path.join(data_path, "artist_images", str(artist_id), "cover.webp")


def album_thumb_path(data_path: str, album_id: int) -> str:
    return os.path.join(data_path, "album_images", str(album_id), "thumb.webp")


def album_cover_path(data_path: str, album_id: int) -> str:
    return os.path.join(data_path, "album_images", str(album_id), "cover.webp")


def scalar(db_dsn: str, query: str, *params) -> object:
    with psycopg.connect(db_dsn) as conn:
        cur = conn.execute(query, params)
        row = cur.fetchone()
        return row[0] if row else None


# ── Collection cover ──────────────────────────────────────────────────────────

class TestDeleteCollectionCover:
    """A photo collection initially gets its thumbnail derived from one of its
    photos. After uploading a manual cover (manual_thumbnail=true), DELETE must
    remove the file and reset the flag so the next thumbnail scan can repick."""

    collection_id: int = 0

    def test_01_setup(self, admin_token, admin_user_id, data_path):
        coll_id, scan_job_id = create_collection(
            admin_token, "Cover Delete Photos", "photo-album-1", "image:photo",
        )
        TestDeleteCollectionCover.collection_id = coll_id
        grant_collection_access(admin_token, admin_user_id, coll_id)
        wait_for_job(admin_token, scan_job_id, timeout=120)
        assert os.path.isfile(collection_cover_path(data_path, coll_id))

    def test_02_upload_manual_cover(self, admin_token, db_dsn, data_path, sample_image_bytes):
        coll_id = TestDeleteCollectionCover.collection_id
        r = httpx.post(
            f"{BASE_URL}/api/v1/admin/collections/{coll_id}/cover",
            headers=bearer(admin_token),
            files={"cover": ("cover.jpg", sample_image_bytes, "image/jpeg")},
        )
        assert r.status_code == 204, r.text
        assert os.path.isfile(collection_cover_path(data_path, coll_id))
        assert scalar(db_dsn, "SELECT manual_thumbnail FROM collections WHERE id = %s", coll_id) is True

    def test_03_delete_clears_file_and_flag(self, admin_token, db_dsn, data_path):
        coll_id = TestDeleteCollectionCover.collection_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/collections/{coll_id}/cover",
            headers=bearer(admin_token),
        )
        assert r.status_code == 204, r.text
        assert not os.path.exists(collection_cover_path(data_path, coll_id))
        assert scalar(db_dsn, "SELECT manual_thumbnail FROM collections WHERE id = %s", coll_id) is False

    def test_04_delete_is_idempotent(self, admin_token, db_dsn):
        coll_id = TestDeleteCollectionCover.collection_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/collections/{coll_id}/cover",
            headers=bearer(admin_token),
        )
        assert r.status_code == 204, r.text
        assert scalar(db_dsn, "SELECT manual_thumbnail FROM collections WHERE id = %s", coll_id) is False


# ── Video cover ───────────────────────────────────────────────────────────────

class TestDeleteVideoCover:
    """The first MP4 in video-collection has no embedded art, so the initial
    scan derives a cover via the keyframe fallback. After uploading a manual
    cover, DELETE must remove the cover file and clear manual_thumbnail."""

    collection_id: int = 0
    item_id: int = 0
    file_hash: str = ""

    def test_01_setup(self, admin_token, admin_user_id, db_dsn, data_path):
        coll_id, scan_job_id = create_collection(
            admin_token, "Cover Delete Videos", "video-collection", "video:movie",
        )
        TestDeleteVideoCover.collection_id = coll_id
        grant_collection_access(admin_token, admin_user_id, coll_id)
        wait_for_job(admin_token, scan_job_id, timeout=180)

        with psycopg.connect(db_dsn) as conn:
            cur = conn.execute(
                """
                WITH RECURSIVE tree AS (
                    SELECT id FROM collections WHERE id = %s
                    UNION ALL
                    SELECT c.id FROM collections c JOIN tree t ON c.parent_collection_id = t.id
                )
                SELECT mi.id, mi.file_hash
                FROM media_items mi
                JOIN tree t ON mi.collection_id = t.id
                WHERE mi.mime_type LIKE 'video/%%'
                  AND mi.missing_since IS NULL
                  AND mi.hidden_at IS NULL
                ORDER BY mi.id
                LIMIT 1
                """,
                (coll_id,),
            )
            row = cur.fetchone()
            assert row is not None, "no video items found after scan"
            TestDeleteVideoCover.item_id, TestDeleteVideoCover.file_hash = row

        assert os.path.isfile(video_cover_path(data_path, TestDeleteVideoCover.file_hash))

    def test_02_upload_manual_cover(self, admin_token, db_dsn, data_path, sample_image_bytes):
        item_id = TestDeleteVideoCover.item_id
        r = httpx.post(
            f"{BASE_URL}/api/v1/admin/media/{item_id}/cover",
            headers=bearer(admin_token),
            files={"cover": ("cover.jpg", sample_image_bytes, "image/jpeg")},
        )
        assert r.status_code == 204, r.text
        assert os.path.isfile(video_cover_path(data_path, TestDeleteVideoCover.file_hash))
        assert scalar(
            db_dsn,
            "SELECT manual_thumbnail FROM video_metadata WHERE media_item_id = %s",
            item_id,
        ) is True

    def test_03_delete_clears_file_and_flag(self, admin_token, db_dsn, data_path):
        item_id = TestDeleteVideoCover.item_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/media/{item_id}/cover",
            headers=bearer(admin_token),
        )
        assert r.status_code == 204, r.text
        assert not os.path.exists(video_cover_path(data_path, TestDeleteVideoCover.file_hash))
        assert scalar(
            db_dsn,
            "SELECT manual_thumbnail FROM video_metadata WHERE media_item_id = %s",
            item_id,
        ) is False

    def test_04_delete_is_idempotent(self, admin_token, db_dsn):
        item_id = TestDeleteVideoCover.item_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/media/{item_id}/cover",
            headers=bearer(admin_token),
        )
        assert r.status_code == 204, r.text
        assert scalar(
            db_dsn,
            "SELECT manual_thumbnail FROM video_metadata WHERE media_item_id = %s",
            item_id,
        ) is False

    def test_05_delete_unknown_video_404s(self, admin_token):
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/media/9999999/cover",
            headers=bearer(admin_token),
        )
        assert r.status_code == 404, r.text


# ── Artist image ──────────────────────────────────────────────────────────────

class TestDeleteArtistImage:
    """music-collection contains a single artist (Artist One). Upload a manual
    image, then DELETE must remove the file and reset manual_thumbnail."""

    collection_id: int = 0
    artist_id: int = 0

    def test_01_setup(self, admin_token, admin_user_id, db_dsn):
        coll_id, scan_job_id = create_collection(
            admin_token, "Cover Delete Music Artists", "music-collection", "audio:music",
        )
        TestDeleteArtistImage.collection_id = coll_id
        grant_collection_access(admin_token, admin_user_id, coll_id)
        wait_for_job(admin_token, scan_job_id, timeout=120)

        with psycopg.connect(db_dsn) as conn:
            cur = conn.execute(
                """
                SELECT DISTINCT a.id
                FROM artists a
                JOIN audio_albums al ON al.artist_id = a.id
                WHERE al.root_collection_id = %s
                ORDER BY a.id
                LIMIT 1
                """,
                (coll_id,),
            )
            row = cur.fetchone()
            assert row is not None, "no artist found after scan"
            TestDeleteArtistImage.artist_id = row[0]

    def test_02_upload_manual_image(self, admin_token, db_dsn, data_path, sample_image_bytes):
        artist_id = TestDeleteArtistImage.artist_id
        r = httpx.post(
            f"{BASE_URL}/api/v1/admin/artists/{artist_id}/image",
            headers=bearer(admin_token),
            files={"image": ("image.jpg", sample_image_bytes, "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        assert os.path.isfile(artist_image_path(data_path, artist_id))
        assert scalar(db_dsn, "SELECT manual_thumbnail FROM artists WHERE id = %s", artist_id) is True

    def test_03_delete_clears_file_and_flag(self, admin_token, db_dsn, data_path):
        artist_id = TestDeleteArtistImage.artist_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/artists/{artist_id}/image",
            headers=bearer(admin_token),
        )
        assert r.status_code == 200, r.text
        assert not os.path.exists(artist_image_path(data_path, artist_id))
        assert scalar(db_dsn, "SELECT manual_thumbnail FROM artists WHERE id = %s", artist_id) is False

    def test_04_delete_is_idempotent(self, admin_token, db_dsn):
        artist_id = TestDeleteArtistImage.artist_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/artists/{artist_id}/image",
            headers=bearer(admin_token),
        )
        assert r.status_code == 200, r.text
        assert scalar(db_dsn, "SELECT manual_thumbnail FROM artists WHERE id = %s", artist_id) is False


# ── Album cover ───────────────────────────────────────────────────────────────

class TestDeleteAlbumCover:
    """music-collection has two albums; the upload endpoint writes both a 400px
    thumb.webp and a 1280px cover.webp. DELETE must remove both files and
    clear manual_cover."""

    collection_id: int = 0
    album_id: int = 0

    def test_01_setup(self, admin_token, admin_user_id, db_dsn):
        coll_id, scan_job_id = create_collection(
            admin_token, "Cover Delete Music Albums", "music-collection", "audio:music",
        )
        TestDeleteAlbumCover.collection_id = coll_id
        grant_collection_access(admin_token, admin_user_id, coll_id)
        wait_for_job(admin_token, scan_job_id, timeout=120)

        with psycopg.connect(db_dsn) as conn:
            cur = conn.execute(
                "SELECT id FROM audio_albums WHERE root_collection_id = %s ORDER BY id LIMIT 1",
                (coll_id,),
            )
            row = cur.fetchone()
            assert row is not None, "no album found after scan"
            TestDeleteAlbumCover.album_id = row[0]

    def test_02_upload_manual_cover(self, admin_token, db_dsn, data_path, sample_image_bytes):
        album_id = TestDeleteAlbumCover.album_id
        r = httpx.post(
            f"{BASE_URL}/api/v1/admin/albums/{album_id}/cover",
            headers=bearer(admin_token),
            files={"image": ("cover.jpg", sample_image_bytes, "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        assert os.path.isfile(album_thumb_path(data_path, album_id))
        assert os.path.isfile(album_cover_path(data_path, album_id))
        assert scalar(db_dsn, "SELECT manual_cover FROM audio_albums WHERE id = %s", album_id) is True

    def test_03_delete_clears_files_and_flag(self, admin_token, db_dsn, data_path):
        album_id = TestDeleteAlbumCover.album_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/albums/{album_id}/cover",
            headers=bearer(admin_token),
        )
        assert r.status_code == 200, r.text
        assert not os.path.exists(album_thumb_path(data_path, album_id))
        assert not os.path.exists(album_cover_path(data_path, album_id))
        assert scalar(db_dsn, "SELECT manual_cover FROM audio_albums WHERE id = %s", album_id) is False

    def test_04_delete_is_idempotent(self, admin_token, db_dsn):
        album_id = TestDeleteAlbumCover.album_id
        r = httpx.delete(
            f"{BASE_URL}/api/v1/admin/albums/{album_id}/cover",
            headers=bearer(admin_token),
        )
        assert r.status_code == 200, r.text
        assert scalar(db_dsn, "SELECT manual_cover FROM audio_albums WHERE id = %s", album_id) is False
