#!/bin/bash
cd "$(dirname "$0")/../agent" || exit 1
PORT="${AGENT_PORT:-8123}"
npx @langchain/langgraph-cli dev --port "$PORT" --no-browser
