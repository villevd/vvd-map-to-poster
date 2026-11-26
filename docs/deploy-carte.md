# Publishing the 3D map at villevd.ca/carte

Same platform as villevd.ca/calendrier (vvd-calendrier): a Cloudflare Worker route on the `villevd.ca` zone, deployed with
`wrangler deploy` by a GitHub Actions workflow. Here the Worker has no code, only static assets (`wrangler.toml`).

## One-time setup
1. In this repository: Settings, Environments, create `production`, then add the secrets `CLOUDFLARE_API_TOKEN`
   (permission: Workers Scripts, edit; zone villevd.ca: Workers Routes, edit) and `CLOUDFLARE_ACCOUNT_ID`.
   Use the same values as vvd-calendrier.
2. Nothing else: the route `villevd.ca/carte*` is created by the first deploy.

## Each time the map changes
```powershell
python -m map3d.static_site "posters/<map>.db" site/carte          # add --no-satellite to skip the Esri pictures
Compress-Archive -Path site/carte/* -DestinationPath carte-bundle.zip -Force
gh release create carte-2026-12-01 carte-bundle.zip --title "Carte 2026-12-01" --notes "3D map bundle"
gh workflow run deploy-carte.yml -f release_tag=carte-2026-12-01
```

## Limits to know
- Cloudflare static assets: 25 MiB per file, 20,000 files (free plan; 100,000 paid). The biggest tile of the large Val-d'Or
  map is about 23 MiB, close to the limit. If the satellite tiles pass 20,000 files, export with `--no-satellite`.
- `site/_headers` makes the tiles cache for a year (their names never change for the same map version: use a new
  folder name or purge the cache if you replace a map).
- The repository is public, so the workflow runs on a GitHub-hosted runner, never the self-hosted Windows ones.
