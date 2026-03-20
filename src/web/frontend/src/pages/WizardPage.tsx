import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Paper from '@mui/material/Paper';
import Step from '@mui/material/Step';
import StepLabel from '@mui/material/StepLabel';
import Stepper from '@mui/material/Stepper';
import TextField from '@mui/material/TextField';
import MenuItem from '@mui/material/MenuItem';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Typography from '@mui/material/Typography';
import { useModels } from '../hooks/useModels';
import { useTemplate, useSaveTemplate } from '../hooks/useTemplates';
import { useStartSession } from '../hooks/useSessions';
import type { SessionConfig } from '../types/config';

const STEPS = ['Topic', 'Setting', 'Agents', 'Orchestrator', 'HITL', 'Review'];
const SETTINGS = ['social', 'research', 'game', 'task', 'problem-solve'];
const PROVIDERS = ['anthropic', 'openai', 'google', 'gemini', 'mistral'];
const ROUTING_MODES = [
  { value: 'pinned', label: 'Pinned Provider Model' },
  { value: 'airlock_routed', label: 'Airlock Routed' },
] as const;

const defaultConfig = (): SessionConfig => ({
  title: '',
  description: '',
  type: 'social',
  setting: 'social',
  topic: '',
  agents: [
    {
      id: 'agent_1',
      name: 'Agent 1',
      provider: 'anthropic',
      model: 'claude-sonnet-4-6',
      routing_mode: 'pinned',
      role: 'participant',
    },
  ],
  orchestrator: { type: 'python', module: 'basic' },
  hitl: { enabled: false },
  transcript: { auto_save: true, format: 'both', path: './sessions/' },
});

