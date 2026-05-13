// Package version exposes build-time metadata about the server binary.
//
// Values are injected at build time via -ldflags "-X". When the binary is
// built without ldflags (e.g. `go run ./cmd/server`), the defaults below apply.
package version

var (
	Version = "dev"
	Commit  = "unknown"
	BuiltAt = "unknown"
)
