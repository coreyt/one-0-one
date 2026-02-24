import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

export function SettingsPage() {
  return (
    <Box sx={{ p: 3 }}>
      <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
        Settings
      </Typography>
      <Typography sx={{ color: '#8b949e' }}>
        Settings configuration coming soon.
      </Typography>
    </Box>
  );
}
