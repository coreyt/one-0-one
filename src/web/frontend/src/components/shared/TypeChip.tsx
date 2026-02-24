import Chip from '@mui/material/Chip';

const TYPE_COLORS: Record<string, string> = {
  games: '#4fc3f7',
  social: '#81c784',
  research: '#ffb74d',
  task: '#f06292',
  'task-completion': '#f06292',
  'problem-solve': '#ba68c8',
};

const TYPE_ICONS: Record<string, string> = {
  games: '🎲',
  social: '💬',
  research: '📊',
  'task-completion': '✅',
  'problem-solve': '🧩',
};

interface TypeChipProps {
  type: string;
  size?: 'small' | 'medium';
}

export function TypeChip({ type, size = 'small' }: TypeChipProps) {
  const color = TYPE_COLORS[type] ?? '#9e9e9e';
  const icon = TYPE_ICONS[type] ?? '📄';
  return (
    <Chip
      label={`${icon} ${type}`}
      size={size}
      sx={{ color, borderColor: color, fontSize: '0.7rem' }}
      variant="outlined"
    />
  );
}
