"""Papervoice M2: multi-agent boardroom with moderator turn-taking.

One LiveKit room, N AI agent participants (each its own persona LLM +
distinct ElevenLabs voice, see personas.py) plus humans. A single connection
runs shared STT across all human tracks and feeds the moderator; the
moderator (moderator.py) grants each agent the floor in turn and revokes it
the instant a human starts talking (barge-in). Agent sessions carry no STT/VAD
of their own — they only speak when told to, per docs/ARCHITECTURE.md
"Moderator floor control, not VAD politeness."

If one agent's turn raises (session/participant dropped), the moderator logs
it, marks that agent unavailable, and keeps the agenda moving — the call
does not go dead.

Run (from repo root, with .venv active):
    PYTHONPATH=src python -m papervoice.boardroom start
Then have humans join with:
    scripts/join-link your-name papervoice-boardroom

Use `start` (registers for automatic dispatch on any new room), not the
one-shot `connect --room X` (dev/smoke-test convenience command): `connect`
pre-creates the room and dispatches a single job immediately, so if nobody
joins within LiveKit's empty-room timeout (~5 min) that job dies and no
retry happens even with a long-TTL join link still in hand. `start` leaves
the room uncreated until a human's own join creates it, so a link handed to
the board stays good for its full TTL regardless of when they click it. See
PER-79.
"""

import asyncio
import json
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

from livekit import rtc
from livekit.agents import Agent, AgentSession, WorkerOptions, cli, function_tool, llm
from livekit.agents.voice import room_io
from livekit.agents.voice.events import UserInputTranscribedEvent, UserStateChangedEvent
from livekit.plugins import anthropic, silero

from papervoice.moderator import AgendaItem, Moderator, SpeakerHandle
from papervoice.personas import (
    BOARDROOM_ROOM,
    CUSTOM_ROOM_PREFIX,
    DIRECT_ROOM_PREFIX,
    Persona,
    filter_roster,
    load_roster_from_paperclip,
    parse_custom_room_identities,
    persona_by_identity,
    resolve_named_preset,
)
from papervoice.prompts import PromptConfig, load_prompt_config
from papervoice.vendors import elevenlabs as el_vendor
from papervoice.vendors import livekit as lk_vendor
from papervoice.vendors import paperclip as pc_vendor

logger = logging.getLogger("papervoice.boardroom")

# The shared STT session's LiveKit participant. It is the room's silent "ears"
# (transcribes humans, drives barge-in), never a voice and never a
# decision-maker — so it must NOT be named "moderator". A caller reading the
# participant list would otherwise see a "papervoice-moderator" bot alongside
# the CEO persona and reasonably assume a second, redundant chair (PER-357).
# The turn-taking moderator is server-side code (moderator.py), not a
# participant; the CEO persona (roster_order 0) is the spoken chair.
TRANSCRIBER_IDENTITY = "papervoice-transcriber"
# LiveKit job-dispatch control connection (see request_fnc/entrypoint). Silent
# plumbing: it accepts the dispatch job and hosts the room, hosts no persona
# session, and never speaks or listens. Named so the participant list is
# self-explanatory rather than showing a bare "agent-<job.id>" (PER-85).
DISPATCH_IDENTITY = "papervoice-dispatch"
AGENT_LLM_MODEL = "claude-haiku-4-5"

# asyncio.create_task() only holds a *weak* reference to the task it returns;
# with nothing else referencing it, the task (and the barge-in it's carrying
# out) can be silently garbage-collected mid-run — see the "Important" note
# on create_task in the asyncio docs. Barge-in fires fire-and-forget from a
# sync event callback (on_user_state_changed below), so without this the
# interrupt can vanish before it ever reaches the agent, with no error logged.
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro, *, name: str) -> asyncio.Task:
    async def _run() -> None:
        try:
            await coro
        except Exception:
            logger.exception("background task %r failed", name)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _dismissal_target(text: str, roster: tuple[Persona, ...]) -> str | None:
    """Resolve only an explicit, named request for one agent to leave."""
    normalized = " ".join(text.lower().split())
    for persona in roster:
        names = {persona.display_name.lower(), persona.identity.lower().removeprefix("agent-")}
        for name in names:
            escaped = re.escape(name)
            direct = rf"\A(?:please )?{escaped}[, ]+(?:please )?(?:leave|exit|drop off|go now)(?:[.!?]|\Z)"
            indirect = rf"\A(?:ask|tell) {escaped} to (?:leave|exit|drop off)(?:[.!?]|\Z)"
            if re.search(direct, normalized) or re.search(indirect, normalized):
                return persona.identity
    return None


