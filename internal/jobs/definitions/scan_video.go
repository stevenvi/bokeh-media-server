package definitions

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/stevenvi/bokeh-mediaserver/internal/constants"
	"github.com/stevenvi/bokeh-mediaserver/internal/imaging"
	"github.com/stevenvi/bokeh-mediaserver/internal/jobs"
	jobsutils "github.com/stevenvi/bokeh-mediaserver/internal/jobs/utils"
	"github.com/stevenvi/bokeh-mediaserver/internal/repository"
	"github.com/stevenvi/bokeh-mediaserver/internal/utils"
)

// yearSuffixRegex matches a 4-digit year in parentheses at the end of a filename,
// e.g. "The Matrix (1999)".
var yearSuffixRegex = regexp.MustCompile(`\((\d{4})\)\s*$`)

// ScanVideoMeta describes the scan_video sub-job type.
var ScanVideoMeta = jobs.JobMeta{
	Description: "Extract video metadata and generate thumbnail",
	TotalSteps:  1,
}


// HandleScanVideo returns a job handler that processes a single video file.
func HandleScanVideo(mediaPath, dataPath string, transcodeBitrateKbps int) jobs.JobHandler {
	return func(ctx context.Context, jc *jobs.JobContext) error {
		db, job := jc.DB, jc.Job
		if job.RelatedID == nil {
			return fmt.Errorf("scan_video job %d has no related_id", job.ID)
		}
		itemID := *job.RelatedID

		relativePath, _, fileHash, err := repository.MediaItemForProcessing(ctx, db, itemID)
		if err != nil {
			return fmt.Errorf("fetch media item %d: %w", itemID, err)
		}

		fsPath := filepath.Join(mediaPath, relativePath)

		// --- Step 1: exiftool extraction ---
		// Include binary tags as cover art detection relies on the key
		// being present in the returned tag map.
		exifData := extractExif(jc.Et, fsPath, "exiftool extract failed for video", true)

		// Duration
		durationFloat := parseDuration(exifData)
		var durationSeconds *int
		if durationFloat != nil {
			v := int(*durationFloat)
			durationSeconds = &v
		}

		// Width/height
		width := jobsutils.ExifInt(exifData, "ImageWidth")
		height := jobsutils.ExifInt(exifData, "ImageHeight")

		// Bitrate
		bitrateKbps := parseVideoBitrate(exifData)

		// Video/audio codec
		videoCodec := jobsutils.ExifStr(exifData, "VideoCodec")
		if videoCodec == nil {
			videoCodec = jobsutils.ExifStr(exifData, "CompressorID")
		}
		audioCodec := jobsutils.ExifStr(exifData, "AudioFormat")
		if audioCodec == nil {
			audioCodec = jobsutils.ExifStr(exifData, "AudioCodec")
		}

		// Title
		exifTitle := jobsutils.ExifStr(exifData, "Title")

		// Date from metadata/file timestamp (fallback)
		exifDate := createdAt(fsPath, exifData)

		// Cover art bytes — extracted separately via exiftool -b
		coverArtBytes := extractVideoCoverBytes(fsPath, exifData)

		// --- Step 2: root collection type for home movie filename fallback ---
		collType, err := repository.MediaItemRootCollectionType(ctx, db, itemID)
		if err != nil {
			slog.Warn("could not determine collection type", "item_id", itemID, "err", err)
		}

		var finalTitle *string
		var dateString *string
		var author *string

		if exifTitle != nil && strings.TrimSpace(*exifTitle) != "" {
			finalTitle = exifTitle
		}

		basename := strings.TrimSuffix(filepath.Base(fsPath), filepath.Ext(fsPath))

		// Priority 1: date prefix from front of filename (e.g. "2024.06.02-04 Trip")
		if strippedName, rawPrefix := utils.ExtractDatePrefixStr(basename); rawPrefix != nil {
			dateString = rawPrefix
			if finalTitle == nil && strippedName != "" {
				finalTitle = &strippedName
			}
		}

		// Priority 2: year in parens at end of filename, e.g. "The Matrix (1999)"
		if dateString == nil {
			if m := yearSuffixRegex.FindStringSubmatch(basename); m != nil {
				dateString = &m[1]
			}
		}

		// Priority 3: metadata/file timestamp
		if dateString == nil && exifDate != nil {
			s := exifDate.UTC().Format("2006.01.02")
			dateString = &s
		}

		// Apply title to media_items
		if finalTitle != nil && strings.TrimSpace(*finalTitle) != "" {
			if err := repository.MediaItemUpdateTitle(ctx, db, itemID, *finalTitle); err != nil {
				slog.Warn("update title from video metadata", "item_id", itemID, "err", err)
			}
		}

		// --- Step 3: upsert video_metadata ---
		if err := repository.VideoUpsert(ctx, db, itemID,
			durationSeconds, width, height, bitrateKbps,
			videoCodec, audioCodec,
			dateString, author,
		); err != nil {
			return fmt.Errorf("upsert video_metadata: %w", err)
		}

		// --- Step 4: cover art ---
		// Manual thumbnails are never overwritten; skip generation if a cover already exists.
		coverWidthRatio, coverHeightRatio := videoCoverAspectRatio(collType)
		notManual, err := repository.VideoHasManualThumbnail(ctx, db, itemID)
		if err == nil && notManual {
			coverPath := imaging.VariantPath(dataPath, fileHash, "cover", "webp")
			if !fileExists(coverPath) {
				if err := generateVideoCover(coverArtBytes, fsPath, dataPath, fileHash, durationSeconds, coverWidthRatio, coverHeightRatio); err != nil {
					slog.Warn("generate video cover", "item_id", itemID, "err", err)
				}
			}
		}

		// --- Step 5: auto-generate collection thumbnail for home movies ---
		if collType == constants.CollectionTypeHomeMovie {
			if collID, err := repository.MediaItemCollectionID(ctx, db, itemID); err == nil {
				if !imaging.CollectionThumbnailExists(dataPath, collID) {
					if err := GenerateThumbnailForCollection(ctx, db, dataPath, collID); err != nil {
						slog.Warn("auto-generate collection thumbnail for home movie", "collection_id", collID, "err", err)
					}
				}
			}
		}

		// --- Step 6: attach transcode sub-job if needed ---
		if bitrateKbps != nil && *bitrateKbps > transcodeBitrateKbps && transcodeBitrateKbps > 0 {
			needsTranscode, err := repository.VideoNeedsTranscode(ctx, db, itemID)
			if err != nil {
				slog.Warn("check transcode status", "item_id", itemID, "err", err)
			} else if needsTranscode {
				jc.AttachTranscodeSubJob(ctx, itemID)
			}
		}

		slog.Debug("finished processing video file", "item_id", itemID)
		return nil
	}
}

