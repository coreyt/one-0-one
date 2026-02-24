import Box from '@mui/material/Box';
import LinearProgress from '@mui/material/LinearProgress';
import Typography from '@mui/material/Typography';

interface TurnIndicatorProps {
  currentTurn: number;
  maxTurns?: number;
  activeAgents: string[];
  ended: boolean;
}

export function TurnIndicator({ currentTurn, maxTurns, activeAgents, ended }: TurnIndicatorProps) {
  const progress = maxTurns ? (currentTurn / maxTurns) * 100 : 0;
  const agentText = activeAgents.length > 0 ? activeAgents.join(', ') : '—';

  return (
    <Box sx={{ p: 1.5, borderTop: '1px solid #333' }}>
      {maxTurns && (
        <LinearProgress
          variant="determinate"
          value={progress}
          sx={{ mb: 0.75, height: 4, borderRadius: 2 }}
        />
      )}
      <Typography variant="caption" sx={{ color: '#8b949e', display: 'block' }}>
        Turn {currentTurn}{maxTurns ? ` / ${maxTurns}` : ''}
      </Typography>
      <Typography variant="caption" sx={{ color: ended ? '#81c784' : '#e6edf3' }}>
        {ended ? '✓ Session complete' : `● ${agentText} thinking…`}
      </Typography>
    </Box>
  );
}
