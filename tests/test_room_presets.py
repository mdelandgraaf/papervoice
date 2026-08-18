import sys
sys.path.insert(0,'src')
import unittest
from dataclasses import dataclass
from papervoice.room_presets import (
    validate_presets, project_preset, read_presets, preset_room_name,
    PRESET_ROOM_PREFIX,
)
from papervoice.personas import resolve_named_preset, Persona


def _persona(agent_id: str, identity: str = "") -> Persona:
    return Persona(
        identity=identity or f"agent-{agent_id}",
        display_name=agent_id,
        voice_id="v1",
        instructions="",
        paperclip_agent_id=agent_id,
    )


class ValidationAndProjectionTest(unittest.TestCase):
    def test_normalize_name_strips_and_collapses_whitespace(self):
        x = validate_presets([{'id': 'marketing-123', 'name': '  Marketing  ', 'agentIds': ['a', 'a', 'b']}])
        assert x[0]['name'] == 'Marketing' and x[0]['agentIds'] == ['a', 'b']

    def test_stale_agents_reported_separately(self):
        x = validate_presets([{'id': 'marketing-123', 'name': 'Marketing', 'agentIds': ['a', 'b']}])
        proj = project_preset(x[0], ['a'])
        assert proj.stale_agent_ids == ('b',)
        assert proj.valid_agent_ids == ('a',)

    def test_all_agents_stale_when_none_enabled(self):
        x = validate_presets([{'id': 'mktg-abcdef12', 'name': 'Empty', 'agentIds': ['a', 'b']}])
        proj = project_preset(x[0], [])
        assert proj.valid_agent_ids == ()
        assert proj.stale_agent_ids == ('a', 'b')

    def test_duplicate_casefold_names_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_presets([
                {'id': 'marketing-123', 'name': 'Sales', 'agentIds': ['a']},
                {'id': 'sales-1234', 'name': ' sales ', 'agentIds': ['b']},
            ])

    def test_missing_agent_ids_rejected(self):
        with self.assertRaises(ValueError):
            validate_presets([{'id': 'marketing-123', 'name': 'X', 'agentIds': []}])

    def test_invalid_preset_id_rejected(self):
        with self.assertRaises(ValueError):
            validate_presets([{'id': 'bad id!', 'name': 'X', 'agentIds': ['a']}])


class SettingPreservationTest(unittest.TestCase):
    def test_read_presets_ignores_other_config_keys(self):
        config = {
            'liveKitUrl': 'wss://example.com',
            'roomPresetsVersion': 1,
            'roomPresets': [{'id': 'preset-abcdef12', 'name': 'Eng', 'agentIds': ['a']}],
            'promptModerator': 'custom prompt',
        }
        presets = read_presets(config)
        assert len(presets) == 1
        assert presets[0]['name'] == 'Eng'

    def test_missing_presets_key_returns_empty_list(self):
        assert read_presets({'liveKitUrl': 'wss://x'}) == []

    def test_unsupported_version_raises(self):
        with self.assertRaises(ValueError):
            read_presets({'roomPresetsVersion': 99, 'roomPresets': []})


class PresetRoomNameTest(unittest.TestCase):
    def test_valid_id_produces_prefixed_name(self):
        assert preset_room_name('abcdefgh') == PRESET_ROOM_PREFIX + 'abcdefgh'

    def test_invalid_id_raises(self):
        with self.assertRaises(ValueError):
            preset_room_name('bad id!')

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            preset_room_name('abc')


