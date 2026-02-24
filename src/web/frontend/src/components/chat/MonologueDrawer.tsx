import { useEffect, useRef } from 'react';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Drawer from '@mui/material/Drawer';
import Typography from '@mui/material/Typography';
import PsychologyIcon from '@mui/icons-material/Psychology';

interface MonologueDrawerProps {
  open: boolean;
  monologue: string;
  agentName: string;
  onClose: () => void;
}

const DRAWER_WIDTH = 320;

export function MonologueDrawer({ open, monologue, agentName, onClose }: MonologueDrawerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [monologue]);

  return (
    <Drawer
      anchor="right"
      variant="persistent"
      open={open}
      onClose={onClose}
      sx={{
        width: open ? DRAWER_WIDTH : 0,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: DRAWER_WIDTH,
          boxSizing: 'border-box',
          position: 'relative',
          height: '100%',
          border: 'none',
          borderLeft: '1px solid #333',
        },
      }}
    >
      <Box
        sx={{
          p: 1.5,
          borderBottom: '1px solid #333',
          display: 'flex',
          alignItems: 'center',
          gap: 1,
        }}
      >
        <PsychologyIcon sx={{ color: 'var(--monologue-text)', fontSize: 18 }} />
        <Typography variant="caption" sx={{ color: 'var(--monologue-text)', flex: 1 }}>
          {agentName || 'Agent'} — thinking…
        </Typography>
        {monologue && <CircularProgress size={12} />}
      </Box>

      <Box
        ref={scrollRef}
        sx={{
          flex: 1,
          overflow: 'auto',
          background: 'var(--monologue-bg)',
          p: 1.5,
        }}
      >
        <Typography
          variant="body2"
          sx={{
            color: 'var(--monologue-text)',
            whiteSpace: 'pre-wrap',
            fontFamily: 'monospace',
            fontSize: '0.78rem',
            lineHeight: 1.5,
          }}
        >
          {monologue || '(waiting for agent…)'}
        </Typography>
      </Box>
    </Drawer>
  );
}
