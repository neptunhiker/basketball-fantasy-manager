#!/usr/bin/env bash
# Downloads the Tailwind standalone CLI (no Node required) and builds the stylesheet.
#
#   ./scripts/tailwind.sh build    one-off, minified — used by the Dockerfile
#   ./scripts/tailwind.sh watch    rebuild on change while developing
set -euo pipefail

TAILWIND_VERSION="${TAILWIND_VERSION:-v4.1.18}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$ROOT/.bin"
BIN="$BIN_DIR/tailwindcss"
INPUT="$ROOT/assets/input.css"
OUTPUT="$ROOT/static/css/app.css"

detect_target() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os" in
    Darwin) os="macos" ;;
    Linux)  os="linux" ;;
    *) echo "Unsupported OS: $os" >&2; exit 1 ;;
  esac
  case "$arch" in
    arm64|aarch64) arch="arm64" ;;
    x86_64|amd64)  arch="x64" ;;
    *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
  esac
  echo "tailwindcss-${os}-${arch}"
}

ensure_binary() {
  if [ -x "$BIN" ]; then
    return
  fi
  local target url
  target="$(detect_target)"
  url="https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/${target}"
  echo "Downloading Tailwind ${TAILWIND_VERSION} (${target})..."
  mkdir -p "$BIN_DIR"
  curl -sSfL "$url" -o "$BIN"
  chmod +x "$BIN"
}

ensure_binary
mkdir -p "$(dirname "$OUTPUT")"

case "${1:-build}" in
  build) exec "$BIN" -i "$INPUT" -o "$OUTPUT" --minify ;;
  watch) exec "$BIN" -i "$INPUT" -o "$OUTPUT" --watch ;;
  *) echo "Usage: $0 [build|watch]" >&2; exit 1 ;;
esac
