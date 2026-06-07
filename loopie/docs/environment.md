# Environment Notes

Expected environment variables:

```bash
OPENAI_API_KEY=
WANDB_API_KEY=
WANDB_ENTITY=
WEAVE_PROJECT=loopie
LOOPIE_LLM_MODE=mock
LOOPIE_WEAVE_ENABLED=true   # Weave traces/evals in mock mode (hosted judging)
REDIS_URL=redis://localhost:6379
LOOPIE_API_BASE=http://localhost:8001
AGENT_URL=http://localhost:8123
```

Use local Redis first. Add Redis Cloud only if needed.

Copy `loopie/.env.example` to `loopie/.env` and paste secrets there. Never commit `.env`.

