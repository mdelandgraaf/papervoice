# VoiceEngineer wake checklist

Every wake, before issue triage or repository exploration:

1. Run `scripts/wake-maintenance` unconditionally. It refreshes the boardroom `.env`
   token from the injected `PAPERCLIP_API_KEY`, probes the `papervoice.boardroom start`
   worker, and drains `PAPERCLIP_PENDING_POSTS_DIR` (default
   `/var/tmp/papervoice-boardroom/pending-posts`).
2. If the worker probe fails, report the concrete liveness failure to the issue/CEO;
   do not silently treat a stale token or dead worker as healthy.
3. Continue the scoped issue work, then leave a Paperclip comment before exiting.

The queue drain must happen after token refresh. Failed post-call summary payloads are
retained on disk and are only removed after a successful Paperclip response.
