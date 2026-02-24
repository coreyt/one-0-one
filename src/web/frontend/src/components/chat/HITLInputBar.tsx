import { useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import FormControl from '@mui/material/FormControl';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import TextField from '@mui/material/TextField';
import SendIcon from '@mui/icons-material/Send';

interface HITLInputBarProps {
  onSend: (text: string, channelId: string) => void;
  hasTeam?: boolean;
  disabled?: boolean;
}

export function HITLInputBar({ onSend, hasTeam = false, disabled = false }: HITLInputBarProps) {
  const [text, setText] = useState('');
  const [channel, setChannel] = useState('public');

  const handleSend = () => {
    const trimmed = text.trim();
    if (trimmed) {
      onSend(trimmed, channel);
      setText('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <Box
      sx={{
        display: 'flex',
        gap: 1,
        p: 1,
        borderTop: '1px solid var(--hitl-color, #444)',
        background: '#161b22',
      }}
    >
      {hasTeam && (
        <FormControl size="small" sx={{ minWidth: 110 }}>
          <InputLabel>Channel</InputLabel>
          <Select
            value={channel}
            label="Channel"
            onChange={(e) => setChannel(e.target.value)}
            disabled={disabled}
          >
            <MenuItem value="public">Public</MenuItem>
            <MenuItem value="team">Team</MenuItem>
          </Select>
        </FormControl>
      )}
      <TextField
        size="small"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Your message…"
        disabled={disabled}
        fullWidth
        multiline
        maxRows={3}
      />
      <Button
        variant="contained"
        onClick={handleSend}
        disabled={disabled || !text.trim()}
        endIcon={<SendIcon />}
        sx={{ whiteSpace: 'nowrap' }}
      >
        Send
      </Button>
    </Box>
  );
}
