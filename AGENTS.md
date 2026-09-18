# Editor ownership

- `editor/` is deprecated but retained. Implement editor features/fixes in
  `Ara-yjx/amp-generator-beta`; only migration/compatibility work belongs here.
- Backend, widget and CDK development continue in this repo.
- Do not republish the legacy editor as a side effect of a widget release.
  Combined publication requires an explicit legacy-editor opt-in.
