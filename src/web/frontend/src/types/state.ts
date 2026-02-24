/** Mirrors src/session/state.py runtime state models. */

import type { AgentConfig } from './config';
import type { ChannelCreatedEvent, MessageEvent } from './events';

export interface AgentState {
  config: AgentConfig;
  status: 'idle' | 'thinking' | 'speaking' | 'done';
  token_usage: Record<string, number>;
}

export interface SessionViewState {
  channels: ChannelCreatedEvent[];
  messages: Record<string, MessageEvent[]>; // channel_id → messages
  monologue: string;
  monologueAgent: string;
  currentTurn: number;
  activeAgents: string[];
  agentStatuses: Record<string, AgentState['status']>;
  gameState: Record<string, unknown>;
  ended: boolean;
  endReason: string;
}

export const initialSessionViewState: SessionViewState = {
  channels: [],
  messages: {},
  monologue: '',
  monologueAgent: '',
  currentTurn: 0,
  activeAgents: [],
  agentStatuses: {},
  gameState: {},
  ended: false,
  endReason: '',
};
