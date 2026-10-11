# Publishing

## Releases

Releases are created by the `Publish Release` workflow
(`.github/workflows/publish-release.yml`) on every push to `main` that changes
`custom_components/worx_vision_cloud/manifest.json`, `CHANGELOG.md`, `hacs.json`
or the brand files:

1. Bump `version` in `custom_components/worx_vision_cloud/manifest.json`.
2. Add a `## X.Y.Z - YYYY-MM-DD` section to `CHANGELOG.md`. The workflow uses the
   section that matches the manifest version as the release notes.
3. Push to `main`. The workflow tags the commit and publishes the GitHub
   release, which HACS offers as an update.

Every push of these files to `main` publishes the version that is in
`manifest.json`, so bump the version together with the changes it describes.

## Tag scheme

Tags are the plain version without a prefix, exactly as in `manifest.json`
(`1.5.1`). The first releases (`v0.1.0` to `v0.3.4`) were tagged by hand with a
`v` prefix; `0.3.3` onwards use the plain version. The old tags are kept so
that existing links and HACS history keep working. HACS orders updates by the
GitHub release, not by the tag name, so both forms are offered correctly. New
tags must use the plain version.

## Before publishing

Check that no private files or data were added:

```bash
git status --short
git grep -n "Bearer\|access_token\|refresh_token\|eyJ\|password\|secret\|latitude\|longitude"
```
