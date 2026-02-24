import React from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';

interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <Box p={2}>
          <Alert severity="error">
            {this.state.error.message}
          </Alert>
        </Box>
      );
    }
    return this.props.children;
  }
}
