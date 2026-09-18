# Stimulize chatroom

Chatroom runtime (`backend/`), widget (`frontend/`), infrastructure (`cdk/`),
and design documents (`docs/`).

## Editor: deprecated

Editor development has moved to
[Ara-yjx/amp-generator-beta](https://github.com/Ara-yjx/amp-generator-beta),
under `src/component/chatroom/` and `src/data/chatroom/`.
The batch editor migration is on `dev/yejiaxi/batch-editor-integration`.
See that repo's `docs/chatroom-editor.md` for local beta testing.

`editor/` and its existing hosted page remain available for comparison and
temporary fallback. New editor features and fixes belong in amp; do not
maintain both copies. No history rewrite or directory deletion is required.
This source migration does not imply the hosted amp site has been updated.

Combined editor/widget publication is now explicit: set
`PUBLISH_LEGACY_EDITOR=1` when using `scripts/build_pages_site.sh`,
or opt in through the manual Pages workflow. Building the widget alone still
uses `npm --prefix frontend run build`; publish only `chatroom.min.js` into
the existing Pages artifact tree to preserve the hosted legacy editor.
