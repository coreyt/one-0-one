import { useEffect, useRef, useState } from 'react';
import Badge from '@mui/material/Badge';
import Box from '@mui/material/Box';
import Tab from '@mui/material/Tab';
import Tabs from '@mui/material/Tabs';
import type { ChannelCreatedEvent, MessageEvent } from '../../types/events';
import { ChatMessage } from './ChatMessage';

interface ChannelTabsProps {
  channels: ChannelCreatedEvent[];
  messages: Record<string, MessageEvent[]>;
  agentIndexMap: Record<string, number>;
}

function channelLabel(ch: ChannelCreatedEvent): string {
  if (ch.channel_id === 'public') return 'Public';
  if (ch.channel_type === 'team') {
    const name = ch.channel_id.replace(/^team_/, '').replace(/_/g, ' ');
    return `Team: ${name.charAt(0).toUpperCase() + name.slice(1)}`;
  }
  if (ch.channel_type === 'private') return 'Private';
  return ch.channel_id;
}

export function ChannelTabs({ channels, messages, agentIndexMap }: ChannelTabsProps) {
  const [activeTab, setActiveTab] = useState('public');
  const [unread, setUnread] = useState<Record<string, number>>({});
  const bottomRef = useRef<HTMLDivElement>(null);

  // Ensure public channel is always present
  const allChannels: ChannelCreatedEvent[] = channels.some((c) => c.channel_id === 'public')
    ? channels
    : [
        {
          type: 'CHANNEL_CREATED',
          timestamp: '',
          session_id: '',
          channel_id: 'public',
          channel_type: 'public',
          members: [],
        },
        ...channels,
      ];

  // Track unread counts on inactive tabs
  useEffect(() => {
    allChannels.forEach((ch) => {
      const msgs = messages[ch.channel_id] ?? [];
      if (ch.channel_id !== activeTab && msgs.length > 0) {
        setUnread((u) => {
          const prev = u[ch.channel_id] ?? 0;
          const newCount = msgs.length;
          return newCount > prev ? { ...u, [ch.channel_id]: newCount } : u;
        });
      }
    });
  }, [messages]);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages[activeTab]]);

  const handleTabChange = (_: unknown, newVal: string) => {
    setActiveTab(newVal);
    setUnread((u) => ({ ...u, [newVal]: 0 }));
  };

  const activeMessages = messages[activeTab] ?? [];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Tabs
        value={activeTab}
        onChange={handleTabChange}
        variant="scrollable"
        scrollButtons="auto"
        sx={{ borderBottom: '1px solid #333', minHeight: 40 }}
      >
        {allChannels.map((ch) => (
          <Tab
            key={ch.channel_id}
            value={ch.channel_id}
            label={
              <Badge
                badgeContent={unread[ch.channel_id] || 0}
                color="primary"
                max={99}
              >
                {channelLabel(ch)}
              </Badge>
            }
            sx={{ minHeight: 40, fontSize: '0.8rem' }}
          />
        ))}
      </Tabs>

      <Box
        sx={{
          flex: 1,
          overflow: 'auto',
          p: 1.5,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {activeMessages.map((msg, i) => (
          <ChatMessage
            key={`${msg.timestamp}-${i}`}
            event={msg}
            agentIndex={agentIndexMap[msg.agent_id] ?? 0}
          />
        ))}
        <div ref={bottomRef} />
      </Box>
    </Box>
  );
}