# Filler words a human might lead with before naming the agent they're
# addressing ("hey Eng...", "ok so Eng...") — stripped so the vocative still
# matches at the start of the utterance.
_ADDRESS_LEAD = r"(?:hey |hi |ok |okay |so |and |alright |right |um |uh |well )*"
# Explicit handoff / question cues that name the agent being addressed
# somewhere other than the very start ("over to Eng", "what about Eng?",
# "an update from Eng", "this one's for Eng").
_ADDRESS_CUE = (
    r"(?:ask|tell|over to|hand(?: it| this| it over)? to|hand off to|hear from|from|for|"
    r"what about|how about|what does|what do|question for|back to|to you)"
)


def _addressed_target(text: str, roster: tuple[Persona, ...]) -> str | None:
    """Resolve which agent, if any, a human utterance addresses by name.

    Used by the moderator (PER-293) to route a barge-in or open-floor question
    to the specific agent it was aimed at — "Eng, what's blocking you?" should
    be answered by Eng, not by whoever happened to hold the floor. Matches the
    agent's display name or bare identity (``agent-eng`` -> ``eng``) as a whole
    word (``\\bEng\\b`` never fires on "engineering") in one of three positions:
    a leading vocative ("Eng, ..." / "hey Eng ..."), after an explicit handoff
    or question cue ("over to Eng", "what about Eng", "an update from Eng"), or
    as a trailing vocative — the name is the last word of the utterance ("what
    do you think, Eng?", "go ahead Eng", "why don't you take this one Eng").
    Punctuation is optional since STT transcripts are often uncommaed and drop
    the trailing "?", which is exactly why the trailing case keys on final-word
    position rather than a question mark (PER-293: "go ahead Eng" used to miss).
    Returns None when no agent is named — the caller then keeps its default
    responder.
    """
    normalized = " ".join(text.lower().split())
    for persona in roster:
        names = {persona.display_name.lower(), persona.identity.lower().removeprefix("agent-")}
        for name in names:
            n = re.escape(name)
            leading = rf"\A{_ADDRESS_LEAD}{n}\b"
            cued = rf"\b{_ADDRESS_CUE} {n}\b"
            # Trailing vocative: the name is the final word (any trailing
            # punctuation/whitespace only), so a comma-less, question-mark-less
            # STT line like "go ahead eng" still resolves.
            trailing = rf"\b{n}[\s?!.,]*\Z"
            if re.search(leading, normalized) or re.search(cued, normalized) or re.search(trailing, normalized):
                return persona.identity
    return None


def _standup_agenda(
    roster: tuple[Persona, ...],
    context: dict[str, str] | None = None,
    paperclip_offline: bool = False,
    prompt_cfg: PromptConfig | None = None,
) -> list[AgendaItem]:
    """Build the agenda. `context` (Milestone 3) is identity -> a one-line live Paperclip
    briefing from _load_context(); folded into each status-update prompt so agents report
    real issue state instead of a generic "give an update", per docs/ARCHITECTURE.md M3.

    `paperclip_offline` (short-lived-token fallback, PER-76): set when every context fetch
    failed on a 401/403 (expired/invalid PAPERCLIP_API_KEY). The call still runs — the
    opener just says so out loud instead of everyone silently reporting nothing, so a human
    on the call knows to expect generic updates and no live issue filing this time.

    Agent-to-agent cross-talk (PER-83 board feedback): before every status update except
    the first, the persona about to speak first gets an optional "reaction" turn
    (`kind="reaction"`) to briefly comment on the *previous* speaker's update — bounded so it
    never becomes forced filler: the prompt tells it to call the pass_on_reacting tool and
    say nothing if it has nothing useful to add, and a turn that produces no spoken text is
    already a no-op for the moderator (see `Moderator._speak`). One reaction turn per
    status-update handoff (not "every remaining persona reacts to everything") keeps the
    added per-minute cost bounded — see docs/ARCHITECTURE.md M3 notes for the actual added
    call time this turned out to cost.
    """
    context = context or {}
    cfg = prompt_cfg or PromptConfig()
    opener, *rest = roster
    offline_notice = (
        " Mention briefly that Paperclip board tools are offline for this call (access"
        " expired), so updates and follow-ups won't reflect live issue state."
        if paperclip_offline
        else ""
    )
    if rest:
        opening_prompt = cfg.agenda_opening.format(next_speaker=rest[0].display_name) + offline_notice
    else:
        # Single-agent standup: opener is the only speaker; skip the handoff.
        opening_prompt = (
            "Open the standup: greet everyone, introduce this as a Papervoice voice standup,"
            " then give your own status update."
            + offline_notice
        )
    items = [
        AgendaItem(opener.identity, opening_prompt)
    ]
    for i, persona in enumerate(rest):
        if i > 0:
            prev = rest[i - 1]
            items.append(
                AgendaItem(
                    persona.identity,
                    cfg.reaction.replace("{prev_speaker}", prev.display_name),
                    kind="reaction",
                )
            )
        nxt = rest[i + 1].display_name if i + 1 < len(rest) else None
        handoff = f" Then hand off to {nxt}." if nxt else f" Then hand back to {opener.display_name} to close."
        briefing = context.get(persona.identity)
        briefing_text = f" Your current Paperclip status: {briefing}" if briefing else ""
        items.append(
            AgendaItem(
                persona.identity,
                cfg.status_update.replace("{briefing}", briefing_text) + handoff,
            )
        )
    items.append(AgendaItem(opener.identity, cfg.closing))
    return items


