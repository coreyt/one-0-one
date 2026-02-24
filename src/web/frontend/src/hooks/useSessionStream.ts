import { useEffect, useReducer } from 'react';
import type { SessionEvent } from '../types/events';
import type { SessionViewState } from '../types/state';
import { initialSessionViewState } from '../types/state';

type Action = { type: 'EVENT'; payload: SessionEvent };

function appendMessage(
  state: SessionViewState,
  event: Extract<SessionEvent, { type: 'MESSAGE' }>,
): SessionViewState {
  const prev = state.messages[event.channel_id] ?? [];
  return {
    ...state,
    messages: { ...state.messages, [event.channel_id]: [...prev, event] },
  };
}

function appendSystemMessage(
  state: SessionViewState,
  event: Extract<SessionEvent, { type: 'RULE_VIOLATION' }>,
): SessionViewState {
  const systemMsg = {
    type: 'MESSAGE' as const,
    timestamp: event.timestamp,
    turn_number: event.turn_number,
    session_id: event.session_id,
    agent_id: 'system',
    agent_name: 'System',
    model: 'system',
    channel_id: 'public',
    text: `Rule violation — ${event.agent_id}: ${event.rule}`,
    is_parallel: false,
  };
  const prev = state.messages['public'] ?? [];
  return {
    ...state,
    messages: { ...state.messages, public: [...prev, systemMsg] },
  };
}

export function sessionEventReducer(
  state: SessionViewState,
  action: Action,
): SessionViewState {
  const event = action.payload;

  switch (event.type) {
    case 'CHANNEL_CREATED':
      if (state.channels.some((c) => c.channel_id === event.channel_id)) {
        return state;
      }
      return { ...state, channels: [...state.channels, event] };

    case 'TURN':
      return {
        ...state,
        currentTurn: event.turn_number,
        activeAgents: event.agent_ids,
        monologue: '',
        monologueAgent: event.agent_ids[0] ?? '',
        agentStatuses: {
          ...state.agentStatuses,
          ...Object.fromEntries(event.agent_ids.map((id) => [id, 'thinking' as const])),
        },
      };

    case 'MESSAGE':
      return {
        ...appendMessage(state, event),
        agentStatuses: {
          ...state.agentStatuses,
          [event.agent_id]: 'speaking' as const,
        },
      };

    case 'MONOLOGUE':
      return {
        ...state,
        monologue: state.monologue + event.text,
        monologueAgent: event.agent_name,
      };

    case 'GAME_STATE':
      return { ...state, gameState: event.full_state };

    case 'RULE_VIOLATION':
      return appendSystemMessage(state, event);

    case 'SESSION_END':
      return { ...state, ended: true, endReason: event.reason };

    default:
      return state;
  }
}

export function useSessionStream(sessionId: string) {
  const [state, dispatch] = useReducer(sessionEventReducer, initialSessionViewState);

  useEffect(() => {
    if (!sessionId) return;
    const source = new EventSource(`/api/sessions/${sessionId}/stream`);

    source.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as SessionEvent;
        dispatch({ type: 'EVENT', payload: event });
      } catch {
        // ignore parse errors
      }
    };

    source.onerror = () => {
      source.close();
    };

    return () => source.close();
  }, [sessionId]);

  return state;
}
