# PER-314 — Persistent custom room presets

Plan revision: 1  
Date: 2026-08-18  
Status: awaiting board confirmation  
Supersedes: the shipped transient custom-room interpretation in commit `ec07e86`

## Clarified outcome

Papervoice administrators can save reusable, company-scoped room presets such as **Marketing** or **Sales**. Each preset has a durable name and a selected set of enabled Papervoice agents. The preset remains in the dashboard room list across reloads and sessions. Clicking **Join room** mints a fresh human join link; when the call starts, only the preset's currently valid selected agents join.

This is a saved configuration object, not a permanently running LiveKit room. LiveKit rooms remain ephemeral call instances, while the preset and its membership persist in Paperclip.

## Scope and product behavior

### Room list

- The dashboard's Rooms tab shows the fixed Boardroom plus all saved custom presets.
- Each saved row displays its human-readable name, selected agent names, and **Join room**.
- **Create room** opens a form for a required name and one or more enabled Papervoice agents.
- A saved preset can be edited or deleted. Deletion requires explicit confirmation and removes only the preset, not agents or historical call data.
- Names are trimmed, case-insensitively unique within the company, and bounded to a documented reasonable length (proposal: 1–80 characters).
- Presets remain visible after page reload and from another authenticated dashboard session.

### Call behavior

- Joining mints a fresh 48-hour human token for a deterministic, opaque room key derived from the preset's stable ID; display-name changes therefore do not change the technical room identity.
- The worker resolves preset membership from persisted configuration at call start and filters it against the live enabled Papervoice roster.
- Only selected agents that still exist, remain enabled, and have valid voice configuration join the call.
- If some selected agents are stale or disabled, the call starts with the valid remainder and the dashboard surfaces which selections need repair.
- If no valid selected agents remain, joining is disabled with an actionable validation message; an empty agent room is not started.
- Moderator behavior follows the existing roster rule: a selected configured moderator leads; otherwise the first valid selected agent is the opener.
- Concurrent calls to the same preset use the same existing LiveKit room semantics. Creating a durable preset does not pre-create or keep a LiveKit room alive.

### Compatibility

- The fixed Boardroom and direct-agent call links continue to work unchanged.
- Existing transient `papervoice-room-<identities>` links from commit `ec07e86` remain accepted for their TTL during rollout, but the UI stops creating them after presets ship.
- No secret values are stored in presets; only stable preset metadata and Paperclip agent IDs are persisted.

## Data contract

Store presets in the existing company-scoped Papervoice plugin configuration under a versioned field:

```json
{
  "roomPresetsVersion": 1,
  "roomPresets": [
    {
      "id": "stable-opaque-id",
      "name": "Marketing",
      "agentIds": ["paperclip-agent-id-1", "paperclip-agent-id-2"]
    }
  ]
}
```

Use Paperclip agent IDs as durable references, not mutable LiveKit identities. Preserve all unrelated plugin configuration fields on every write. The worker converts agent IDs to the current enabled roster when a call starts. The LiveKit room key should carry only the opaque preset ID (for example `papervoice-preset-<id>`), avoiding agent membership or user-entered names in tokens/logs.

## Implementation plan

1. **Persistence and validation contract**
   - Add typed parsing/serialization for versioned `roomPresets` in plugin configuration.
   - Validate stable ID, normalized unique name, non-empty deduplicated agent IDs, maximum preset count, and bounded input sizes.
   - Make updates preserve unrelated settings and reject malformed or duplicate records atomically.
   - Define stale-agent projection so reads can return both valid roster members and repair warnings.

2. **Plugin actions and dashboard experience**
   - Add company-scoped list/create/update/delete actions with clear authorization and validation failures.
   - Replace the transient checkbox-only Rooms section with the persistent list and create/edit form.
   - Keep Boardroom as a fixed non-editable row; show empty, loading, error, save, and delete-confirmation states.
   - Mint join links from the stable preset room key and disable join when membership resolves to zero valid agents.

3. **Worker preset resolution and backward compatibility**
   - Recognize the new preset room prefix and resolve its opaque ID from Papervoice config at job start.
   - Filter saved Paperclip agent IDs against the current enabled voice roster before constructing the moderated call.
   - Fail closed for unknown/deleted preset IDs so arbitrary room names cannot select agents.
   - Retain parsing of old identity-encoded transient links for the compatibility window; document a later cleanup point.

4. **Verification and documentation**
   - Unit-test config validation, uniqueness, setting preservation, stale/disabled agents, deletion, and unknown preset IDs.
   - UI/action tests cover create → reload → edit → join → delete and visible repair states.
   - Worker tests prove exact roster selection, moderator fallback, zero-valid-agent refusal, and legacy-link compatibility.
   - Build the plugin, run focused Python tests plus the relevant full suite, and manually smoke-test two presets (Marketing and Sales) through dashboard reload and link minting.
   - Update operator/user documentation to distinguish durable presets from ephemeral LiveKit call instances.

## Acceptance criteria

- An administrator can create a named preset with selected agents and see it in the Rooms list after reload.
- Marketing and Sales presets can coexist with different rosters and case-insensitively unique names.
- Clicking a preset's **Join room** opens a call where exactly its currently valid selected agents are present.
- Editing the preset changes the roster used by the next call without changing the preset's stable identity.
- Deleting the preset removes it from the list and prevents future joins for that preset ID.
- Disabled/deleted agents never join; partial stale membership is visible and recoverable through edit.
- A preset with zero valid agents cannot be joined.
- Boardroom, direct-agent links, and already-issued transient custom-room links continue working during rollout.
- Persistence writes retain LiveKit references, prompts, default room, and every unrelated plugin setting.
- Automated tests and plugin build pass, and a manual two-preset smoke test is recorded.

## Risks and controls

- **Lost configuration from concurrent full-object writes:** centralize read-modify-write handling and test preservation; if the platform offers optimistic concurrency, use it.
- **Mutable identity/name drift:** persist Paperclip agent IDs and resolve current voice identities only at call start.
- **Deleted preset link reuse:** worker must require the preset ID to exist at dispatch, rather than trusting the room key.
- **User-entered names leaking into infrastructure identifiers:** use opaque IDs in LiveKit room names.
- **Partial invalid roster:** surface repair state; allow valid members to proceed but refuse a wholly invalid roster.

## Execution graph after approval

| Planned issue | Owner | Initial state | Hard blockers |
|---|---|---|---|
| A. Implement persistence contract, plugin actions, worker resolution, compatibility, tests, and docs as one cohesive change | VoiceEngineer — best specialty match for Papervoice/LiveKit and plugin integration | Ready: can start immediately after plan approval; repository and existing transient flow are available | Plan confirmation only |
| B. Board acceptance smoke test: create Marketing and Sales presets, reload, join each, edit one, delete one | Board user | Blocked | A |

The code change stays as one implementation issue because the persistence, UI contract, and worker dispatch semantics must agree atomically and are small enough for one specialist. The board acceptance check is the only dependent branch. No implementation child issues will be created until this revision is accepted.

## Out of scope

- Recurring meeting schedules, invitations, calendars, access-control lists, room ownership roles, call-history retention, analytics, and permanently running LiveKit rooms.
- Per-preset prompts or settings beyond agent membership.
- Automatic department-based membership; presets use an explicit saved selection.
