// Pure validators for the boardroom pcp_ token (PER-408).
// No React or DOM imports — kept plain TypeScript so the test can exercise it
// with esbuild's `ts` loader without a JSX pass. See boardroom-key.test.mjs.

// Paperclip agent tokens are pcp_ + a base64/url-ish body. The exact length
// varies by minting host, so we allow ≥20 body chars; the server does the
// authoritative check when we POST the secret value.
export const BOARDROOM_KEY_PATTERN = /^pcp_[A-Za-z0-9._-]{20,}$/;
export const BOARDROOM_SECRET_KEY = "papervoice.boardroom_api_key";
export const BOARDROOM_SECRET_NAME = "Papervoice boardroom API key";

export type BoardroomKeyValidation = { ok: true } | { ok: false; error: string };

export function validateBoardroomKey(raw: string): BoardroomKeyValidation {
  const value = raw.trim();
  if (!value) return { ok: false, error: "Paste the pcp_ value returned by the token command." };
  if (!value.startsWith("pcp_")) return { ok: false, error: "Value must start with pcp_." };
  if (!BOARDROOM_KEY_PATTERN.test(value)) {
    return { ok: false, error: "That does not look like a Paperclip agent token (pcp_ + ≥20 chars)." };
  }
  return { ok: true };
}
