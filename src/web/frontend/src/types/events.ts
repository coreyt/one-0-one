/**
 * TypeScript discriminated union mirroring Python's SessionEvent models exactly.
 * One interface per event type — matches src/session/events.py.
 */

interface BaseEvent {
  timestamp: string; // ISO 8601
  turn_number: number;
  session_id: string;
}

export interface MessageEvent extends BaseEvent {
  type: 'MESSAGE';
  agent_id: string;
  agent_name: string;
  model: string;
  channel_id: string;
  recipient_id?: string;
  text: string;
  is_parallel: boolean;
}

export interface MonologueEvent extends BaseEvent {
  type: 'MONOLOGUE';
  agent_id: string;
  agent_name: string;
  text: string;
}

export interface TurnEvent extends BaseEvent {
  type: 'TURN';
  agent_ids: string[];
  is_parallel: boolean;
}

export interface GameStateEvent extends BaseEvent {
  type: 'GAME_STATE';
  updates: Record<string, unknown>;
  full_state: Record<string, unknown>;
}

export interface RuleViolationEvent extends BaseEvent {
  type: 'RULE_VIOLATION';
  agent_id: string;
  rule: string;
  violation_text: string;
}

export interface ChannelCreatedEvent {
  type: 'CHANNEL_CREATED';
  timestamp: string;
  session_id: string;
  channel_id: string;
  channel_type: 'public' | 'team' | 'private';
  members: string[];
}

export interface SessionEndEvent extends BaseEvent {
  type: 'SESSION_END';
  reason: 'max_turns' | 'win_condition' | 'completion_signal' | 'user_ended' | 'error';
  message?: string;
}

export interface IncidentEvent extends BaseEvent {
  type: 'INCIDENT';
  agent_id: string;
  agent_name: string;
  model: string;
  incident_type: 'timeout' | 'error';
  detail: string;
}

export type SessionEvent =
  | MessageEvent
  | MonologueEvent
  | TurnEvent
  | GameStateEvent
  | RuleViolationEvent
  | ChannelCreatedEvent
  | SessionEndEvent
  | IncidentEvent;
