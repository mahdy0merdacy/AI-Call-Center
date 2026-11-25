// Runtime settings, loaded by index.html before the app bundle. This copy is a no-op
// for `npm run dev` and `npm run preview`, so the app uses its VITE_* variables. The
// container image replaces it: nginx serves a /config.js written at start-up from the
// environment (docker/40-runtime-config.sh), e.g.
//   window.__AICC_CONFIG__ = {"voiceTestUrl": "http://localhost:8082"};
