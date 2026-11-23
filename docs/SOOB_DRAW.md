# SOOB mascot prize draw

The Website campaign is at `/soob`. Deploy Backend before Website. Backend's deploy workflow applies Alembic revision `91bbc2a2a139` before restarting the API. Staging uses its own database and Clerk instance. A staging entry does not carry over to production.

## Export the entrants

A database operator can run this read-only query against the production GDG scores database when the draw closes. Export the result as CSV for the draw. It returns each active entrant once, ordered by original entry time in UTC. Share member IDs for the draw and use the email field to contact the selected winner.

```sql
SELECT m.id AS member_id, m.name, m.email, e.entered_at AS entered_at_utc
FROM soob_draw_entries AS e
JOIN members AS m ON m.id = e.member_id
WHERE m.deleted_at IS NULL
ORDER BY e.entered_at, e.id;
```

If the team sets a closing time, apply it in UTC with `AND e.entered_at < 'YYYY-MM-DD HH:MM:SS'` before the `ORDER BY`. The game has no automatic closing time. Once the campaign is over, remove the Website route and the Backend entry endpoints while keeping the table for the draw.

## Adjust the placement

Edit `HITBOX` in `app/routers/soob_draw.py`. The Website positions the monochrome mascot from that same rectangle, with coordinates measured as fractions from the top-left of the scene. The backend validates the submitted coordinates against the rectangle. Players can inspect the artwork position in their browser, so this is a casual game with member-level deduplication.
