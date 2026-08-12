"""Unit tests for the pure agenda-building logic in boardroom.py.

Room/session wiring (_connect_agent, _connect_transcriber) needs a live
LiveKit room and is exercised by the manual smoke test instead, per
docs/ARCHITECTURE.md's "Definition of done" for call-flow changes.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.boardroom import _standup_agenda
from papervoice.personas import BOARDROOM_ROSTER


class StandupAgendaTest(unittest.TestCase):
    def test_every_persona_gets_at_least_one_turn(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        identities = {item.identity for item in agenda}
        self.assertEqual(identities, {p.identity for p in BOARDROOM_ROSTER})

    def test_opener_speaks_twice_everyone_else_once(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        identities = [item.identity for item in agenda]
        opener, *rest = BOARDROOM_ROSTER
        self.assertEqual(identities.count(opener.identity), 2)
        for persona in rest:
            self.assertEqual(identities.count(persona.identity), 1, persona.identity)

    def test_opener_also_closes(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        self.assertEqual(agenda[0].identity, BOARDROOM_ROSTER[0].identity)
        self.assertEqual(agenda[-1].identity, BOARDROOM_ROSTER[0].identity)
        self.assertIn("close", agenda[-1].prompt.lower())

    def test_middle_items_are_the_non_opener_personas_in_order(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        middle_identities = [item.identity for item in agenda[1:-1]]
        expected = [p.identity for p in BOARDROOM_ROSTER[1:]]
        self.assertEqual(middle_identities, expected)


if __name__ == "__main__":
    unittest.main()
