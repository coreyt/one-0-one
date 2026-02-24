import Avatar from '@mui/material/Avatar';
import Box from '@mui/material/Box';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { agentColor } from '../../theme';
import type { AgentConfig } from '../../types/config';

interface AgentRosterProps {
  agents: AgentConfig[];
  agentStatuses: Record<string, string>;
  collapsed?: boolean;
}

const STATUS_ICONS: Record<string, string> = {
  thinking: '💭',
  speaking: '💬',
  idle: '○',
  done: '✓',
};

export function AgentRoster({ agents, agentStatuses, collapsed = false }: AgentRosterProps) {
  return (
    <Box
      sx={{
        width: collapsed ? 48 : 200,
        transition: 'width 0.2s',
        borderLeft: '1px solid #333',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        py: 1,
      }}
    >
      {agents.map((agent, i) => {
        const color = agentColor(i);
        const status = agentStatuses[agent.id] ?? 'idle';
        const statusIcon = STATUS_ICONS[status] ?? '○';

        return collapsed ? (
          <Tooltip key={agent.id} title={`${agent.name} — ${status}`} placement="left">
            <Avatar
              sx={{
                bgcolor: color,
                width: 32,
                height: 32,
                m: 0.5,
                fontSize: 13,
                cursor: 'default',
              }}
            >
              {agent.name[0]}
            </Avatar>
          </Tooltip>
        ) : (
          <Box
            key={agent.id}
            sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1, py: 0.75 }}
          >
            <Avatar sx={{ bgcolor: color, width: 28, height: 28, fontSize: 12 }}>
              {agent.name[0]}
            </Avatar>
            <Box sx={{ flex: 1, overflow: 'hidden' }}>
              <Typography
                variant="caption"
                sx={{ color, fontWeight: 600, display: 'block' }}
                noWrap
              >
                {agent.name}
              </Typography>
              <Typography
                variant="caption"
                sx={{ color: '#888', display: 'block', fontSize: '0.68rem' }}
                noWrap
              >
                {agent.role}
              </Typography>
            </Box>
            <Typography variant="caption" sx={{ fontSize: '0.8rem' }}>
              {statusIcon}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