export function WizardPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const templateSlug = searchParams.get('template');

  const { data: existingTemplate } = useTemplate(templateSlug ?? '');
  const { data: airlockModels = [] } = useModels();
  const saveTemplate = useSaveTemplate();
  const startSession = useStartSession();

  const [step, setStep] = useState(0);
  const [config, setConfig] = useState<SessionConfig>(() =>
    existingTemplate ?? defaultConfig(),
  );

  const update = (partial: Partial<SessionConfig>) =>
    setConfig((c) => ({ ...c, ...partial }));

  const handleSave = async () => {
    await saveTemplate.mutateAsync(config);
    navigate('/');
  };

  const handleRun = async () => {
    const result = await startSession.mutateAsync(config);
    navigate(`/sessions/${result.session_id}`);
  };

  const renderStep = () => {
    switch (step) {
      case 0:
        return (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <TextField
              label="Session title"
              value={config.title}
              onChange={(e) => update({ title: e.target.value })}
              fullWidth
            />
            <TextField
              label="Description"
              value={config.description}
              onChange={(e) => update({ description: e.target.value })}
              fullWidth
              multiline
              rows={2}
            />
            <TextField
              label="Topic / prompt"
              value={config.topic}
              onChange={(e) => update({ topic: e.target.value })}
              fullWidth
              multiline
              rows={4}
              placeholder="What should the agents discuss or do?"
            />
          </Box>
        );

      case 1:
        return (
          <Box>
            <Typography variant="body2" sx={{ mb: 2, color: '#8b949e' }}>
              Choose the session setting:
            </Typography>
            <ToggleButtonGroup
              value={config.setting}
              exclusive
              onChange={(_, v) => v && update({ setting: v, type: v as SessionConfig['type'] })}
              orientation="vertical"
              fullWidth
            >
              {SETTINGS.map((s) => (
                <ToggleButton key={s} value={s} sx={{ justifyContent: 'flex-start' }}>
                  {s}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>
        );

      case 2:
        return (
          <Box>
            <Typography variant="body2" sx={{ mb: 2, color: '#8b949e' }}>
              {config.agents.length} agent(s) configured. Edit agent details below:
            </Typography>
            {config.agents.map((agent, i) => (
              (() => {
                const routed = (agent.routing_mode ?? 'pinned') === 'airlock_routed';
                const modelOptions =
                  routed && airlockModels.length > 0
                    ? Array.from(new Set([agent.model, ...airlockModels].filter(Boolean)))
                    : [];
                return (
              <Paper key={agent.id} sx={{ p: 2, mb: 1, background: '#1c2128' }}>
                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                  <TextField
                    label="Name"
                    size="small"
                    value={agent.name}
                    onChange={(e) => {
                      const agents = [...config.agents];
                      agents[i] = { ...agent, name: e.target.value };
                      update({ agents });
                    }}
                    sx={{ width: 120 }}
                  />
                  <TextField
                    label="Routing"
                    select
                    size="small"
                    value={agent.routing_mode ?? 'pinned'}
                    onChange={(e) => {
                      const agents = [...config.agents];
                      agents[i] = {
                        ...agent,
                        routing_mode: e.target.value as 'pinned' | 'airlock_routed',
                      };
                      update({ agents });
                    }}
                    sx={{ width: 180 }}
                  >
                    {ROUTING_MODES.map((mode) => (
                      <MenuItem key={mode.value} value={mode.value}>
                        {mode.label}
                      </MenuItem>
                    ))}
                  </TextField>
                  <TextField
                    label="Provider"
                    select
                    size="small"
                    value={agent.provider}
                    disabled={(agent.routing_mode ?? 'pinned') === 'airlock_routed'}
                    onChange={(e) => {
                      const agents = [...config.agents];
                      agents[i] = { ...agent, provider: e.target.value };
                      update({ agents });
                    }}
                    sx={{ width: 120 }}
                  >
                    {PROVIDERS.map((provider) => (
                      <MenuItem key={provider} value={provider}>
                        {provider}
                      </MenuItem>
                    ))}
                  </TextField>
                  <TextField
                    label="Model"
                    size="small"
                    select={routed && modelOptions.length > 0}
                    value={agent.model}
                    onChange={(e) => {
                      const agents = [...config.agents];
                      agents[i] = { ...agent, model: e.target.value };
                      update({ agents });
                    }}
                    sx={{ width: 180 }}
                  >
                    {routed && modelOptions.length > 0
                      ? modelOptions.map((model) => (
                          <MenuItem key={model} value={model}>
                            {model}
                          </MenuItem>
                        ))
                      : null}
                  </TextField>
                  <TextField
                    label="Role"
                    size="small"
                    value={agent.role}
                    onChange={(e) => {
                      const agents = [...config.agents];
                      agents[i] = { ...agent, role: e.target.value };
                      update({ agents });
                    }}
                    sx={{ width: 120 }}
                  />
                </Box>
              </Paper>
                );
              })()
            ))}
          </Box>
        );

      case 4: // HITL
        return (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Typography variant="body2" sx={{ color: '#8b949e' }}>
              Enable human-in-the-loop participation:
            </Typography>
            <ToggleButtonGroup
              value={config.hitl?.enabled ? 'yes' : 'no'}
              exclusive
              onChange={(_, v) => update({ hitl: { ...config.hitl, enabled: v === 'yes' } })}
            >
              <ToggleButton value="yes">Enabled</ToggleButton>
              <ToggleButton value="no">Disabled</ToggleButton>
            </ToggleButtonGroup>
            {config.hitl?.enabled && (
              <TextField
                label="Your role name"
                value={config.hitl?.role ?? ''}
                onChange={(e) => update({ hitl: { ...config.hitl!, role: e.target.value } })}
                fullWidth
              />
            )}
          </Box>
        );

      case 5: // Review
        return (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <Typography variant="subtitle2">Review</Typography>
            <Typography variant="body2">Title: {config.title || '(untitled)'}</Typography>
            <Typography variant="body2">Setting: {config.setting}</Typography>
            <Typography variant="body2">Agents: {config.agents.length}</Typography>
            <Typography variant="body2">HITL: {config.hitl?.enabled ? 'Yes' : 'No'}</Typography>
          </Box>
        );

      default:
        return (
          <Typography sx={{ color: '#8b949e' }}>
            Step {step + 1} — coming soon
          </Typography>
        );
    }
  };

  return (
    <Box sx={{ p: 3, maxWidth: 640, mx: 'auto' }}>
      <Typography variant="h6" sx={{ mb: 3, fontWeight: 700 }}>
        {templateSlug ? 'Edit Template' : 'New Session'}
      </Typography>

      <Stepper activeStep={step} sx={{ mb: 4 }}>
        {STEPS.map((label) => (
          <Step key={label}>
            <StepLabel sx={{ '& .MuiStepLabel-label': { fontSize: '0.75rem' } }}>
              {label}
            </StepLabel>
          </Step>
        ))}
      </Stepper>

      <Paper sx={{ p: 3, mb: 3, background: '#161b22' }}>{renderStep()}</Paper>

      <Box sx={{ display: 'flex', gap: 1 }}>
        <Button
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0}
        >
          Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button
            variant="contained"
            onClick={() => setStep((s) => s + 1)}
            sx={{ ml: 'auto' }}
          >
            Next
          </Button>
        ) : (
          <>
            <Button onClick={handleSave} disabled={saveTemplate.isPending} sx={{ ml: 'auto' }}>
              Save template
            </Button>
            <Button
              variant="contained"
              onClick={handleRun}
              disabled={startSession.isPending}
            >
              Run session
            </Button>
          </>
        )}
      </Box>
    </Box>
  );
}
