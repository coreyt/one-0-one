import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import PsychologyIcon from '@mui/icons-material/Psychology';
import { useSession } from '../hooks/useSessions';
import { usePauseSession, useResumeSession, useEndSession, useInjectMessage } from '../hooks/useSessions';
import { useSessionStream } from '../hooks/useSessionStream';
import { ChannelTabs } from '../components/chat/ChannelTabs';
import { MonologueDrawer } from '../components/chat/MonologueDrawer';
import { HITLInputBar } from '../components/chat/HITLInputBar';
import { AgentRoster } from '../components/session/AgentRoster';
import { TurnIndicator } from '../components/session/TurnIndicator';

export function LiveChatPage() {
  const { id: sessionId = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [paused, setPaused] = useState(false);
  const [monoOpen, setMonoOpen] = useState(false);

  const { data: sessionData } = useSession(sessionId);
  const stream = useSessionStream(sessionId);

  const pauseSession = usePauseSession();
  const resumeSession = useResumeSession();
  const endSession = useEndSession();
  const injectMessage = useInjectMessage();

  const config = (sessionData as { config?: { agents?: unknown[]; hitl?: { enabled?: boolean }; max_turns?: number } } | undefined)?.config;
  const agents = (config?.agents ?? []) as Array<{ id: string; name: string; role: string }>;
  const agentIndexMap = Object.fromEntries(agents.map((a, i) => [a.id, i]));
  const hasTeam = false; // simplified — would check agent team config

  const handleTogglePause = async () => {
    if (paused) {
      await resumeSession.mutateAsync(sessionId);
      setPaused(false);
    } else {
      await pauseSession.mutateAsync(sessionId);
      setPaused(true);
    }
  };

  const handleEnd = async () => {
    if (confirm('End this session?')) {
      await endSession.mutateAsync(sessionId);
      navigate('/');
    }
  };

  const handleSendHITL = (text: string, channel_id: string) => {
    injectMessage.mutate({ sessionId, text, channel_id });
  };

  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Main area */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Toolbar */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            px: 2,
            py: 0.75,
            borderBottom: '1px solid #333',
            background: '#161b22',
            gap: 1,
          }}
        >
          <Typography variant="body2" sx={{ color: '#8b949e', mr: 'auto' }}>
            Session: {sessionId.slice(0, 8)}…
          </Typography>

          <Tooltip title={monoOpen ? 'Hide monologue' : 'Show monologue'}>
            <IconButton
              size="small"
              onClick={() => setMonoOpen((o) => !o)}
              sx={{ color: monoOpen ? '#4fc3f7' : '#8b949e' }}
            >
              <PsychologyIcon />
            </IconButton>
          </Tooltip>

          <Tooltip title={paused ? 'Resume' : 'Pause'}>
            <IconButton size="small" onClick={handleTogglePause} sx={{ color: '#8b949e' }}>
              {paused ? <PlayArrowIcon /> : <PauseIcon />}
            </IconButton>
          </Tooltip>

          <Tooltip title="End session">
            <IconButton size="small" onClick={handleEnd} sx={{ color: '#f06292' }}>
              <StopIcon />
            </IconButton>
          </Tooltip>
        </Box>

        {/* Chat */}
        <Box sx={{ flex: 1, overflow: 'hidden' }}>
          <ChannelTabs
            channels={stream.channels}
            messages={stream.messages}
            agentIndexMap={agentIndexMap}
          />
        </Box>

        {/* HITL input */}
        {config?.hitl?.enabled && (
          <HITLInputBar
            onSend={handleSendHITL}
            hasTeam={hasTeam}
            disabled={stream.ended}
          />
        )}

        {/* Turn indicator */}
        <TurnIndicator
          currentTurn={stream.currentTurn}
          maxTurns={config?.max_turns}
          activeAgents={stream.activeAgents}
          ended={stream.ended}
        />
      </Box>

      {/* Monologue drawer */}
      <MonologueDrawer
        open={monoOpen}
        monologue={stream.monologue}
        agentName={stream.monologueAgent}
        onClose={() => setMonoOpen(false)}
      />

      {/* Agent roster */}
      <AgentRoster
        agents={agents as Parameters<typeof AgentRoster>[0]['agents']}
        agentStatuses={stream.agentStatuses}
        collapsed={monoOpen}
      />
    </Box>
  );
}
