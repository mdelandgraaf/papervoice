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
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from livekit import rtc
from livekit.agents import Agent, AgentSession, WorkerOptions, cli, function_tool, llm
from livekit.agents.voice import room_io
from livekit.agents.voice.events import UserInputTranscribedEvent, UserStateChangedEvent
from livekit.plugins import anthropic, silero

from papervoice.moderator import AgendaItem, Moderator, SpeakerHandle
from papervoice.personas import BOARDROOM_ROOM, BOARDROOM_ROSTER, Persona
from papervoice.vendors import elevenlabs as el_vendor
from papervoice.vendors import livekit as lk_vendor
from papervoice.vendors import paperclip as pc_vendor

logger = logging.getLogger("papervoice.boardroom")

TRANSCRIBER_IDENTITY = "papervoice-moderator"
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


def _standup_agenda(
    roster: tuple[Persona, ...],
    context: dict[str, str] | None = None,
    paperclip_offline: bool = False,
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
    opener, *rest = roster
    offline_notice = (
        " Mention briefly that Paperclip board tools are offline for this call (access"
        " expired), so updates and follow-ups won't reflect live issue state."
        if paperclip_offline
        else ""
    )
    items = [
        AgendaItem(
            opener.identity,
            "Open the standup: greet everyone, say this is the Papervoice milestone-three"
            f" test call, and hand it to {rest[0].display_name} for their update.{offline_notice}",
        )
    ]
    for i, persona in enumerate(rest):
        if i > 0:
            prev = rest[i - 1]
            items.append(
                AgendaItem(
                    persona.identity,
                    f"Before your own update: {prev.display_name} just gave theirs. If you have a"
                    " genuinely useful reaction — advice, a question, encouragement — say one brief"
                    " sentence. If not, call the pass_on_reacting tool and don't say anything else;"
                    " don't force a comment just to fill air time.",
                    kind="reaction",
                )
            )
        nxt = rest[i + 1].display_name if i + 1 < len(rest) else None
        handoff = f" Then hand off to {nxt}." if nxt else f" Then hand back to {opener.display_name} to close."
        briefing = context.get(persona.identity)
        state = f" Your current Paperclip status: {briefing}" if briefing else ""
        items.append(
            AgendaItem(
                persona.identity,
                f"Give a brief status update based on your real Paperclip issue state below."
                f"{state} If something needs a follow-up ticket, file it with the"
                f" file_followup_issue tool.{handoff}",
            )
        )
    items.append(
        AgendaItem(
            opener.identity,
            "Close the standup: briefly recap any decisions or action items from this meeting"
            " that don't already have a follow-up ticket, and file each one now with the"
            " file_followup_issue tool before wrapping up — don't rely on whoever made the"
            " decision to have filed it themselves. Then ask if anyone has final questions"
            " before wrapping up, and thank everyone.",
        )
    )
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
    await room.connect(os.environ["LIVEKIT_URL"], token)
    try:
        session = AgentSession(
            llm=anthropic.LLM(model=AGENT_LLM_MODEL),
            tts=el_vendor.plugin_tts(voice_id_override=persona.voice_id),
        )
        await session.start(
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
            room_options=room_io.RoomOptions(audio_input=False, text_input=False),
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


async def _connect_transcriber(room_name: str, moderator: Moderator) -> tuple[AgentSession, rtc.Room]:
    token = lk_vendor.mint_join_token(TRANSCRIBER_IDENTITY, room_name, ttl_hours=1, agent=True)
    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    # ElevenLabs Scribe doesn't support streaming STT; VAD segments the human
    # audio into utterances so each one can be sent as a single batch call.
    session = AgentSession(stt=el_vendor.plugin_stt(), vad=silero.VAD.load())

    def on_transcribed(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final and ev.transcript.strip():
            moderator.record_transcript(ev.speaker_id or "human", ev.transcript.strip())

    def on_user_state_changed(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            logger.info("human speech detected, revoking floor from %s", moderator.current_speaker)
            _fire_and_forget(moderator.on_human_speech_started(), name="barge-in")

    session.on("user_input_transcribed", on_transcribed)
    session.on("user_state_changed", on_user_state_changed)

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


def _speaker_handle(identity: str, session: AgentSession, moderator: Moderator) -> SpeakerHandle:
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

    return SpeakerHandle(identity, speak, interrupt)


async def run_standup(room_name: str = BOARDROOM_ROOM, summary_issue_id: str | None = None) -> list[str]:
    """Connect every persona + the shared transcriber, then run the agenda. Returns completed identities.

    Milestone 3: loads each persona's live Paperclip context before building the agenda,
    and — if `summary_issue_id` is given — posts a post-call summary comment to it,
    including any follow-up issues filed live via the file_followup_issue tool.
    """
    context, paperclip_offline = await _load_context(BOARDROOM_ROSTER)
    moderator = Moderator(
        agenda=_standup_agenda(BOARDROOM_ROSTER, context, paperclip_offline=paperclip_offline), speakers={}
    )
    sessions: list[AgentSession] = []
    rooms: list[rtc.Room] = []
    completed: list[str] = []
    try:
        transcriber_session, transcriber_room = await _connect_transcriber(room_name, moderator)
        sessions.append(transcriber_session)
        rooms.append(transcriber_room)

        for persona in BOARDROOM_ROSTER:
            try:
                session, room = await _connect_agent(room_name, persona, moderator)
            except Exception:
                logger.exception("agent %s failed to join, continuing without it", persona.identity)
                moderator.dropped.add(persona.identity)
                continue
            sessions.append(session)
            rooms.append(room)
            moderator.add_speaker(persona.identity, _speaker_handle(persona.identity, session, moderator))

        completed = await moderator.run_agenda()
        return completed
    finally:
        for session in sessions:
            await session.aclose()
        for room in rooms:
            await room.disconnect()
        if summary_issue_id:
            try:
                await asyncio.to_thread(pc_vendor.post_comment, summary_issue_id, _build_summary(moderator, completed))
            except Exception:
                logger.exception("failed to post standup summary to %s", summary_issue_id)


async def entrypoint(ctx) -> None:
    await ctx.connect()
    # Agent playout doesn't complete until a real participant is in the room
    # to receive it (confirmed live: with an empty room every agent's
    # wait_for_playout() hangs to the moderator's 45s turn timeout and gets
    # dropped). Block on a human/SIP participant — never our own agent
    # participants, DEFAULT_PARTICIPANT_KINDS already excludes those — before
    # spending any agent turns that nobody is there to hear.
    logger.info("standup ready, waiting for a human to join room %s", ctx.room.name)
    participant = await ctx.wait_for_participant()
    logger.info("%s joined, starting the standup", participant.identity)
    # Optional (Milestone 3): where to post the post-call summary. Left unset,
    # the standup still runs and files issues live — it just doesn't post a
    # summary comment anywhere afterward.
    summary_issue_id = os.environ.get("PAPERCLIP_STANDUP_SUMMARY_ISSUE_ID")
    await run_standup(ctx.room.name, summary_issue_id=summary_issue_id)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
