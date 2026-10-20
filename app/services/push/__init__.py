"""Push notifications to the GDG Qassim iPhone app, sent straight to Apple's APNs.

- ``outbox``: who a push can reach, and writing outbox rows (``push_notifications``).
- ``triggers``: what writes them - acceptance, attendance points, the sweep's reminders and new events.
- ``delivery``: claiming due rows and sending them; ``apns``: the HTTP/2 client and the provider token.
- ``messages``: the Arabic and English text; ``links``: the app links a push may open.

See docs/PUSH.md.
"""
