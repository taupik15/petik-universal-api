#!/bin/sh
# Start the private BgUtils server and a loopback-only telemetry forwarder,
# then start the Petik API. None of these helper ports are publicly exposed.
set -u

APP_DIR="/app"
BGUTIL_DIR="/opt/bgutil-ytdlp-pot-provider/server"
BGUTIL_PORT="${BGUTIL_PROVIDER_PORT:-4416}"
BGUTIL_HOST="127.0.0.1"
BGUTIL_ENABLED="${BGUTIL_POT_PROVIDER_ENABLED:-1}"
TELEMETRY_PORT="${BGUTIL_TELEMETRY_PORT:-4417}"
BGUTIL_UPSTREAM="${BGUTIL_POT_PROVIDER_URL:-http://${BGUTIL_HOST}:${BGUTIL_PORT}}"

cd "$APP_DIR"
BGUTIL_READY=0

if [ "$BGUTIL_ENABLED" = "1" ] && command -v deno >/dev/null 2>&1 && [ -f "$BGUTIL_DIR/src/main.ts" ] && [ -d "$BGUTIL_DIR/node_modules" ]; then
  echo "[Petik] Starting private BgUtils PO Token provider on ${BGUTIL_HOST}:${BGUTIL_PORT}"
  (
    cd "$BGUTIL_DIR/node_modules" || exit 1
    exec deno run --allow-env --allow-net --allow-ffi=. --allow-read=. \
      ../src/main.ts --host "$BGUTIL_HOST" --port "$BGUTIL_PORT"
  ) >/tmp/petik-bgutil-provider.log 2>&1 &
  BGUTIL_PID=$!

  BGUTIL_TIMEOUT="${BGUTIL_PROVIDER_START_TIMEOUT:-25}"
  case "$BGUTIL_TIMEOUT" in
    ''|*[!0-9]*) BGUTIL_TIMEOUT=25 ;;
  esac
  i=0
  while [ "$i" -lt "$BGUTIL_TIMEOUT" ]; do
    if curl -fsS --connect-timeout 1 --max-time 2 \
      "http://${BGUTIL_HOST}:${BGUTIL_PORT}/ping" >/tmp/petik-bgutil-ping.json 2>/dev/null; then
      BGUTIL_READY=1
      break
    fi
    if ! kill -0 "$BGUTIL_PID" 2>/dev/null; then
      break
    fi
    i=$((i + 1))
    sleep 1
  done

  if [ "$BGUTIL_READY" = "1" ]; then
    echo "[Petik] BgUtils PO Token provider is ready"
  else
    echo "[Petik] WARNING: BgUtils provider did not become ready; non-YouTube extractors will still start"
    tail -n 30 /tmp/petik-bgutil-provider.log 2>/dev/null || true
  fi
else
  echo "[Petik] WARNING: BgUtils provider is disabled or its runtime files are missing"
fi

if [ "$BGUTIL_READY" = "1" ] && [ -f "$APP_DIR/pot_telemetry.py" ]; then
  echo "[Petik] Starting loopback-only BgUtils telemetry proxy on ${BGUTIL_HOST}:${TELEMETRY_PORT}"
  python "$APP_DIR/pot_telemetry.py" --host "$BGUTIL_HOST" --port "$TELEMETRY_PORT" --upstream "$BGUTIL_UPSTREAM" \
    >/tmp/petik-pot-telemetry.log 2>&1 &
  TELEMETRY_PID=$!
  TELEMETRY_TIMEOUT=8
  i=0
  while [ "$i" -lt "$TELEMETRY_TIMEOUT" ]; do
    if curl -fsS --connect-timeout 1 --max-time 2 \
      "http://${BGUTIL_HOST}:${TELEMETRY_PORT}/stats" >/tmp/petik-pot-telemetry-stats.json 2>/dev/null; then
      echo "[Petik] BgUtils telemetry proxy is ready; token requests will be counted without storing token data"
      break
    fi
    if ! kill -0 "$TELEMETRY_PID" 2>/dev/null; then
      echo "[Petik] WARNING: telemetry proxy did not start; API will connect to BgUtils directly"
      break
    fi
    i=$((i + 1))
    sleep 1
  done
fi

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-10000}"
