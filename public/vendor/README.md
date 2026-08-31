# Browser chart bundle

`index.html` loads `echarts-custom-6.1.0-r1.min.js`. The build has the chart,
component, feature, and renderer modules that `app.js` uses. It exports the
modules through the `echarts` browser global.

The build has scatter, line, bar, and map charts. It also has the title,
tooltip, grid, visual map, timeline, mark area, geo, axis pointer, and inside
data zoom components. It has label layout and the Canvas and SVG renderers.

The focused bundle is 707,051 bytes raw and 240,750 bytes compressed with gzip.
The full ECharts 6.1.0 browser bundle is 1,121,883 bytes raw and 369,270 bytes
compressed with gzip.

## Rebuild and upgrade

The committed build definition lives in `vendor/echarts-build/`. Its package
lock pins ECharts 6.1.0 and esbuild 0.28.2.

```bash
cd vendor/echarts-build
npm ci
npm audit --audit-level=moderate
npm run build
openssl dgst -sha384 -binary ../../public/vendor/echarts-custom-6.1.0-r1.min.js \
  | openssl base64 -A
```

Copy the new SHA-384 value into the script integrity attribute in
`public/index.html`. Keep the `defer`, `crossorigin`, and `referrerpolicy`
attributes. Run the complete browser suite because it covers every chart type,
the guided story, zoom, and PNG and SVG exports. Add any new ECharts module to
the committed entry before the application uses it.
