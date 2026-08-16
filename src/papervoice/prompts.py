"""Papervoice prompt configuration — loads from Paperclip plugin config at call start.

Three configurable prompt levels:
  1. promptModerator  — system-level instructions for the standup moderator agent.
  2. promptParticipant — system-level instructions for participant (non-moderator) agents.
  3. promptAgendaOpening — the moderator's opening turn instructions (replaces the
     instance-specific "say this is the milestone-X test call" text). Use {next_speaker}
     where the first update speaker's name should appear.

All three fall back to built-in defaults when not configured.
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


@dataclass
class PromptConfig:
    moderator_instructions: str = field(default=DEFAULT_MODERATOR_INSTRUCTIONS)
    participant_instructions: str = field(default=DEFAULT_PARTICIPANT_INSTRUCTIONS)
    agenda_opening: str = field(default=DEFAULT_AGENDA_OPENING)


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
        )
    except Exception:
        logger.warning("failed to load prompt config from Paperclip plugin config; using defaults")
        return PromptConfig()
