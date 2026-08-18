#!/usr/bin/env bash
set -euo pipefail
sudo apt update
sudo apt install -y docker.io docker-compose-plugin unzip
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true
echo "Docker installed. Log out/in once if docker requires sudo, then run ./scripts/deploy.sh"
