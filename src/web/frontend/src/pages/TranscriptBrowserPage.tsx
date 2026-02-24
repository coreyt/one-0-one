import { useNavigate, useSearchParams } from 'react-router-dom';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useTranscripts } from '../hooks/useTranscripts';
import { TypeChip } from '../components/shared/TypeChip';

export function TranscriptBrowserPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get('q') ?? '';

  const { data: transcripts, isLoading } = useTranscripts({ q: q || undefined });

  const handleSearch = (value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value) params.set('q', value);
    else params.delete('q');
    setSearchParams(params);
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 2 }}>
        <Typography variant="h6" sx={{ fontWeight: 700, flex: 1 }}>
          Transcripts
        </Typography>
        <TextField
          size="small"
          placeholder="Search…"
          value={q}
          onChange={(e) => handleSearch(e.target.value)}
          sx={{ width: 240 }}
        />
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', pt: 4 }}>
          <CircularProgress />
        </Box>
      ) : (
        <TableContainer component={Paper} sx={{ background: '#161b22' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Title</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Date</TableCell>
                <TableCell align="right">Agents</TableCell>
                <TableCell align="right">Turns</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(transcripts ?? []).map((t) => (
                <TableRow
                  key={t.id}
                  hover
                  onClick={() => navigate(`/transcripts/${t.id}`)}
                  sx={{ cursor: 'pointer' }}
                >
                  <TableCell>{t.title}</TableCell>
                  <TableCell>
                    {t.setting && <TypeChip type={t.setting} />}
                  </TableCell>
                  <TableCell>{t.date}</TableCell>
                  <TableCell align="right">{t.agent_count}</TableCell>
                  <TableCell align="right">{t.turn_count}</TableCell>
                </TableRow>
              ))}
              {(transcripts ?? []).length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} sx={{ textAlign: 'center', color: '#8b949e' }}>
                    No transcripts found.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
