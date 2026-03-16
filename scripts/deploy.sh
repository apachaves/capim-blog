#!/bin/bash
# Deploy script for Manus — rebuilds and redeploys to Surge.sh
# Run this after any content update to push changes live

set -e
cd "$(dirname "$0")/.."

echo "Building..."
pnpm build

echo "Deploying to https://capim-blog.surge.sh ..."
SURGE_TOKEN=65451230948a4b8617ed41f0eea1a458 surge ./dist/public capim-blog.surge.sh

echo "Done! Live at https://capim-blog.surge.sh"
