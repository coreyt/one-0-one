import { createBrowserRouter } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { LibraryPage } from './pages/LibraryPage';
import { WizardPage } from './pages/WizardPage';
import { LiveChatPage } from './pages/LiveChatPage';
import { TranscriptBrowserPage } from './pages/TranscriptBrowserPage';
import { TranscriptReaderPage } from './pages/TranscriptReaderPage';
import { SettingsPage } from './pages/SettingsPage';
import { ErrorBoundary } from './components/shared/ErrorBoundary';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell><LibraryPage /></AppShell>,
    errorElement: <ErrorBoundary><div>Error loading page</div></ErrorBoundary>,
  },
  {
    path: '/wizard',
    element: <AppShell><WizardPage /></AppShell>,
  },
  {
    path: '/sessions/:id',
    element: (
      <AppShell>
        <LiveChatPage />
      </AppShell>
    ),
  },
  {
    path: '/transcripts',
    element: <AppShell><TranscriptBrowserPage /></AppShell>,
  },
  {
    path: '/transcripts/:id',
    element: <AppShell><TranscriptReaderPage /></AppShell>,
  },
  {
    path: '/settings',
    element: <AppShell><SettingsPage /></AppShell>,
  },
]);
