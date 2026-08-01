#!/usr/bin/env bash
# Render build script — Al-Nathim SaaS Platform
set -o errexit

# Install the `ping` binary (iputils-ping) — required by the network_tools
# ping feature on Render's minimal Linux containers. Render builds run as root.
apt-get update -y && apt-get install -y --no-install-recommends iputils-ping || true

pip install -r requirements.txt