package definitions

import (
	"context"
	"fmt"
	"log/slog"
	"path/filepath"

	"github.com/stevenvi/bokeh-mediaserver/internal/constants"
	"github.com/stevenvi/bokeh-mediaserver/internal/imaging"
	"github.com/stevenvi/bokeh-mediaserver/internal/jobs"
	"github.com/stevenvi/bokeh-mediaserver/internal/repository"
)

// ThumbnailScanMeta describes the thumbnail_scan job type.
var ThumbnailScanMeta = jobs.JobMeta{
	Description: "Rebuild missing thumbnails for a collection",
	TotalSteps:  3,
}

// HandleThumbnailScan returns a job handler that rebuilds missing thumbnails
// for a collection and all its descendants. The job's related_id must be the collection ID.
func HandleThumbnailScan(mediaPath, dataPath string) jobs.JobHandler {
	return func(ctx context.Context, jc *jobs.JobContext) error {
		db, job, et := jc.DB, jc.Job, jc.Et
		if job.RelatedID == nil {
			return fmt.Errorf("thumbnail_scan job %d has no related_id", job.ID)
		}
		collectionID := *job.RelatedID

		// Step 1: Walk all sub-collections and generate missing collection thumbnails.
		jc.SetStep(ctx, 1)
		_ = repository.JobUpdateProgress(ctx, db, job.ID, "Generating missing collection thumbnails")
		slog.Info("thumbnail scan: walking collections", "job_id", job.ID, "collection_id", collectionID)

		collIDs, err := repository.CollectionGetDescendantCollectionIDs(ctx, db, collectionID)
		if err != nil {
			return fmt.Errorf("list descendant collections: %w", err)
		}
		// Include the root collection itself
		allCollIDs := append([]int64{collectionID}, collIDs...)

		var generatedColls int
		for _, cid := range allCollIDs {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if imaging.CollectionThumbnailExists(dataPath, cid) {
				continue
			}
			if err := GenerateThumbnailForCollection(ctx, db, dataPath, cid); err != nil {
				slog.Warn("thumbnail scan: skip collection", "collection_id", cid, "err", err)
				continue
			}
			generatedColls++
		}
		slog.Info("thumbnail scan: collection thumbnails done", "job_id", job.ID, "generated", generatedColls)

		// Step 2: Regenerate missing album art for audio collections.
		jc.SetStep(ctx, 2)
		_ = repository.JobUpdateProgress(ctx, db, job.ID, "Regenerating missing album thumbnails")

		albumIDs, err := repository.AlbumIDsInCollection(ctx, db, collectionID)
		if err != nil {
			// Not an audio collection or no albums — silently skip
			slog.Warn("thumbnail scan: query albums", "collection_id", collectionID, "err", err)
		}

		var generatedAlbums int
		for _, albumID := range albumIDs {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			// Skip if both variants already exist
			if imaging.AlbumThumbnailExists(dataPath, albumID) && imaging.AlbumCoverExists(dataPath, albumID) {
				continue
			}

			// Find a track with embedded art
			paths, err := repository.AlbumTrackRelPathsWithEmbeddedArt(ctx, db, albumID)
			if err != nil {
				slog.Warn("thumbnail scan: query tracks with embedded art", "album_id", albumID, "err", err)
				continue
			}
			if len(paths) == 0 {
				continue
			}

			fsPath := filepath.Join(mediaPath, paths[0])
			if err := extractAndGenerateAlbumArt(et, fsPath, dataPath, albumID); err != nil {
				slog.Warn("thumbnail scan: extract album art", "album_id", albumID, "err", err)
				continue
			}
			generatedAlbums++
		}
		slog.Info("thumbnail scan: album thumbnails done", "job_id", job.ID, "generated", generatedAlbums)

		// Step 3: Regenerate missing per-item video covers.
		jc.SetStep(ctx, 3)
		_ = repository.JobUpdateProgress(ctx, db, job.ID, "Regenerating missing video covers")

		videos, err := repository.VideoItemsForThumbnailScan(ctx, db, collectionID)
		if err != nil {
			slog.Warn("thumbnail scan: query video items", "collection_id", collectionID, "err", err)
			return nil
		}

		var collType constants.CollectionType
		if coll, err := repository.CollectionGet(ctx, db, collectionID); err != nil {
			slog.Warn("thumbnail scan: determine collection type", "collection_id", collectionID, "err", err)
		} else {
			collType = constants.CollectionType(coll.Type)
		}
		coverWidthRatio, coverHeightRatio := videoCoverAspectRatio(collType)

		var generatedVideos int
		for _, v := range videos {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if v.ManualThumbnail {
				continue
			}
			coverPath := imaging.VariantPath(dataPath, v.FileHash, "cover", "webp")
			if fileExists(coverPath) {
				continue
			}
			fsPath := filepath.Join(mediaPath, v.RelativePath)
			exifData := extractExif(et, fsPath, "exiftool extract failed for video", true)
			coverArtBytes := extractVideoCoverBytes(fsPath, exifData)
			if err := generateVideoCover(coverArtBytes, fsPath, dataPath, v.FileHash, v.DurationSeconds, coverWidthRatio, coverHeightRatio); err != nil {
				slog.Warn("thumbnail scan: regenerate video cover", "item_id", v.ItemID, "err", err)
				continue
			}
			generatedVideos++
		}
		slog.Info("thumbnail scan: video covers done", "job_id", job.ID, "generated", generatedVideos)

		return nil
	}
}
