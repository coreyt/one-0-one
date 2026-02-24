import Avatar from '@mui/material/Avatar';
import Chip from '@mui/material/Chip';
import { agentColor } from '../../theme';

interface AgentChipProps {
  name: string;
  index: number;
  size?: 'small' | 'medium';
}

export function AgentChip({ name, index, size = 'small' }: AgentChipProps) {
  const color = agentColor(index);
  return (
    <Chip
      avatar={
        <Avatar sx={{ bgcolor: color, width: 20, height: 20, fontSize: 11 }}>
          {name[0]}
        </Avatar>
      }
      label={name}
      size={size}
      sx={{ color, borderColor: color }}
      variant="outlined"
    />
  );
}
