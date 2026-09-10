# Action Button local mirror

[View the currently online mirror on GitHub Pages](https://ilariodeangelis77.github.io/actionbutton-net-archive/).

This workspace contains a resumable Wayback Machine crawler that recreates the
archived Action Button website as static files. Internal page and asset URLs are
rewritten to local paths. A second repair pass localizes third-party images and
restores image names that were changed by the archived site's word filter.

## Build or resume the mirror

```powershell
python .\mirror_wayback.py --output .\actionbutton-site --retry-failed `
  --capture-index .\actionbutton-site\post-captures-exact.json `
  --capture-index .\actionbutton-site\asset-captures-uploads.json `
  --capture-index .\actionbutton-site\asset-captures-content.json `
  --capture-index .\actionbutton-site\asset-captures-includes.json `
  --capture-index .\actionbutton-site\asset-captures-images.json
```

The finished site in this workspace is written to `actionbutton-site`. Crawl state is saved in
`actionbutton-site/.mirror-state.sqlite3`, so rerunning the command resumes
instead of downloading completed resources again.

After a rebuild, repair and audit all rendered image references with:

```powershell
python .\repair_images.py --output .\actionbutton-site
```

The repair is safe to rerun. Its detailed result is written to
`actionbutton-site/image-audit.json`.

Repair archived UTF-8/Windows-1252 text corruption with:

```powershell
python .\repair_text_encoding.py --output .\actionbutton-site
```

This pass is also idempotent and records every changed file in
`actionbutton-site/text-encoding-audit.json`.

Apply the offline consistency cleanup after crawling and image/text repair:

```powershell
python .\repair_consistency.py --output .\actionbutton-site
```

This removes obsolete social widgets, disables unavailable audio links, keeps
missing reviews on explicit local fallback pages, and synchronizes repaired
asset state. To resume recovering the optional randomized sidebar GIF set, add
`--recover-sidebar`; completed files are retained between runs and the live
slideshow automatically uses only locally available variants.

## Browse locally

```powershell
python .\serve_mirror.py
```

Then open <http://127.0.0.1:8000/>. The MGS4 review is available at
<http://127.0.0.1:8000/p/430/>.

`actionbutton-site/mirror-report.json` contains totals and
`actionbutton-site/missing-resources.csv` lists captures that could not be
retrieved.

The mirror contains no Wayback toolbar or replay URLs. The few remaining dead
references are source URLs for which Wayback has no retrievable payload; crawl
failures are in `missing-resources.csv`, and rendered-image exceptions are in
`image-audit.json`. The external Hoefler typography
stylesheet remains external because its archived response explicitly forbids
copying or redistribution.

## Back up and publish with GitHub

The repository includes a GitHub Pages workflow at
`.github/workflows/pages.yml`. On every push to `main`, it builds a deploy-only
copy of the mirror, adjusts root-relative links for the repository's Pages URL,
adds a clear archival/non-affiliation notice, and publishes the result. The
original local mirror is not modified.

To build the same project-path version locally:

```powershell
python .\build_github_pages.py --source .\actionbutton-site `
  --destination .\_site --base-path /YOUR_REPOSITORY_NAME
```

In the GitHub repository settings, set **Pages → Build and deployment →
Source** to **GitHub Actions**. Only publish material you have permission to
redistribute.
