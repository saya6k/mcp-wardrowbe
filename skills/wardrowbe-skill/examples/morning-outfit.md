# Example — Morning outfit suggestion

```
User: What should I wear today? I have an office meeting at 10.

Claude (calls suggest_outfit occasion="office" time_of_day="morning"):
  → backend returns a suggestion with composite image + 4 items

Claude: I'd go with the navy blazer over the white tee and chinos. Brown
        loafers tie it together. Want me to confirm it?

User: Yeah, accept it.

Claude (calls accept_latest_outfit):
  → 200 OK

Claude: Locked in.
```

## Notes for the model

- Map free-form occasion phrasing (`office meeting`, `client lunch`,
  `interview`) to the closed vocabulary the tool accepts. When unsure,
  ask. Don't invent unsupported occasion strings — the tool rejects them.
- Default `time_of_day` from the user's actual time (morning if before
  ~12:00 local) rather than asking.
- After suggesting, ask whether to accept/reject/skip rather than acting
  unilaterally — the user may want to see the visual first.
