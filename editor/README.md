# Deprecated editor

Develop the editor in
[Ara-yjx/amp-generator-beta](https://github.com/Ara-yjx/amp-generator-beta)
(`src/component/chatroom/`, `src/data/chatroom/`).
Local beta launch: `bash scripts/start-chatroom-beta.sh` in that repo after
`npm ci --legacy-peer-deps`. See its `docs/chatroom-editor.md`.

This directory is retained for comparison and temporary fallback.
Only necessary migration/compatibility changes should be made here.
The hosted proto editor remains available until its replacement is released.
Republishing it requires explicit `PUBLISH_LEGACY_EDITOR=1`.
