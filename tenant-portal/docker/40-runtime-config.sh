#!/bin/sh
# Writes /config.js from the environment at every start, so one image serves every
# deployment: index.html loads it before the bundle, which reads window.__AICC_CONFIG__.
# awk takes the value from ENVIRON (the shell never expands it) and JSON-encodes it,
# so whatever the variable holds stays a string.
set -eu

ME=$(basename "$0")
OUT_DIR=/tmp/aicc-web   # nginx serves /config.js from here (default.conf.template)

case "${VOICE_TEST_URL:-}" in
    '' | http://* | https://*) ;;
    *) echo "$ME: warning: VOICE_TEST_URL is not an http(s) URL, so the dashboard will not link to the voice test page" >&2 ;;
esac

mkdir -p "$OUT_DIR"
awk '
function json_string(s,    out, i, c) {
    out = ""
    for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\\" || c == "\"") out = out "\\" c
        else if (c in control) out = out sprintf("\\u%04x", control[c])
        else out = out c
    }
    return "\"" out "\""
}
BEGIN {
    for (i = 1; i < 32; i++) control[sprintf("%c", i)] = i
    printf "window.__AICC_CONFIG__ = {\"voiceTestUrl\": %s};\n", json_string(ENVIRON["VOICE_TEST_URL"])
}' > "$OUT_DIR/config.js"

if [ -z "${NGINX_ENTRYPOINT_QUIET_LOGS:-}" ]; then
    echo "$ME: wrote $OUT_DIR/config.js (VOICE_TEST_URL=${VOICE_TEST_URL:-})"
fi
