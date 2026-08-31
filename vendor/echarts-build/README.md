# Rebuild the custom ECharts bundle

This directory is the complete source for the browser bundle in `public/vendor/`.
Both ECharts and esbuild use exact versions in `package-lock.json`.

```bash
cd vendor/echarts-build
npm ci
npm run build
openssl dgst -sha384 -binary ../../public/vendor/echarts-custom-6.1.0-r1.min.js \
  | openssl base64 -A
```

Copy the resulting SHA-384 value into the `integrity` attribute in
`public/index.html`. Then run the complete browser suite. It exercises bubble,
line, bar, map, panel, guided-story, zoom, and PNG-export paths.
