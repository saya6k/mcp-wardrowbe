# Example — Wardrobe audit / stats drill-down

```
User: How's my wardrobe doing?

Claude (calls get_wardrobe_summary):
  → {total_items: 187, total_outfits: 124, acceptance_rate: 0.72,
     average_rating: 4.1, total_wears: 412, ...}

Claude: 187 items, 124 outfits suggested, 72% acceptance, average rating
        4.1. You've logged 412 wears total. Want to see what you wear the
        most?

User: Yeah.

Claude (calls get_most_worn_items limit=5):
  → top 5 items with wear counts

Claude: Your navy tee is way ahead at 38 wears. Grey crewneck is second
        at 22, then those black chinos at 19.

User: How many haven't been worn in months?

Claude: I can't query "not worn since" directly — wardrowbe doesn't expose
        that filter. I can list everything in a category and check the
        last_worn_at on each, but that's a lot of calls. Want me to walk
        a specific category, like outerwear?
```

## Notes for the model

- `get_wardrobe_summary` is a single backend call — cheap. Lead with it
  on broad "how's my wardrobe" questions.
- `get_most_worn_items` is analytics-derived; the wear counts are
  precomputed on the backend.
- For "haven't worn in X days/months", there is no dedicated tool.
  `list_items` returns `last_worn_at` per item but you'd be filtering
  client-side over a paginated list. Tell the user about the cost before
  doing it.
- Don't fabricate stats the tools didn't return. If the user asks for
  "average wears per item per month" — that's not a backend field. Say so.
