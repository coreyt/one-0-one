import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Typography from '@mui/material/Typography';
import LockIcon from '@mui/icons-material/Lock';
import { agentColor } from '../../theme';
import type { MessageEvent } from '../../types/events';

interface ChatMessageProps {
  event: MessageEvent;
  agentIndex: number;
}

export function ChatMessage({ event, agentIndex }: ChatMessageProps) {
  const color = agentColor(agentIndex);
  const isPrivate = event.recipient_id != null;
  const isSystem = event.agent_id === 'system';

  if (isSystem) {
    return (
      <Box
        sx={{
          color: 'var(--orchestrator-color)',
          fontStyle: 'italic',
          fontSize: '0.82rem',
          py: 0.5,
          my: 0.5,
          borderTop: '1px solid #333',
          borderBottom: '1px solid #333',
          textAlign: 'center',
        }}
      >
        — {event.text} —
      </Box>
    );
  }

  return (
    <Box
      sx={{
        mb: 1.5,
        ...(isPrivate && {
          background: 'var(--private-bg)',
          borderLeft: '3px solid var(--private-border)',
          pl: 1.5,
          py: 0.5,
          borderRadius: '0 4px 4px 0',
        }),
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.25 }}>
        {isPrivate && (
          <LockIcon sx={{ fontSize: 13, color: 'var(--private-border)' }} />
        )}
        <Typography variant="caption" sx={{ color, fontWeight: 700, lineHeight: 1 }}>
          {event.agent_name}
          {isPrivate && event.recipient_id ? ` → ${event.recipient_id}` : ''}
        </Typography>
        {event.is_parallel && (
          <Chip label="parallel" size="small" sx={{ height: 14, fontSize: 9 }} />
        )}
      </Box>
      <Typography variant="body2" sx={{ lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
        {event.text}
      </Typography>
    </Box>
  );
}
