#!/usr/bin/env bash
# Uses the same Firedrake development image family as upstream Thetis CI.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v docker >/dev/null 2>&1; then
  echo 'Docker is not installed. Install Docker, or use an existing Firedrake main environment; see THETIS.md.' >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Docker daemon is not running. Start it and retry.' >&2
  exit 2
fi
if [[ ! -f "$project_dir/external/thetis/pyproject.toml" ]]; then
  echo 'Clone the Thetis repository into external/thetis first.' >&2
  exit 2
fi
# Override with an image digest once a compatible run has been validated.
image_name="${SEDIMENTCFD_FIREDRAKE_IMAGE:-firedrakeproject/firedrake-vanilla-default:dev-main}"
docker run --rm \
  --mount "type=bind,source=$project_dir,target=/work" \
  --workdir /work "$image_name" \
  bash -lc 'python3 -m venv --system-site-packages /tmp/sedimentcfd-venv && source /tmp/sedimentcfd-venv/bin/activate && python -m pip install -e /work/external/thetis -e /work && python -m sedimentcfd thetis "$@"' -- "$@"