async def _load_context(roster: tuple[Persona, ...]) -> tuple[dict[str, str], bool]:
    """Fetch each persona's live Paperclip briefing at call start (Milestone 3).

    A failed fetch (API down, bad credentials) never blocks the call — it just
    leaves that persona speaking without live context, logged loudly so it's
    caught before the next call rather than silently every time.

    Returns `(context, offline)`. `offline` is True only when *every* persona's
    fetch failed on a 401/403 — i.e. PAPERCLIP_API_KEY itself is expired/invalid
    (the short-lived-token fallback, PER-76), not a one-off or partial failure —
    so the opener can say so out loud instead of the call quietly degrading with
    no one told (see `_standup_agenda`'s `paperclip_offline`).
    """
    context: dict[str, str] = {}
    auth_failures = 0
    for persona in roster:
        try:
            context[persona.identity] = await asyncio.to_thread(
                pc_vendor.context_briefing, persona.paperclip_agent_id
            )
        except Exception as exc:
            if pc_vendor.is_auth_error(exc):
                auth_failures += 1
                logger.error(
                    "Paperclip auth failed (401/403) loading context for %s — board tools"
                    " offline for this call, is PAPERCLIP_API_KEY expired?",
                    persona.identity,
                )
            else:
                logger.exception("failed to load Paperclip context for %s, continuing without it", persona.identity)
    offline = bool(roster) and auth_failures == len(roster)
    return context, offline


def _build_summary(moderator: Moderator, completed: list[str]) -> str:
    """Post-call summary markdown (Milestone 3): who spoke, and any issues filed live."""
    lines = ["## Papervoice standup summary", "", f"Completed turns: {', '.join(completed) or 'none'}."]
    if moderator.dropped:
        lines.append(f"Dropped mid-call: {', '.join(sorted(moderator.dropped))}.")
    if moderator.filed_issues:
        lines.append("")
        lines.append("Follow-up issues filed during the call:")
        lines.extend(f"- {identifier}: {title}" for identifier, title in moderator.filed_issues)
    lines.append("")
    lines.append("Full transcript:")
    lines.append("```")
    lines.append(moderator.full_transcript_text() or "(no transcript recorded)")
    lines.append("```")
    return "\n".join(lines)


def _file_issue_tool(persona: Persona, moderator: Moderator):
    """A per-persona LiveKit function tool (Milestone 3): lets the LLM file a real Paperclip
    issue mid-conversation when the board decides something needs tracking. Defaults the
    assignee to the speaking persona's own bound Paperclip agent, if it has one.
    """

    @function_tool
    async def file_followup_issue(title: str, description: str = "") -> str:
        """Create a Paperclip follow-up issue for a concrete action item the board just
        decided on. Do not use this for routine status updates — only for something that
        needs to be tracked and followed up on after the call.

        Args:
            title: Short issue title.
            description: Optional extra detail — what was decided and why.
        """
        try:
            issue = await asyncio.to_thread(
                pc_vendor.create_issue,
                title=title,
                description=description,
                assignee_agent_id=persona.paperclip_agent_id,
            )
        except Exception as exc:
            if pc_vendor.is_auth_error(exc):
                logger.error(
                    "Paperclip auth failed (401/403) filing %r from %s — board tools offline"
                    " for this call, is PAPERCLIP_API_KEY expired?",
                    title,
                    persona.identity,
                )
                return "Sorry, I can't file that right now — Paperclip access has expired for this call."
            logger.exception("failed to file follow-up issue %r from %s", title, persona.identity)
            return "Sorry, I couldn't file that issue — the Paperclip API call failed."
        identifier = issue.get("identifier") or "unknown"
        moderator.record_filed_issue(identifier, title)
        moderator.record_transcript("moderator", f"{persona.display_name} filed {identifier}: {title}")
        return f"Filed {identifier}: {title}"

    return file_followup_issue


@function_tool
async def _pass_on_reacting() -> str:
    """Call this instead of speaking when you have nothing genuinely useful to add during a
    reaction turn (a brief chance to comment on the previous speaker's update). Silence is
    fine — don't force a comment just to fill air time.
    """
    return "(passed, no comment)"


