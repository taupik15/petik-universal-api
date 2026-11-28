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

# --- Optional Cloudflare WARP pool for YouTube (YOUTUBE_WARP_ENABLED=1) ---
# Render Free shares datacenter IPs that YouTube answers with HTTP 429. This
# starts WARP_POOL_SIZE userspace WireGuard tunnels (each its own free WARP
# identity) exposed as SOCKS5 on 127.0.0.1. The API rotates between them and
# retries on another tunnel when YouTube blocks one, instead of freezing all
# YouTube traffic for minutes. Any failure falls back to the direct Render IP.
WARP_BASE_PORT="${WARP_SOCKS_PORT:-40000}"
WARP_POOL_SIZE="${WARP_POOL_SIZE:-2}"
WARP_ROOT=/tmp/warp
WARP_POOL_FILE="$WARP_ROOT/pool.txt"

warp_start_tunnel() {
  # $1 = slot number (0-based). Prints nothing; returns 0 when the tunnel is verified.
  slot="$1"
  port=$((WARP_BASE_PORT + slot))
  dir="$WARP_ROOT/$slot"
  mkdir -p "$dir" && cd "$dir" || return 1
  if [ "$slot" = "0" ] && [ -n "${WGCF_ACCOUNT_TOML_B64:-}" ]; then
    printf '%s' "$WGCF_ACCOUNT_TOML_B64" | base64 -d > wgcf-account.toml 2>/dev/null || rm -f wgcf-account.toml
  fi
  n=0
  while [ "$n" -lt 4 ] && [ ! -s wgcf-account.toml ]; do
    wgcf register --accept-tos >>"/tmp/petik-wgcf-$slot.log" 2>&1 || sleep $((3 + n * 2))
    n=$((n + 1))
  done
  [ -s wgcf-account.toml ] || return 1
  wgcf generate >>"/tmp/petik-wgcf-$slot.log" 2>&1 || return 1
  [ -s wgcf-profile.conf ] || return 1
  {
    cat wgcf-profile.conf
    printf '\n[Socks5]\nBindAddress = 127.0.0.1:%s\n' "$port"
  } > wireproxy.conf
  # Cap Go heap: Render Free has only 512 MB shared with Python, deno and ffmpeg.
  GOMEMLIMIT="${WARP_GOMEMLIMIT:-24MiB}" GOGC=50 wireproxy -c wireproxy.conf >"/tmp/petik-wireproxy-$slot.log" 2>&1 &
  i=0
  while [ "$i" -lt 15 ]; do
    trace="$(curl -fsS --max-time 6 --socks5-hostname "127.0.0.1:${port}" https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null || true)"
    if printf '%s' "$trace" | grep -Eq '^warp=(on|plus)'; then
      echo "[Petik] WARP tunnel $slot ready on port $port (egress $(printf '%s' "$trace" | sed -n 's/^ip=//p' | head -n 1))"
      echo "socks5h://127.0.0.1:${port}" >> "$WARP_POOL_FILE"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  return 1
}

if [ "${YOUTUBE_WARP_ENABLED:-0}" = "1" ] && [ -z "${YOUTUBE_PROXY:-}" ] \
   && command -v wgcf >/dev/null 2>&1 && command -v wireproxy >/dev/null 2>&1; then
  echo "[Petik] Preparing Cloudflare WARP pool (${WARP_POOL_SIZE} tunnels) for YouTube"
  rm -rf "$WARP_ROOT"; mkdir -p "$WARP_ROOT"; : > "$WARP_POOL_FILE"
  export YOUTUBE_PROXY_POOL_FILE="$WARP_POOL_FILE"
  # First tunnel is synchronous so the API starts with at least one route.
  if ( warp_start_tunnel 0 ); then
    export YOUTUBE_WARP_ACTIVE=1
  else
    echo "[Petik] WARNING: first WARP tunnel failed; YouTube will use the direct Render IP"
    tail -n 10 /tmp/petik-wgcf-0.log /tmp/petik-wireproxy-0.log 2>/dev/null || true
  fi
  # Extra tunnels start in the background and join the pool when verified.
  (
    s=1
    while [ "$s" -lt "$WARP_POOL_SIZE" ]; do
      sleep 4
      ( warp_start_tunnel "$s" ) || echo "[Petik] WARNING: WARP tunnel $s failed (pool continues without it)"
      s=$((s + 1))
    done
  ) &
  cd "$APP_DIR" || true
fi

if [ "$BGUTIL_ENABLED" = "1" ] && command -v deno >/dev/null 2>&1 && [ -f "$BGUTIL_DIR/src/main.ts" ] && [ -d "$BGUTIL_DIR/node_modules" ]; then
  echo "[Petik] Starting private BgUtils PO Token provider on ${BGUTIL_HOST}:${BGUTIL_PORT}"
  (
    cd "$BGUTIL_DIR/node_modules" || exit 1
    exec deno run --v8-flags="--max-old-space-size=${BGUTIL_MAX_HEAP_MB:-256}" --allow-env --allow-net --allow-ffi=. --allow-read=. \
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

export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-10000}"
