import { useParams, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Divider from '@mui/material/Divider';
import Typography from '@mui/material/Typography';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DownloadIcon from '@mui/icons-material/Download';
import { useTranscript } from '../hooks/useTranscripts';

export function TranscriptReaderPage() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: transcript, isLoading } = useTranscript(id);

  const handleExport = (format: 'md' | 'json') => {
    window.open(`/api/transcripts/${id}/export?format=${format}`, '_blank');
  };

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', pt: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!transcript) {
    return (
      <Box sx={{ p: 3 }}>
        <Typography color="error">Transcript not found.</Typography>
      </Box>
    );
  }

  const title = (transcript.title as string) ?? id;

  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Main transcript area */}
      <Box sx={{ flex: 1, overflow: 'auto', p: 3 }}>
        {/* Header */}
        <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 1 }}>
          <Button
            startIcon={<ArrowBackIcon />}
            onClick={() => navigate('/transcripts')}
            size="small"
          >
            Back
          </Button>
          <Typography variant="h6" sx={{ fontWeight: 700, flex: 1 }}>
            {title}
          </Typography>
          <Button
            size="small"
            startIcon={<DownloadIcon />}
            onClick={() => handleExport('md')}
          >
            .md
          </Button>
          <Button
            size="small"
            startIcon={<DownloadIcon />}
            onClick={() => handleExport('json')}
          >
            .json
          </Button>
        </Box>

        <Divider sx={{ mb: 2 }} />

        {/* Events */}
        <pre
          style={{
            fontFamily: 'monospace',
            fontSize: '0.8rem',
            whiteSpace: 'pre-wrap',
            color: '#e6edf3',
            lineHeight: 1.6,
            margin: 0,
          }}
        >
          {JSON.stringify(transcript, null, 2)}
        </pre>
      </Box>

      {/* Metadata sidebar */}
      <Box
        sx={{
          width: 200,
          borderLeft: '1px solid #333',
          p: 2,
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
          flexShrink: 0,
        }}
      >
        <Typography variant="caption" sx={{ fontWeight: 700, color: '#4fc3f7' }}>
          Metadata
        </Typography>
        <Divider />
        <Typography variant="caption" sx={{ color: '#8b949e' }}>
          ID: {id.slice(0, 8)}…
        </Typography>
        {typeof transcript.setting === 'string' && (
          <Typography variant="caption" sx={{ color: '#8b949e' }}>
            Setting: {transcript.setting}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