def _ask_board_tool(persona: Persona, moderator: Moderator):
    """A per-persona LiveKit function tool (PER-83): lets the LLM proactively pose a
    question to the humans on the call and wait for their answer before continuing its own
    turn, instead of steering only ever flowing human-in via barge-in. Backed by
    `Moderator._ask_and_wait`, which speaks nothing itself — the persona's own turn already
    said the question as normal spoken text; this tool just holds the floor open for the
    reply and hands it back so the LLM can react in the same turn.
    """

    @function_tool
    async def ask_board(question: str) -> str:
        """Ask the humans on this call a question and wait briefly for their answer — use
        this when you need the board's steering or a decision before you can continue, not
        for routine status updates. Say the question as part of your normal reply, then call
        this tool with the same question to actually wait for the answer.

        Args:
            question: The question you just asked, exactly as you said it.
        """
        answer = await moderator._ask_and_wait(persona.identity, question)
        if answer is None:
            return "No answer came back in time — say so briefly and move on."
        return f'The board answered: "{answer}"'

    return ask_board


async def _connect_agent(room_name: str, persona: Persona, moderator: Moderator) -> tuple[AgentSession, rtc.Room]:
    token = lk_vendor.mint_join_token(persona.identity, room_name, ttl_hours=1, agent=True)
    room = rtc.Room()
    await asyncio.wait_for(room.connect(os.environ["LIVEKIT_URL"], token), timeout=15.0)
    try:
        session = AgentSession(
            llm=anthropic.LLM(model=AGENT_LLM_MODEL),
            tts=el_vendor.plugin_tts(voice_id_override=persona.voice_id),
        )
        await asyncio.wait_for(
            session.start(
                # file_followup_issue (Milestone 3): lets this persona create a real Paperclip
                # issue when the board decides something needs tracking. pass_on_reacting and
                # ask_board (PER-83): let a reaction turn skip speaking, and let this persona
                # proactively ask the board a question and wait for the answer mid-turn.
                agent=Agent(
                    instructions=persona.instructions,
                    tools=[_file_issue_tool(persona, moderator), _pass_on_reacting, _ask_board_tool(persona, moderator)],
                ),
                room=room,
                # No STT/VAD: this agent never listens for itself. The shared
                # transcriber below is the room's only ears; the moderator feeds
                # each agent the rolling transcript as text when granting the floor.
                # close_on_disconnect=False (PER-84): LiveKit's default links this
                # session's lifecycle to the first human participant and tears the
                # whole AgentSession down the instant that participant disconnects
                # for any reason, including a momentary blip. With N personas each
                # independently linked to the same human, one blip closed all N
                # sessions in lockstep on a live board call (all three "dropped"
                # within milliseconds of each other) and there is no reconnect —
                # a closed AgentSession never reopens even if the human rejoins.
                # Moderator._speak already has its own per-turn try/except plus a
                # hard timeout (see docs/ARCHITECTURE.md M2 note 6/7) that
                # degrades one persona at a time and tolerates a hung/absent
                # human — that is the intended failure-isolation boundary, not
                # this SDK default, which defeats it by acting on all sessions at
                # once before the moderator ever gets a chance to isolate one.
                room_options=room_io.RoomOptions(audio_input=False, text_input=False, close_on_disconnect=False),
            ),
            timeout=20.0,
        )
    except Exception:
        # room.connect() above already put this identity in the room as a
        # visible participant; if session.start() then fails (bad voice id,
        # ElevenLabs quota, etc.) the caller only ever sees the exception and
        # never gets a `room` handle back to clean up with. Left alone that
        # participant just sits there connected with no session driving it —
        # exactly a silent "muted" agent nobody can ever grant the floor to
        # (see PER-75 board feedback). Disconnect it here before re-raising so
        # a failed join never leaves a ghost in the call.
        await room.disconnect()
        raise
    return session, room


