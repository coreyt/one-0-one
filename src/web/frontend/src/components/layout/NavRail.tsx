import Box from '@mui/material/Box';
import Divider from '@mui/material/Divider';
import Tooltip from '@mui/material/Tooltip';
import IconButton from '@mui/material/IconButton';
import Typography from '@mui/material/Typography';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';
import HistoryIcon from '@mui/icons-material/History';
import SettingsIcon from '@mui/icons-material/Settings';
import { useNavigate, useLocation } from 'react-router-dom';

const NAV_ITEMS = [
  { icon: <LibraryBooksIcon />, label: 'Library', path: '/' },
  { icon: <HistoryIcon />, label: 'Transcripts', path: '/transcripts' },
];

export function NavRail() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Box
      sx={{
        width: 60,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        py: 1,
        borderRight: '1px solid #333',
        background: '#0d1117',
      }}
    >
      <Typography
        variant="caption"
        sx={{ fontWeight: 700, color: '#4fc3f7', fontSize: '0.65rem', mb: 1, letterSpacing: 1 }}
      >
        1-0-1
      </Typography>
      <Divider sx={{ width: '100%', mb: 1 }} />

      {NAV_ITEMS.map((item) => (
        <Tooltip key={item.path} title={item.label} placement="right">
          <IconButton
            onClick={() => navigate(item.path)}
            sx={{
              color: location.pathname === item.path ? '#4fc3f7' : '#8b949e',
              mb: 0.5,
            }}
          >
            {item.icon}
          </IconButton>
        </Tooltip>
      ))}

      <Box sx={{ flexGrow: 1 }} />
      <Tooltip title="Settings" placement="right">
        <IconButton
          onClick={() => navigate('/settings')}
          sx={{ color: location.pathname === '/settings' ? '#4fc3f7' : '#8b949e' }}
        >
          <SettingsIcon />
        </IconButton>
      </Tooltip>
    </Box>
  );
}
