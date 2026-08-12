"""Papervoice M1: one ElevenLabs-voiced agent in a LiveKit room with one human.

Run a worker that joins rooms on dispatch:
    python -m papervoice.agent dev        # local dev, connects to LIVEKIT_URL
Or join one specific room directly:
    python -m papervoice.agent connect --room papervoice-m1

M2 (multi-agent + moderator floor control) builds on this file; keep it minimal.
"""

from dotenv import load_dotenv

load_dotenv()

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import anthropic, silero

from papervoice.vendors import elevenlabs as el_vendor

INSTRUCTIONS = """You are Papervoice, the first voiced agent of the Paperclip AI company.
You are on a live audio call with a board member. Be concise and conversational —
one or two sentences per turn, no lists, no markdown. This is the Milestone 1 test
call: confirm you can hear them, answer their questions, and keep the exchange natural."""


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    session = AgentSession(
        stt=el_vendor.plugin_stt(),
        llm=anthropic.LLM(model="claude-haiku-4-5"),
        tts=el_vendor.plugin_tts(),
        vad=silero.VAD.load(),
    )
    await session.start(agent=Agent(instructions=INSTRUCTIONS), room=ctx.room)
    await session.generate_reply(
        instructions="Greet the caller, say this is the Papervoice milestone-one test call, and ask if they can hear you clearly."
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
