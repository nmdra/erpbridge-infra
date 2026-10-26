// Command sqlite-snapshot takes a consistent snapshot of the ERPBridge SQLite
// registry. It runs inside the running server container, which has no sqlite3
// CLI and no Python.
//
// Provenance: this file is the reviewed helper that performed the live alpha.5
// upgrade on 2026-10-15, promoted from the ignored evidence directory into the
// repository so the cutover does not depend on an unreviewed rebuild. The
// executable logic below is unchanged.
//
// Invocation: sqlite-snapshot <absolute-source> <absolute-new-destination>
// It prints one JSON line containing snapshot_created, source_mode, and
// journal_mode. The source connection is read-only, so the helper cannot change
// the registry it snapshots.
// Temporary backup helper. The source connection is read-only.
package main

import (
 "context"
 "database/sql"
 "encoding/json"
 "fmt"
 "net/url"
 "os"
 "path/filepath"
 "time"

 _ "modernc.org/sqlite"
)

func run() error {
 if len(os.Args) != 3 { return fmt.Errorf("expected source and new destination") }
 source, destination := os.Args[1], os.Args[2]
 if !filepath.IsAbs(source) || !filepath.IsAbs(destination) || source == destination { return fmt.Errorf("invalid paths") }
 if _, err := os.Lstat(destination); !os.IsNotExist(err) { return fmt.Errorf("destination must not exist") }
 uri := (&url.URL{Scheme: "file", Path: source, RawQuery: "mode=ro"}).String()
 db, err := sql.Open("sqlite", uri)
 if err != nil { return err }
 defer db.Close()
 db.SetMaxOpenConns(1)
 ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
 defer cancel()
 if _, err := db.ExecContext(ctx, "PRAGMA busy_timeout=10000"); err != nil { return err }
 var mode string
 if err := db.QueryRowContext(ctx, "PRAGMA journal_mode").Scan(&mode); err != nil { return err }
 // VACUUM INTO creates a transactionally consistent, standalone snapshot.
 // mode=ro prevents this connection from changing the source database.
 if _, err := db.ExecContext(ctx, "VACUUM INTO ?", destination); err != nil { return err }
 if err := os.Chmod(destination, 0600); err != nil { return err }
 return json.NewEncoder(os.Stdout).Encode(map[string]any{"snapshot_created":true,"source_mode":"ro","journal_mode":mode})
}

func main() {
 if err := run(); err != nil {
  fmt.Fprintln(os.Stderr, "SQLite snapshot failed; no source writes were requested")
  os.Exit(1)
 }
}
