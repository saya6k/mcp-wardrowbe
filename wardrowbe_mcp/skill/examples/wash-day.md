# Example — Wash day workflow

```
User: What needs washing?

Claude (calls get_items_to_wash limit=10):
  → 6 items returned, each with image + wears_since_wash

Claude: 6 things in the pile. The grey hoodie's been worn 9 times since
        its last wash, and the black tee's at 7. Want to mark anything
        as done?

User: I washed the hoodie and both tees.

Claude (calls log_wash for each item_id, then list_items needs_wash=true):
  → 3 calls, then re-fetch shows 3 left

Claude: Done. 3 items still in the pile.
```

## Notes for the model

- Use `get_items_to_wash` first — it returns the same shape items as the
  user sees in the wardrowbe UI, with the `wears_since_wash` counter
  already calculated.
- When the user names items vaguely ("the hoodie", "my favourite jeans"),
  match against the returned list before calling `log_wash`. If multiple
  candidates match, ask which.
- Batch operations: just call `log_wash` once per `item_id`. Don't bother
  re-listing between calls unless the user wants the new count.