class ResolveNamedPresetTest(unittest.TestCase):
    def _make_config(self, presets):
        return {'roomPresetsVersion': 1, 'roomPresets': presets}

    def test_exact_roster_selection_keeps_only_matching_agents(self):
        config = self._make_config([
            {'id': 'preset-eng-1234', 'name': 'Engineering', 'agentIds': ['agent-a', 'agent-b']},
        ])
        roster = (_persona('agent-a'), _persona('agent-b'), _persona('agent-c'))
        result = resolve_named_preset(PRESET_ROOM_PREFIX + 'preset-eng-1234', config, roster)
        assert result is not None
        ids = tuple(p.paperclip_agent_id for p in result)
        assert set(ids) == {'agent-a', 'agent-b'}
        assert 'agent-c' not in ids

    def test_unknown_preset_id_returns_empty_tuple(self):
        config = self._make_config([
            {'id': 'preset-eng-1234', 'name': 'Engineering', 'agentIds': ['agent-a']},
        ])
        roster = (_persona('agent-a'),)
        result = resolve_named_preset(PRESET_ROOM_PREFIX + 'no-such-preset', config, roster)
        assert result == ()

    def test_zero_valid_agents_returns_empty_tuple(self):
        config = self._make_config([
            {'id': 'preset-eng-1234', 'name': 'Engineering', 'agentIds': ['agent-disabled']},
        ])
        roster = (_persona('agent-other'),)
        result = resolve_named_preset(PRESET_ROOM_PREFIX + 'preset-eng-1234', config, roster)
        assert result == ()

    def test_stale_agents_filtered_valid_returned(self):
        config = self._make_config([
            {'id': 'preset-mixed-12', 'name': 'Mixed', 'agentIds': ['agent-a', 'agent-gone']},
        ])
        # agent-gone is no longer in the roster (disabled/deleted)
        roster = (_persona('agent-a'),)
        result = resolve_named_preset(PRESET_ROOM_PREFIX + 'preset-mixed-12', config, roster)
        assert result is not None and len(result) == 1
        assert result[0].paperclip_agent_id == 'agent-a'

    def test_roster_order_preserved_not_preset_order(self):
        config = self._make_config([
            {'id': 'preset-order-12', 'name': 'Order', 'agentIds': ['agent-b', 'agent-a']},
        ])
        # roster has agent-a before agent-b; preset lists b first — result follows roster order
        persona_a = _persona('agent-a', 'agent-a')
        persona_b = _persona('agent-b', 'agent-b')
        roster = (persona_a, persona_b)
        result = resolve_named_preset(PRESET_ROOM_PREFIX + 'preset-order-12', config, roster)
        assert result is not None
        assert result[0].paperclip_agent_id == 'agent-a'
        assert result[1].paperclip_agent_id == 'agent-b'

    def test_non_preset_room_returns_none(self):
        config = self._make_config([])
        roster = (_persona('agent-a'),)
        result = resolve_named_preset('papervoice-boardroom', config, roster)
        assert result is None

    def test_moderator_first_in_roster_becomes_opener(self):
        """When preset includes the moderator-first persona, they remain opener."""
        config = self._make_config([
            {'id': 'preset-full-12', 'name': 'Full', 'agentIds': ['mod-agent', 'par-agent']},
        ])
        mod = _persona('mod-agent', 'agent-moderator')
        par = _persona('par-agent', 'agent-participant')
        roster = (mod, par)  # moderator first per load_roster convention
        result = resolve_named_preset(PRESET_ROOM_PREFIX + 'preset-full-12', config, roster)
        assert result is not None and result[0].paperclip_agent_id == 'mod-agent'

    def test_moderator_excluded_leaves_participant_first(self):
        """When preset excludes the moderator, first valid roster persona becomes opener."""
        config = self._make_config([
            {'id': 'preset-nomod-1', 'name': 'No-mod', 'agentIds': ['par-agent']},
        ])
        mod = _persona('mod-agent', 'agent-moderator')
        par = _persona('par-agent', 'agent-participant')
        roster = (mod, par)
        result = resolve_named_preset(PRESET_ROOM_PREFIX + 'preset-nomod-1', config, roster)
        assert result is not None and len(result) == 1
        assert result[0].paperclip_agent_id == 'par-agent'


if __name__ == '__main__':
    unittest.main()
