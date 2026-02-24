import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardActions from '@mui/material/CardActions';
import CardContent from '@mui/material/CardContent';
import CircularProgress from '@mui/material/CircularProgress';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import AddIcon from '@mui/icons-material/Add';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PersonIcon from '@mui/icons-material/Person';
import { useTemplates } from '../hooks/useTemplates';
import { useStartSession } from '../hooks/useSessions';
import { TypeChip } from '../components/shared/TypeChip';

const TYPE_TABS = ['All', 'Games', 'Social', 'Research', 'Task', 'Problem-Solve'];
const TYPE_VALUES: Record<string, string | undefined> = {
  All: undefined,
  Games: 'games',
  Social: 'social',
  Research: 'research',
  Task: 'task-completion',
  'Problem-Solve': 'problem-solve',
};

export function LibraryPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tabIndex, setTabIndex] = useState(0);

  const q = searchParams.get('q') ?? '';
  const typeFilter = TYPE_VALUES[TYPE_TABS[tabIndex]];

  const { data: templates, isLoading } = useTemplates({ type: typeFilter, q: q || undefined });
  const startSession = useStartSession();

  const handleSearch = (value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value) params.set('q', value);
    else params.delete('q');
    setSearchParams(params);
  };

  const handleLaunch = async (slug: string) => {
    // Fetch config then start session
    const resp = await fetch(`/api/templates/${slug}`);
    const config = await resp.json();
    const result = await startSession.mutateAsync(config);
    navigate(`/sessions/${result.session_id}`);
  };

  return (
    <Box sx={{ p: 3, height: '100%', overflow: 'auto' }}>
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 2 }}>
        <Typography variant="h6" sx={{ fontWeight: 700, flex: 1 }}>
          Session Library
        </Typography>
        <TextField
          size="small"
          placeholder="Search templates…"
          value={q}
          onChange={(e) => handleSearch(e.target.value)}
          sx={{ width: 240 }}
        />
        <Tooltip title="Create new template">
          <Button
            variant="outlined"
            startIcon={<AddIcon />}
            onClick={() => navigate('/wizard')}
          >
            New
          </Button>
        </Tooltip>
      </Box>

      {/* Type filter tabs */}
      <Tabs
        value={tabIndex}
        onChange={(_, v) => setTabIndex(v)}
        sx={{ mb: 2, borderBottom: '1px solid #333' }}
      >
        {TYPE_TABS.map((label) => (
          <Tab key={label} label={label} sx={{ fontSize: '0.8rem', minHeight: 40 }} />
        ))}
      </Tabs>

      {/* Template list */}
      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', pt: 4 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2 }}>
          {(templates ?? []).map((template) => (
            <Card
              key={template.slug}
              sx={{
                width: 280,
                background: '#161b22',
                border: '1px solid #30363d',
                '&:hover': { borderColor: '#4fc3f7' },
                transition: 'border-color 0.15s',
              }}
            >
              <CardContent sx={{ pb: 1 }}>
                <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mb: 0.75 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700, flex: 1 }}>
                    {template.title}
                  </Typography>
                  {template.setting && <TypeChip type={template.setting} />}
                </Box>
                <Typography
                  variant="body2"
                  sx={{
                    color: '#8b949e',
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                    mb: 1,
                    fontSize: '0.8rem',
                  }}
                >
                  {template.description}
                </Typography>
                <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                  <PersonIcon sx={{ fontSize: 14, color: '#8b949e' }} />
                  <Typography variant="caption" sx={{ color: '#8b949e' }}>
                    {template.agent_count} agents
                  </Typography>
                  {template.hitl_enabled && (
                    <Typography variant="caption" sx={{ color: '#4fc3f7', ml: 1 }}>
                      HITL
                    </Typography>
                  )}
                </Box>
              </CardContent>
              <CardActions sx={{ pt: 0, px: 2, pb: 1.5, gap: 1 }}>
                <Button
                  size="small"
                  variant="text"
                  onClick={() => navigate(`/wizard?template=${template.slug}`)}
                  sx={{ fontSize: '0.75rem' }}
                >
                  Edit
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => handleLaunch(template.slug)}
                  disabled={startSession.isPending}
                  sx={{ fontSize: '0.75rem', ml: 'auto' }}
                >
                  Launch
                </Button>
              </CardActions>
            </Card>
          ))}
          {(templates ?? []).length === 0 && (
            <Typography sx={{ color: '#8b949e', pt: 2 }}>
              No templates found.
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
}
