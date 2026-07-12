# Revenant AI — marketing landing page

Self-contained, single-file landing page (ember cyber-assassin theme). No build step —
`index.html` inlines all CSS/JS; the only external asset is `revenant-hero.jpg` (the hero figure).

## Live
- Production: https://revenantai-app.vercel.app/
- Vercel project: `revenantai` (org `himanshuthakur7s-projects`)

## Deploy
From this directory:

```bash
npx vercel deploy --prod --yes
# then point the friendly domain at the new deployment:
npx vercel alias set <new-deployment-url> revenantai-app.vercel.app
```

`vercel.json` overrides the project's default build (it's a static site, no `npm run build`).

## Hero image
`revenant-hero.jpg` is a web-optimized copy (2000×1121, ~289 KB) of the source art.
To swap it, drop a replacement at the same path and redeploy. Everything else — the fire/ember
canvas, light-streaks, grain, scanlines, scroll reveals — is generated in `index.html`.
