# loopie-demo-polish

Polish Loopie for demos, presentations, and public sharing.

## Checklist

### README

- Opening paragraph must answer: what is Loopie, what problem does it solve, why does it work.
- Include a one-command quickstart that actually runs end-to-end.
- Add a screenshot or terminal recording that shows a real eval run with Weave comparison.
- Remove any placeholder text, TODO comments, or internal notes.

### Pitch script

- Lead with the pain: LLM outputs are non-deterministic and hard to trust.
- Demo the invariant live: show baseline failing, Redis correction applied, rerun passing, Weave diff.
- Keep the live demo under 3 minutes. Have a fallback recording.

### Social demo recording

- Use a clean terminal with large font, minimum 18pt, and light or high-contrast theme.
- Record at 1920x1080. Show real data, not mocked responses.
- Add captions or on-screen annotations if sharing without audio.

### Before any demo

- Run the full eval suite cold on a fresh clone or clean env.
- Confirm Weave traces are publicly viewable or have a shareable link ready.
- Verify no secrets, internal URLs, or personal data appear on screen.

