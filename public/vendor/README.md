# vendor/

- `echarts-custom-5.6.0-r2.min.js` — the build index.html loads. Tree-shaken
  ECharts 5.6.0 (666 KB raw / 223 KB gzip vs 1,031 KB / 334 KB full): only the
  modules app.js uses (scatter/line/bar/map charts; title, tooltip, grid,
  visualMap, timeline, markArea, geo, axisPointer, dataZoom-inside components;
  LabelLayout; Canvas + SVG renderers), re-exported as the `echarts` global.
  r2 added SVGRenderer (SVG export button) and DataZoomInsideComponent (bubble
  pinch/wheel zoom) over r1 for +13.3 KB gzip.
Rollback: the full upstream `echarts-5.6.0.min.js` was removed (it was
unreferenced but still shipped ~1 MB on every deploy). To roll back, re-fetch it
from the ECharts 5.6.0 release
(`https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js`), point the
index.html script tag at it, and set its SRI to
`sha384-Mx5lkUEQPM1pOJCwFtUICyX45KNojXbkWdYhkKUKsbv391mavbfoAmONbzkgYPzR`.

## Upgrade / rebuild recipe

The build definition lives in `tmp/echarts-build/` (entry.js + build.md with
the full module census and verification steps). Short version:

```sh
cd tmp/echarts-build
npm install echarts@<version> esbuild
npx esbuild entry.js --bundle --minify --format=iife --global-name=echarts \
  --outfile=../../public/vendor/echarts-custom-<version>-<rev>.min.js
openssl dgst -sha384 -binary ../../public/vendor/echarts-custom-<version>-<rev>.min.js | openssl base64 -A
```

Update the script tag src + `integrity="sha384-<hash>"` in `public/index.html`
(keep defer/crossorigin/referrerpolicy), then `python3 -m pytest -q` — the
browser tests exercise every chart type, the guided arc, and PNG export. If a
new ECharts feature is used in app.js, add its module to entry.js first
(census greps are in tmp/echarts-build/build.md).
