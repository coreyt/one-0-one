import Box from '@mui/material/Box';
import { NavRail } from './NavRail';

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <Box sx={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <NavRail />
      <Box
        component="main"
        sx={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}
      >
        {children}
      </Box>
    </Box>
  );
}
