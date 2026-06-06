# Environment Notes

Expected future environment variables:

```bash
OPENAI_API_KEY=
WANDB_API_KEY=
WANDB_ENTITY=
WEAVE_PROJECT=loopie
REDIS_URL=redis://localhost:6379
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

Use local Redis first. Add Redis Cloud only if needed.

Copy `loopie/.env.example` to `loopie/.env` and paste secrets there. Never commit `.env`.

