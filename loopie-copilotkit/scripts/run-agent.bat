@echo off
cd /d "%~dp0..\agent"
if not defined AGENT_PORT set AGENT_PORT=8123
npx @langchain/langgraph-cli dev --port %AGENT_PORT% --no-browser
