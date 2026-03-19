"""Tests for the authoritative Mafia game plugin."""

from src.games import GameAction, MafiaGame, load_game, load_game_from_config
from src.session.config import AgentConfig, GameConfig


def _agents() -> list[AgentConfig]:
    return [
        AgentConfig(id="moderator", name="Narrator", provider="p", model="m", role="moderator"),
        AgentConfig(id="mafia_don", name="Don Corvo", provider="p", model="m", role="mafia", team="mafia"),
        AgentConfig(id="mafia_soldier", name="Sal Bricks", provider="p", model="m", role="mafia", team="mafia"),
        AgentConfig(id="mafia_consigliere", name="Luca Moretti", provider="p", model="m", role="mafia", team="mafia"),
        AgentConfig(id="detective", name="Iris Sharp", provider="p", model="m", role="detective"),
        AgentConfig(id="doctor", name="Dante Mend", provider="p", model="m", role="doctor"),
        AgentConfig(id="villager_1", name="Rosa Fields", provider="p", model="m", role="villager"),
        AgentConfig(id="villager_2", name="Marco Stone", provider="p", model="m", role="villager"),
        AgentConfig(id="villager_3", name="Cleo Vance", provider="p", model="m", role="villager"),
        AgentConfig(id="villager_4", name="Reed Cole", provider="p", model="m", role="villager"),
    ]


def _config() -> GameConfig:
    return GameConfig(plugin="mafia", name="Mafia")


def _new_state():
    game = MafiaGame()
    return game, game.initial_state(_config(), _agents())


class TestMafiaInitialState:
    def test_registry_loads_mafia(self):
        assert isinstance(load_game("mafia"), MafiaGame)
        assert isinstance(load_game_from_config(_config()), MafiaGame)

    def test_initial_state_assigns_roles_and_phase(self):
        game, state = _new_state()

        assert game.game_type == "mafia"
        assert state.phase == "night_mafia_discussion"
        assert state.round_number == 1
        assert state.alive_players[0] == "mafia_don"
        assert state.detective_id == "detective"
        assert state.doctor_id == "doctor"
        assert state.mafia_order == ["mafia_don", "mafia_soldier", "mafia_consigliere"]

    def test_visible_state_is_role_scoped(self):
        game, state = _new_state()

        mafia_view = game.visible_state(state, "mafia_don").payload
        villager_view = game.visible_state(state, "villager_1").payload

        assert len(mafia_view["mafia_teammates"]) == 2
        assert "mafia_teammates" not in villager_view
        assert villager_view["self_role"] == "villager"


class TestMafiaFlow:
    def test_discussion_turn_advances_to_mafia_vote(self):
        game, state = _new_state()

        for actor in ["mafia_don", "mafia_soldier", "mafia_consigliere"]:
            result = game.apply_message_turn(state, actor, "We need a target.")
            state = result.next_state

        assert state.phase == "night_mafia_vote"
        assert state.current_vote_order == ["mafia_don", "mafia_soldier", "mafia_consigliere"]

    def test_doctor_save_prevents_kill_and_detective_gets_private_result(self):
        game, state = _new_state()

        for actor in ["mafia_don", "mafia_soldier", "mafia_consigliere"]:
            state = game.apply_message_turn(state, actor, "Discuss.").next_state
        state = game.apply_action(
            state, "mafia_don", GameAction(action_type="night_mafia_vote", payload={"target": "villager_1"})
        ).next_state
        state = game.apply_action(
            state, "mafia_soldier", GameAction(action_type="night_mafia_vote", payload={"target": "villager_2"})
        ).next_state
        state = game.apply_action(
            state, "mafia_consigliere", GameAction(action_type="night_mafia_vote", payload={"target": "villager_1"})
        ).next_state

        detective_result = game.apply_action(
            state, "detective", GameAction(action_type="night_detective", payload={"investigate": "mafia_don"})
        )
        state = detective_result.next_state

        assert detective_result.private_events[0]["recipient_id"] == "detective"
        assert "mafia" in detective_result.private_events[0]["text"]

        doctor_result = game.apply_action(
            state, "doctor", GameAction(action_type="night_doctor", payload={"protect": "villager_1"})
        )
        state = doctor_result.next_state

        assert state.phase == "day_discussion"
        assert "villager_1" in state.alive_players
        assert doctor_result.public_events[0]["text"] == "Night 1 result: no one died."

    def test_day_vote_eliminates_on_strict_majority(self):
        game, state = _new_state()

        for actor in ["mafia_don", "mafia_soldier", "mafia_consigliere"]:
            state = game.apply_message_turn(state, actor, "Discuss.").next_state
        for actor, target in [
            ("mafia_don", "villager_1"),
            ("mafia_soldier", "villager_1"),
            ("mafia_consigliere", "villager_1"),
        ]:
            state = game.apply_action(
                state,
                actor,
                GameAction(action_type="night_mafia_vote", payload={"target": target}),
            ).next_state
        state = game.apply_action(
            state, "detective", GameAction(action_type="night_detective", payload={"investigate": "mafia_don"})
        ).next_state
        state = game.apply_action(
            state, "doctor", GameAction(action_type="night_doctor", payload={"protect": "villager_2"})
        ).next_state

        discussion_order = list(state.alive_players)
        for actor in discussion_order:
            state = game.apply_message_turn(state, actor, "Public discussion.").next_state

        vote_plan = {
            "mafia_don": "detective",
            "mafia_soldier": "detective",
            "mafia_consigliere": "detective",
            "detective": "mafia_don",
            "doctor": "mafia_don",
            "villager_2": "mafia_don",
            "villager_3": "mafia_don",
            "villager_4": "mafia_don",
        }
        result = None
        for actor in discussion_order:
            result = game.apply_action(
                state,
                actor,
                GameAction(action_type="day_vote", payload={"vote_for": vote_plan[actor]}),
            )
            state = result.next_state

        assert result is not None
        assert "mafia_don" in state.eliminated
        assert state.revealed_roles["mafia_don"] == "mafia"
        assert "revealed as mafia" in result.public_events[0]["text"].lower()

    def test_town_win_detection(self):
        game, state = _new_state()

        state.alive_players = ["detective", "doctor", "villager_1"]
        state.eliminated = ["mafia_don", "mafia_soldier", "mafia_consigliere"]
        state.revealed_roles = {
            "mafia_don": "mafia",
            "mafia_soldier": "mafia",
            "mafia_consigliere": "mafia",
        }
        result = game._resolve_night(state)
        final_state = result.next_state

        assert game.is_terminal(final_state) is True
        outcome = game.outcome(final_state)
        assert outcome is not None
        assert final_state.winner == "town"
        assert set(outcome.winners) == {"detective", "doctor", "villager_1"}
