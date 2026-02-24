/** Mirrors src/session/config.py SessionConfig and sub-models. */

export interface AgentConfig {
  id: string;
  name: string;
  provider: string;
  model: string;
  role: string;
  persona?: string;
  team?: string;
  monologue?: boolean;
  monologue_mode?: 'prompt' | 'native';
}

export interface OrchestratorConfig {
  type: 'python' | 'llm';
  module?: string;
  provider?: string;
  model?: string;
  persona?: string;
}

export interface HITLConfig {
  enabled: boolean;
  role?: string;
}

export interface TranscriptConfig {
  auto_save: boolean;
  format: 'markdown' | 'json' | 'both';
  path: string;
}

export interface SessionConfig {
  title: string;
  description: string;
  type: 'games' | 'social' | 'task-completion' | 'research' | 'problem-solve';
  setting: string;
  topic: string;
  agents: AgentConfig[];
  orchestrator?: OrchestratorConfig;
  hitl?: HITLConfig;
  transcript?: TranscriptConfig;
  max_turns?: number;
}

export interface TemplateSummary {
  slug: string;
  title: string;
  description?: string;
  setting?: string;
  agent_count: number;
  hitl_enabled: boolean;
}