async def _connect_transcriber(
    room_name: str, moderator: Moderator, roster: tuple[Persona, ...]
) -> tuple[AgentSession, rtc.Room]:
    token = lk_vendor.mint_join_token(TRANSCRIBER_IDENTITY, room_name, ttl_hours=1, agent=True)
    room = rtc.Room()
    await asyncio.wait_for(room.connect(os.environ["LIVEKIT_URL"], token), timeout=15.0)
    # ElevenLabs Scribe doesn't support streaming STT; VAD segments the human
    # audio into utterances so each one can be sent as a single batch call.
    session = AgentSession(stt=el_vendor.plugin_stt(), vad=silero.VAD.load())

    def on_transcribed(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final and ev.transcript.strip():
            text = ev.transcript.strip()
            # Log every final human utterance: without this the worker log has
            # no record of what was actually said, so a "naming didn't route"
            # report can't be debugged after the fact (PER-293).
            logger.info("human utterance (final): %r", text)
            target = _dismissal_target(text, roster)
            if target is not None:
                logger.info("explicit request for %s to leave", target)
                _fire_and_forget(moderator.dismiss_speaker(target), name=f"dismiss-{target}")
            moderator.record_transcript(ev.speaker_id or "human", ev.transcript.strip())

    def on_user_state_changed(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            logger.info("human speech detected, revoking floor from %s", moderator.current_speaker)
            _fire_and_forget(moderator.on_human_speech_started(), name="barge-in")
        elif ev.new_state == "listening":
            moderator.on_human_speech_stopped()

    session.on("user_input_transcribed", on_transcribed)
    session.on("user_state_changed", on_user_state_changed)

    human_kinds = {rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, rtc.ParticipantKind.PARTICIPANT_KIND_SIP}

    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        if participant.kind not in human_kinds:
            return
        humans_remaining = any(p.kind in human_kinds for p in room.remote_participants.values())
        if not humans_remaining:
            logger.info("last human left; ending call")
            moderator.end_call()

    room.on("participant_disconnected", on_participant_disconnected)

    await session.start(
        agent=Agent(instructions="You silently transcribe the room; you never speak."),
        room=room,
        room_options=room_io.RoomOptions(
            audio_input=True,
            text_input=False,
            audio_output=False,
            text_output=False,
            # Human tracks only (browser + phone dial-in) — never our own
            # agent participants. Subscribing to agent audio here would let
            # an agent's own TTS trigger barge-in against itself, exactly
            # the anti-pattern docs/ARCHITECTURE.md warns against.
            participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, rtc.ParticipantKind.PARTICIPANT_KIND_SIP],
            # close_on_disconnect=False (PER-84): same reasoning as
            # _connect_agent — this session must survive a human blip so it
            # picks the room's ears back up on reconnect instead of staying
            # permanently deaf for the rest of the call.
            close_on_disconnect=False,
        ),
    )
    return session, room


def _spoken_text(handle) -> str | None:
    """Extract what an agent actually said from a finished SpeechHandle, so the
    moderator's rolling transcript covers agent turns too, not just human
    ones. Needed so a barge-in continuation (`Moderator._run_turn`) knows
    what it already said and doesn't repeat itself, and so the post-call
    summary's transcript tail isn't human-only (see PER-76 board feedback).
    """
    texts = [
        item.text_content
        for item in handle.chat_items
        if isinstance(item, llm.ChatMessage) and item.role == "assistant" and item.text_content
    ]
    return "\n".join(texts) if texts else None


def _speaker_handle(
    identity: str, session: AgentSession, room: rtc.Room, moderator: Moderator
) -> SpeakerHandle:
    async def speak(prompt: str) -> str | None:
        context = moderator.recent_transcript_text()
        instructions = prompt if not context else f"Recent conversation:\n{context}\n\n{prompt}"
        handle = session.generate_reply(instructions=instructions, allow_interruptions=False)
        await handle.wait_for_playout()
        return _spoken_text(handle)

    async def interrupt() -> None:
        # force=True: agent turns run with allow_interruptions=False (they
        # only ever speak on the moderator's command, never react to their
        # own VAD) — but a human barge-in must still cut them off immediately.
        # Await the returned future: session.interrupt() is a plain (non-async)
        # method, so an un-awaited call still cancels current speech
        # synchronously, but awaiting is what surfaces a failure here instead
        # of discarding it silently.
        await session.interrupt(force=True)

    async def leave() -> None:
        await session.aclose()
        await room.disconnect()

    return SpeakerHandle(identity, speak, interrupt, leave)



def _direct_call_instructions(
    persona: Persona,
    briefing: str | None = None,
    prompt_cfg: PromptConfig | None = None,
) -> str:
    """Instructions for a 1:1 direct call — natural conversation, not standup script."""
    cfg = prompt_cfg or PromptConfig()
    context = f"\n\nYour current open Paperclip issues: {briefing}" if briefing else ""
    base = cfg.direct_call_instructions.replace("{agent_name}", persona.display_name)
    return f"{base}{context}"


def _direct_file_issue_tool(persona: Persona, filed_issues: list):
    """A per-persona LiveKit function tool for 1:1 direct calls (no Moderator dependency)."""

    @function_tool
    async def file_followup_issue(title: str, description: str = "") -> str:
        """Create a Paperclip follow-up issue for a concrete action item from this call.

        Args:
            title: Short issue title.
            description: Optional extra detail — what was decided and why.
        """
        try:
            issue = await asyncio.to_thread(
                pc_vendor.create_issue,
                title=title,
                description=description,
                assignee_agent_id=persona.paperclip_agent_id,
            )
        except Exception as exc:
            if pc_vendor.is_auth_error(exc):
                return "Sorry, I can't file that right now — Paperclip access has expired for this call."
            logger.exception("failed to file follow-up issue %r from %s (direct call)", title, persona.identity)
            return "Sorry, I couldn't file that issue — the Paperclip API call failed."
        identifier = issue.get("identifier") or "unknown"
        filed_issues.append((identifier, title))
        return f"Filed {identifier}: {title}"

    return file_followup_issue


def _resolve_direct_persona(livekit_identity: str) -> Persona | None:
    """Find a Persona by livekit_identity for a direct 1:1 call.

    Checks Paperclip live roster first (agents with metadata.papervoice.enabled),
    then falls back to the static BOARDROOM_ROSTER. The returned Persona's
    `instructions` field is intentionally empty — run_direct_call builds fresh
    instructions via _direct_call_instructions so the agent doesn't receive
    standup-specific language.
    """
    try:
        for cfg in pc_vendor.get_voice_enabled_agents():
            if cfg.livekit_identity == livekit_identity:
                return Persona(
                    identity=cfg.livekit_identity,
                    display_name=cfg.display_name,
                    voice_id=cfg.voice_id,
                    instructions="",
                    paperclip_agent_id=cfg.agent_id,
                )
    except Exception:
        logger.warning(
            "failed to load Paperclip roster for direct call %s; trying static fallback",
            livekit_identity,
        )
    return persona_by_identity(livekit_identity)


async def run_direct_call(
    room_name: str,
    persona: Persona,
    summary_issue_id: str | None = None,
) -> None:
    """Run a 1:1 direct call between one human and one agent persona.

    Unlike the standup, there is no moderator and no agenda: the agent listens
    directly via STT/VAD and responds naturally, like a colleague taking a call.
    The file_followup_issue tool is available so actions raised on the call can
    be filed directly to Paperclip.

    When `summary_issue_id` is given, posts a brief summary (any filed issues)
    after the human leaves. If no issues were filed the summary is skipped —
    there is nothing worth recording beyond the conversation itself.
    """
    prompt_cfg = await asyncio.to_thread(load_prompt_config)
    briefing: str | None = None
    try:
        briefing = await asyncio.to_thread(pc_vendor.context_briefing, persona.paperclip_agent_id)
    except Exception:
        logger.exception("failed to load Paperclip context for direct call with %s", persona.identity)

    instructions = _direct_call_instructions(persona, briefing, prompt_cfg)
    filed_issues: list[tuple[str, str]] = []

    token = lk_vendor.mint_join_token(persona.identity, room_name, ttl_hours=1, agent=True)
    room = rtc.Room()
    await asyncio.wait_for(room.connect(os.environ["LIVEKIT_URL"], token), timeout=15.0)

    done = asyncio.Event()
    human_kinds = {rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, rtc.ParticipantKind.PARTICIPANT_KIND_SIP}
    # Detect worker restart: human was already in the room before we joined.
    # On a fresh call the human joins after the agent starts; on a restart
    # the human has been there continuously. The greeting differs.
    human_already_present = any(p.kind in human_kinds for p in room.remote_participants.values())

    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        if participant.kind not in human_kinds:
            return
        humans_remaining = any(p.kind in human_kinds for p in room.remote_participants.values())
        if not humans_remaining:
            logger.info("last human left direct call with %s", persona.identity)
            done.set()

    room.on("participant_disconnected", on_participant_disconnected)

    try:
        session = AgentSession(
            stt=el_vendor.plugin_stt(),
            llm=anthropic.LLM(model=AGENT_LLM_MODEL),
            tts=el_vendor.plugin_tts(voice_id_override=persona.voice_id),
            vad=silero.VAD.load(),
        )
        await session.start(
            agent=Agent(
                instructions=instructions,
                tools=[_direct_file_issue_tool(persona, filed_issues)],
            ),
            room=room,
            # close_on_disconnect=False: stay alive across human blips; done.set()
            # handles the explicit "last human left" teardown path instead.
            room_options=room_io.RoomOptions(close_on_disconnect=False),
        )
        # Wait for RoomIO's _init_task to complete before greeting: it finds the
        # human participant, subscribes to their audio input, and publishes the
        # agent's audio output track. Without this, session.start() returns while
        # _init_task is still running in the background, and generate_reply() can
        # race against audio track publication — on a worker restart into an
        # existing room the window is wide enough that the greeting is silently
        # lost (PER-349).
        try:
            await asyncio.wait_for(session.room_io.wait_for_ready(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning(
                "direct call: room I/O not ready within 15s for %s — no participant "
                "detected; skipping greeting but staying live",
                persona.identity,
            )
        else:
            # Also wait for the browser to actually subscribe to the agent's audio
            # output track before greeting. wait_for_ready() resolves when the track
            # is published to the SFU; WebRTC subscription from the browser follows
            # ~0.5–5s later. Audio sent before that window closes is silently dropped.
            # asyncio.wait() is used (not wait_for) so the SDK-owned future is never
            # cancelled on timeout. (PER-349)
            subscribed = session.room_io.subscribed_fut
            if subscribed is not None and not subscribed.done():
                done_futs, _ = await asyncio.wait({subscribed}, timeout=10.0)
                if not done_futs:
                    logger.warning(
                        "direct call: no audio track subscriber within 10s for %s; "
                        "proceeding with greeting anyway",
                        persona.identity,
                    )
            if human_already_present:
                greeting_instructions = (
                    "Briefly acknowledge that you had a brief connection interruption "
                    "and that you're back. One sentence; do not re-introduce yourself."
                )
            else:
                greeting_instructions = (
                    f"Greet the person who just joined and introduce yourself briefly as {persona.display_name}."
                )
            await session.generate_reply(instructions=greeting_instructions)
        try:
            await asyncio.wait_for(done.wait(), timeout=3600.0)
        except asyncio.TimeoutError:
            logger.warning(
                "direct call with %s idle for 1 hour; closing session",
                persona.identity,
            )
    finally:
        await session.aclose()
        await room.disconnect()
        if summary_issue_id and filed_issues:
            lines = [f"## Direct call — {persona.display_name}", "", "Issues filed during the call:"]
            lines.extend(f"- {identifier}: {title}" for identifier, title in filed_issues)
            try:
                await asyncio.to_thread(
                    pc_vendor.post_comment_or_queue,
                    summary_issue_id,
                    "\n".join(lines),
                )
            except Exception:
                logger.exception("failed to post or queue direct call summary to %s", summary_issue_id)


async def run_standup(
    room_name: str = BOARDROOM_ROOM,
    summary_issue_id: str | None = None,
    roster: tuple[Persona, ...] | None = None,
) -> list[str]:
    """Connect every persona + the shared transcriber, then run the agenda. Returns completed identities.

    Milestone 3: loads each persona's live Paperclip context before building the agenda,
    and — if `summary_issue_id` is given — posts a post-call summary comment to it,
    including any follow-up issues filed live via the file_followup_issue tool.

    PER-89: `roster` is built dynamically from Paperclip agents with
    metadata.papervoice.enabled when not supplied (calls load_roster_from_paperclip(),
    which falls back to the static BOARDROOM_ROSTER if the API is unreachable).
    Pass an explicit roster in tests or when the caller has already loaded it.
    """
    prompt_cfg = await asyncio.to_thread(load_prompt_config)
    if roster is None:
        roster = await asyncio.to_thread(load_roster_from_paperclip, prompt_cfg)
    context, paperclip_offline = await _load_context(roster)
    moderator = Moderator(
        agenda=_standup_agenda(roster, context, paperclip_offline=paperclip_offline, prompt_cfg=prompt_cfg),
        speakers={},
        # Route a barge-in / open-floor question that names a specific agent to
        # that agent, instead of always to whoever held the floor (PER-293).
        addressee_resolver=lambda text: _addressed_target(text, roster),
    )
    sessions: list[AgentSession] = []
    rooms: list[rtc.Room] = []
    completed: list[str] = []
    try:
        try:
            transcriber_session, transcriber_room = await _connect_transcriber(room_name, moderator, roster)
            sessions.append(transcriber_session)
            rooms.append(transcriber_room)
        except Exception:
            logger.exception("transcriber failed to join; call will proceed without STT/barge-in")

        for persona in roster:
            try:
                session, room = await _connect_agent(room_name, persona, moderator)
            except Exception:
                logger.exception("agent %s failed to join, continuing without it", persona.identity)
                moderator.dropped.add(persona.identity)
                continue
            sessions.append(session)
            rooms.append(room)
            moderator.add_speaker(persona.identity, _speaker_handle(persona.identity, session, room, moderator))
            logger.info("agent %s joined the standup as %r", persona.identity, persona.display_name)

        completed = await moderator.run_agenda()
        return completed
    finally:
        # Always dump the full transcript to the worker log, even when no
        # summary issue is configured — otherwise a call's conversation is
        # unrecoverable once the process exits and "debug the last call" is
        # impossible (PER-293).
        logger.info(
            "standup ended; completed=%s; full transcript:\n%s",
            completed,
            moderator.full_transcript_text() or "(no transcript recorded)",
        )
        async def _teardown() -> None:
            for session in sessions:
                await session.aclose()
            for room in rooms:
                await room.disconnect()
        try:
            await asyncio.wait_for(_teardown(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("standup teardown timed out after 10s; connections may not have closed cleanly")
        if summary_issue_id:
            try:
                await asyncio.to_thread(
                    pc_vendor.post_comment_or_queue,
                    summary_issue_id,
                    _build_summary(moderator, completed),
                )
            except Exception:
                logger.exception("failed to post or queue standup summary to %s", summary_issue_id)


async def request_fnc(job_request) -> None:
    # Without an explicit identity, the SDK's job-dispatch connection joins as
    # "agent-<job.id>" — a silent 5th participant with no self-explanatory
    # name (PER-85). Give it one so anyone reading the participant list during
    # a call knows what it is.
    await job_request.accept(identity=DISPATCH_IDENTITY)


async def entrypoint(ctx) -> None:
    await ctx.connect()

    # The dispatch connection (DISPATCH_IDENTITY) has no session host and
    # therefore no lk.agent.session or lk.transcription stream handlers. Any
    # such stream sent by a room participant (e.g. the human's browser sending
    # remote-session control, or the persona broadcasting its transcription)
    # would otherwise log "ignoring … no callback attached" at INFO. Register
    # no-ops so those messages are cleanly absorbed on the dispatch side without
    # interfering with the handlers the persona's own session registers on its
    # separate room connection (PER-349).
    async def _discard_stream(reader, _identity) -> None:
        async with reader:
            pass

    try:
        ctx.room.register_byte_stream_handler("lk.agent.session", _discard_stream)
    except ValueError:
        pass
    try:
        ctx.room.register_text_stream_handler("lk.transcription", _discard_stream)
    except ValueError:
        pass

    # Block on a human/SIP participant before spending any LLM/TTS budget —
    # agent playout doesn't complete until a real listener is in the room.
    logger.info("worker ready, waiting for a human to join room %s", ctx.room.name)
    participant = await ctx.wait_for_participant()
    logger.info("%s joined room %s", participant.identity, ctx.room.name)

    if ctx.room.name.startswith(DIRECT_ROOM_PREFIX):
        # Direct 1:1 call: room name encodes the target agent identity.
        livekit_identity = ctx.room.name[len(DIRECT_ROOM_PREFIX):]
        persona = await asyncio.to_thread(_resolve_direct_persona, livekit_identity)
        if persona is None:
            logger.error(
                "no voice-enabled persona found for direct room %s (identity: %s); closing",
                ctx.room.name,
                livekit_identity,
            )
            return
        logger.info("starting direct call with %s in room %s", livekit_identity, ctx.room.name)
        summary_issue_id = os.environ.get("PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID")
        await run_direct_call(ctx.room.name, persona, summary_issue_id=summary_issue_id)
    elif ctx.room.name.startswith(CUSTOM_ROOM_PREFIX):
        identities = parse_custom_room_identities(ctx.room.name)
        live_roster = await asyncio.to_thread(load_roster_from_paperclip)
        roster = filter_roster(live_roster, identities or ())
        if not roster:
            logger.error("custom room %s has no enabled matching agents; closing", ctx.room.name)
            return
        logger.info("starting custom room %s with agents %s", ctx.room.name, [p.identity for p in roster])
        summary_issue_id = os.environ.get("PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID")
        await run_standup(ctx.room.name, summary_issue_id=summary_issue_id, roster=roster)
    elif ctx.room.name.startswith("papervoice-preset-"):
        live_roster = await asyncio.to_thread(load_roster_from_paperclip)
        roster: tuple[Persona, ...] | None = None

        # Primary path: agent IDs are encoded in the LiveKit room metadata, which
        # the plugin worker sets when minting the human join link (before the human
        # arrives). The boardroom agent can read room.metadata without board access.
        raw_meta = getattr(ctx.room, "metadata", None)
        if raw_meta:
            try:
                meta = json.loads(raw_meta)
                agent_ids = meta.get("agentIds")
                if isinstance(agent_ids, list) and agent_ids:
                    wanted = set(agent_ids)
                    roster = tuple(p for p in live_roster if p.paperclip_agent_id in wanted)
                    logger.info(
                        "resolved preset %s from room metadata: %s",
                        ctx.room.name,
                        [p.identity for p in roster],
                    )
                    unmatched = wanted - {p.paperclip_agent_id for p in roster if p.paperclip_agent_id}
                    if unmatched:
                        logger.warning(
                            "preset %s: %d agent(s) in metadata not found in live roster "
                            "(not papervoice-enabled or removed): %s",
                            ctx.room.name,
                            len(unmatched),
                            sorted(unmatched),
                        )
            except Exception:
                logger.warning("could not parse room metadata for preset %s", ctx.room.name, exc_info=True)

        # Fallback path: read the preset from the plugin config. The plugin-config
        # endpoint requires board access and returns 403 for agent tokens; after the
        # get_plugin_config() fix this degrades to an empty dict (no presets) rather
        # than a crash, so the "no valid agents" guard below handles it cleanly.
        if not roster:
            try:
                roster = resolve_named_preset(
                    ctx.room.name,
                    await asyncio.to_thread(pc_vendor.get_plugin_config),
                    live_roster,
                )
            except Exception:
                logger.exception("preset %s: failed to resolve from plugin config; closing", ctx.room.name)
                return

        if not roster:
            logger.error(
                "preset %s: no valid enabled agents found (room metadata absent or empty; "
                "plugin config returned no match). Join the room via a freshly minted link "
                "from the Papervoice settings page to ensure room metadata is populated.",
                ctx.room.name,
            )
            return
        await run_standup(ctx.room.name, summary_issue_id=os.environ.get("PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID"), roster=roster)
    else:
        # Full boardroom standup.
        summary_issue_id = os.environ.get("PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID")
        await run_standup(ctx.room.name, summary_issue_id=summary_issue_id)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, request_fnc=request_fnc))
