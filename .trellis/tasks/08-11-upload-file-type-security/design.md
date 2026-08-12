# Design: Main Upload Dangerous-Extension Denylist

## Boundary

Add one shared constant and a small filename validator in `src/api/routes/upload.py`. Call it at the start of `POST /api/upload/file`, before storage initialization is used for any write and before the request body is spooled.

Normalize with the filename basename and lowercase/casefold the final suffix. Reject missing/unsafe filename control characters only if the existing FastAPI upload contract cannot safely extract a basename; otherwise preserve current non-dangerous behavior.

## Policy

Use an explicit denylist for the minimal compatibility-preserving fix. Cover executable/install/system formats, shell/command scripts, server-side web scripts, browser-active HTML/SVG, Java archives/classes, shortcuts and macro-enabled Office formats. Do not turn this task into a global allowlist or content scanner.

The category-specific allowlists continue to run after the universal dangerous-extension check. This closes the `UNKNOWN` bypass while preserving all other route behavior.

## Error and Test Contract

Raise HTTP 400 with a stable message such as `Dangerous file extension '.exe' is not allowed`. Tests call the route with a storage/file object that fails if read or written, proving early rejection. Add allowed and unknown-nonblocked compatibility cases.

## Rollback

Rollback is removal of the denylist helper/call and its tests. No schema, storage record, dependency, lockfile, frontend or historical-data changes are required.