// extractBinaryTag runs exiftool with -b to extract a binary tag.
func extractBinaryTag(fsPath, tag string) ([]byte, error) {
	cmd := exec.Command("exiftool", "-b", tag, fsPath)
	return cmd.Output()
}

// extractVideoCoverBytes pulls the raw embedded cover image from a video file.
// exifData is consulted first to avoid spawning exiftool -b when neither
// Picture nor CoverArt is present.
func extractVideoCoverBytes(fsPath string, exifData map[string]any) []byte {
	var coverArtBytes []byte
	if _, hasPic := exifData["Picture"]; hasPic {
		coverArtBytes, _ = extractBinaryTag(fsPath, "-Picture")
	}
	if len(coverArtBytes) == 0 {
		if _, hasCover := exifData["CoverArt"]; hasCover {
			coverArtBytes, _ = extractBinaryTag(fsPath, "-CoverArt")
		}
	}
	return coverArtBytes
}

// videoCoverAspectRatio returns the desired (width, height) ratio for a
// video cover image given the containing collection's type. video:movie uses
// a 2:3 poster shape; everything else uses a 4:3 frame.
func videoCoverAspectRatio(collType constants.CollectionType) (int, int) {
	if collType == constants.CollectionTypeMovie {
		return 2, 3
	}
	return 4, 3
}

// generateVideoCover writes the per-item cover for a video. If embedded art
// is available it's preferred; otherwise a keyframe is extracted at the given
// duration. Returns an error only when neither path produces a cover.
func generateVideoCover(coverArtBytes []byte, fsPath, dataPath, fileHash string, durationSeconds *int, widthRatio, heightRatio int) error {
	if len(coverArtBytes) > 0 {
		return imaging.GenerateVideoCoverFromBytes(coverArtBytes, dataPath, fileHash, widthRatio, heightRatio)
	}
	if durationSeconds != nil && *durationSeconds > 0 {
		return imaging.GenerateVideoCoverFromFrame(fsPath, dataPath, fileHash, *durationSeconds, widthRatio, heightRatio)
	}
	return fmt.Errorf("no embedded cover and no duration for keyframe extraction")
}

// parseVideoBitrate reads AvgBitrate from exifData and converts to kbps.
func parseVideoBitrate(exifData map[string]any) *int {
	v, ok := exifData["AvgBitrate"]
	if !ok || v == nil {
		return nil
	}
	s, ok := v.(string)
	if !ok {
		return nil
	}
	s = strings.TrimSpace(s)

	if strings.HasSuffix(s, " Mbps") {
		f, err := strconv.ParseFloat(strings.TrimSuffix(s, " Mbps"), 64)
		if err != nil {
			return nil
		}
		kbps := int(f * 1000)
		return &kbps
	}
	if strings.HasSuffix(s, " kbps") {
		n, err := strconv.Atoi(strings.TrimSuffix(s, " kbps"))
		if err != nil {
			return nil
		}
		return &n
	}
	if strings.HasSuffix(s, " bps") {
		n, err := strconv.Atoi(strings.TrimSuffix(s, " bps"))
		if err != nil {
			return nil
		}
		kbps := n / 1000
		return &kbps
	}
	if n, err := strconv.Atoi(s); err == nil {
		return &n
	}
	return nil
}

// fileExists reports whether the file at path exists on disk.
func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
