"""Push notifications to the GDG Qassim iPhone app, sent straight to Apple's APNs.

- ``outbox``: who a push can reach, and writing outbox rows (``push_notifications``).
- ``triggers``: what writes them - acceptance, attendance points, the sweep's reminders and new events.
- ``cases``: the app 1.0.3 kinds; ``rank``: entering the top 5, reaching #1 and leaving the top 5 (sweep).
- ``delivery``: claiming due rows and sending them; ``apns``: the HTTP/2 client and the provider token.
- ``messages``: the Arabic and English text; ``links``: the app links a push may open.

See docs/PUSH.md.
"""
