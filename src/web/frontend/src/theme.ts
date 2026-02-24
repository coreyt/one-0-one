import { createTheme } from '@mui/material/styles';

export const AGENT_COLORS = [
  '#4fc3f7', // 0 light blue
  '#81c784', // 1 green
  '#ffb74d', // 2 orange
  '#f06292', // 3 pink
  '#ba68c8', // 4 purple
  '#4db6ac', // 5 teal
];

export const agentColor = (index: number): string =>
  AGENT_COLORS[index % AGENT_COLORS.length];

export const semanticTokens: Record<string, string> = {
  '--hitl-color': '#ffffff',
  '--orchestrator-color': '#9e9e9e',
  '--monologue-bg': '#1a2a2f',
  '--monologue-text': '#90a4ae',
  '--private-bg': '#2a1f1a',
  '--private-border': '#8d6e63',
  '--team-a': '#7986cb',
  '--team-b': '#ef5350',
  '--team-c': '#66bb6a',
  '--team-d': '#ffa726',
};

export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#4fc3f7' },
    secondary: { main: '#81c784' },
    background: {
      default: '#0d1117',
      paper: '#161b22',
    },
    text: {
      primary: '#e6edf3',
      secondary: '#8b949e',
    },
  },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    fontSize: 13,
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        ':root': semanticTokens,
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: { backgroundImage: 'none' },
      },
    },
  },
});
