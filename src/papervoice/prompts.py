"""Papervoice prompt configuration — loads from Paperclip plugin config at call start.

Seven configurable prompt levels:
  1. promptModerator     — system-level instructions for the standup moderator agent.
  2. promptParticipant   — system-level instructions for participant (non-moderator) agents.
  3. promptAgendaOpening — the moderator's opening turn. Use {next_speaker} where the first
                           update speaker's name should appear.
  4. promptStatusUpdate  — per-participant status update turn. Use {briefing} where the
                           agent's live Paperclip issue state should appear (empty when
                           offline). The handoff to the next speaker is always appended.
  5. promptReaction      — the brief cross-talk turn before each status update. Use
                           {prev_speaker} where the previous speaker's name should appear.
  6. promptClosing       — the moderator's closing turn at the end of the standup.
  7. promptDirectCall    — system-level instructions for an agent on a 1:1 direct call.
                           Use {agent_name} where the agent's display name should appear.

All seven fall back to built-in defaults when not configured.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default prompt strings — used when no override is configured in the plugin.
# Keep these in sync with the placeholder text shown in the UI (index.tsx).
# ---------------------------------------------------------------------------

DEFAULT_MODERATOR_INSTRUCTIONS = (
    "This is a daily standup, and you are the moderator."
    " You open the standup, keep it on time, and close it."
    " Be concise and conversational — one or two sentences per turn, no lists, no markdown."
    " This is a live multi-party voice call."
    " Report what Paperclip issues on your name you have done recently, what's still pending,"
    " what needs decisions from the board, and any blockers."
    " After your update, give the floor to another agent."
    " If you have a genuinely useful reaction — advice, a question — give it."
    " If not, call the pass_on_reacting tool and don't say anything else;"
    " don't force a comment just to fill air time."
    " If a human starts talking while you're mid-sentence, stop immediately."
    " If you need the board's steering or a decision before you can continue,"
    " ask the question out loud and then call the ask_board tool with that same question"
    " to wait for their answer — don't just guess or wait for the human to bring it up on their own."
)

DEFAULT_PARTICIPANT_INSTRUCTIONS = (
    "This is a live multi-party voice call daily standup, and you are a participant."
    " Your role is to update the moderator and board with the latest status of your recent"
    " Paperclip issues, and to ask questions if there are blockers or decisions that need to be taken."
    " Be concise and conversational — one or two sentences per issue, no lists, no markdown."
    " Report what Paperclip issues on your name you have done recently, what's still pending,"
    " what needs decisions from the board, and any blockers."
    " After your update, give the floor to another agent."
    " If you have a genuinely useful reaction — advice, a question — give it."
    " If not, call the pass_on_reacting tool and don't say anything else;"
    " don't force a comment just to fill air time."
    " If a human starts talking while you're mid-sentence, stop immediately."
    " If you need the board's steering or a decision before you can continue,"
    " ask the question out loud and then call the ask_board tool with that same question"
    " to wait for their answer — don't just guess or wait for the human to bring it up on their own."
)

# Use {next_speaker} where the first update speaker's name should appear.
# An {offline_notice} clause is appended automatically when Paperclip is offline.
DEFAULT_AGENDA_OPENING = (
    "Open the standup: greet everyone, introduce this as a Papervoice voice standup,"
    " and hand it to {next_speaker} for their update."
)

# Use {briefing} where the agent's live Paperclip issue state should appear.
# Expands to " Your current Paperclip status: {value}" when a briefing is available, or
# "" when offline. The handoff to the next speaker is always appended by the boardroom.
DEFAULT_STATUS_UPDATE = (
    "Give a brief status update based on your real Paperclip issue state below."
    "{briefing}"
    " If something needs a follow-up ticket, file it with the file_followup_issue tool."
)

# Use {prev_speaker} where the previous speaker's name should appear.
DEFAULT_REACTION = (
    "Before your own update: {prev_speaker} just gave theirs. If you have a"
    " genuinely useful reaction — advice, a question, encouragement — say one brief"
    " sentence. If not, call the pass_on_reacting tool and don't say anything else;"
    " don't force a comment just to fill air time."
)

DEFAULT_CLOSING = (
    "Close the standup: briefly recap any decisions or action items from this meeting"
    " that don't already have a follow-up ticket, and file each one now with the"
    " file_followup_issue tool before wrapping up — don't rely on whoever made the"
    " decision to have filed it themselves. Then ask if anyone has final questions"
    " before wrapping up, and thank everyone."
)

# Use {agent_name} where the agent's display name should appear.
# The agent's current open Paperclip issues are appended automatically when available.
DEFAULT_DIRECT_CALL_INSTRUCTIONS = (
    "You are {agent_name} on a one-on-one voice call with a board member."
    " Treat this like calling a colleague to discuss work — speak naturally and conversationally."
    " Keep your responses concise (one to three sentences) and leave space for the other person to reply."
    " You can discuss your work, answer questions about your issues, and file follow-up Paperclip"
    " issues with the file_followup_issue tool when something needs tracking."
)


@dataclass
class PromptConfig:
    moderator_instructions: str = field(default=DEFAULT_MODERATOR_INSTRUCTIONS)
    participant_instructions: str = field(default=DEFAULT_PARTICIPANT_INSTRUCTIONS)
    agenda_opening: str = field(default=DEFAULT_AGENDA_OPENING)
    status_update: str = field(default=DEFAULT_STATUS_UPDATE)
    reaction: str = field(default=DEFAULT_REACTION)
    closing: str = field(default=DEFAULT_CLOSING)
    direct_call_instructions: str = field(default=DEFAULT_DIRECT_CALL_INSTRUCTIONS)


def load_prompt_config() -> PromptConfig:
    """Load prompt overrides from the Paperclip plugin config, falling back to built-in defaults.

    Never raises — any fetch/parse failure logs a warning and returns defaults so the
    boardroom keeps working even when the Paperclip API is unreachable at call start.
    """
    try:
        from papervoice.vendors import paperclip as pc_vendor

        config = pc_vendor.get_plugin_config()
        return PromptConfig(
            moderator_instructions=config.get("promptModerator") or DEFAULT_MODERATOR_INSTRUCTIONS,
            participant_instructions=config.get("promptParticipant") or DEFAULT_PARTICIPANT_INSTRUCTIONS,
            agenda_opening=config.get("promptAgendaOpening") or DEFAULT_AGENDA_OPENING,
            status_update=config.get("promptStatusUpdate") or DEFAULT_STATUS_UPDATE,
            reaction=config.get("promptReaction") or DEFAULT_REACTION,
            closing=config.get("promptClosing") or DEFAULT_CLOSING,
            direct_call_instructions=config.get("promptDirectCall") or DEFAULT_DIRECT_CALL_INSTRUCTIONS,
        )
    except Exception:
        logger.warning("failed to load prompt config from Paperclip plugin config; using defaults")
        return PromptConfig()
