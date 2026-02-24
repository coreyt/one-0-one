# one-0-one — Web UI Component Design

**Status:** Approved for implementation
**References:** `dev/preliminary-solution-design.md`, `agents/gui-architect.md`, `src/session/`

---

## 1. Overview

The web UI is a FastAPI backend + React/TypeScript frontend. The backend embeds the
session engine in-process and exposes it via REST and Server-Sent Events (SSE). The
frontend consumes the API using React Query for state and `EventSource` for live
streaming.

```
Browser (React + MUI)
    │
    │  REST (React Query)          SSE (EventSource)
    │  GET/POST /api/*             GET /api/sessions/{id}/stream
    ▼
FastAPI  (/src/web/api.py)
    │
    │  in-process
    ▼
SessionEngine + EventBus  (/src/session/)
    │
    ▼
LiteLLM → airlock router → providers
```

---

## 2. File Structure

```
src/web/
├── api.py                          # FastAPI router — all REST + SSE endpoints
├── session_manager.py              # In-process session registry
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── index.html
    └── src/
        ├── main.tsx                # React entry point, QueryClient, ThemeProvider
        ├── theme.ts                # MUI theme + one-0-one design tokens
        ├── router.tsx              # React Router routes
        ├── pages/
        │   ├── LibraryPage.tsx     # Session template library (home)
        │   ├── WizardPage.tsx      # Setup wizard
        │   ├── LiveChatPage.tsx    # Active session view
        │   ├── TranscriptBrowserPage.tsx
        │   ├── TranscriptReaderPage.tsx
        │   └── SettingsPage.tsx
        ├── components/
        │   ├── layout/
        │   │   ├── AppShell.tsx    # Nav drawer + top bar
        │   │   └── NavRail.tsx     # Left navigation rail
        │   ├── chat/
        │   │   ├── ChannelTabs.tsx
        │   │   ├── ChatMessage.tsx
        │   │   ├── MonologueDrawer.tsx
        │   │   └── HITLInputBar.tsx
        │   ├── session/
        │   │   ├── AgentRoster.tsx
        │   │   ├── TurnIndicator.tsx
        │   │   └── GameStatePanel.tsx
        │   ├── wizard/
        │   │   ├── WizardStepper.tsx
        │   │   ├── AgentForm.tsx
        │   │   └── OrchestratorForm.tsx
        │   └── shared/
        │       ├── TypeChip.tsx    # Color-coded session type chip
        │       ├── AgentChip.tsx   # Agent color + name chip
        │       └── ErrorBoundary.tsx
        ├── hooks/
        │   ├── useTemplates.ts     # React Query: template CRUD
        │   ├── useSessions.ts      # React Query: session state
        │   ├── useSessionStream.ts # SSE: EventSource subscription
        │   ├── useTranscripts.ts   # React Query: transcript list + search
        │   └── useChannelStream.ts # SSE: per-channel event stream
        └── types/
            ├── events.ts           # SessionEvent discriminated union (mirrors Python)
            ├── config.ts           # SessionConfig, AgentConfig shapes
            └── state.ts            # SessionState, AgentState shapes
```

---

## 3. Backend Design (`src/web/api.py`)

### 3.1 Session Manager

An in-process registry holds running sessions keyed by session ID. Only one session
should be active at a time in v1 (per PSD risk R-006).

```python
# src/web/session_manager.py
from dataclasses import dataclass, field
from src.session.engine import SessionEngine
from src.session.event_bus import EventBus
from src.session.config import SessionConfig

@dataclass
class ActiveSession:
    session_id: str
    config: SessionConfig
    engine: SessionEngine
    bus: EventBus
    task: asyncio.Task           # the @work coroutine running engine.run()
    sse_queues: list[asyncio.Queue] = field(default_factory=list)

class SessionManager:
    def __init__(self):
        self._sessions: dict[str, ActiveSession] = {}

    def start(self, config: SessionConfig) -> ActiveSession: ...
    def get(self, session_id: str) -> ActiveSession | None: ...
    def end(self, session_id: str) -> None: ...
    def add_sse_subscriber(self, session_id: str) -> asyncio.Queue: ...
    def remove_sse_subscriber(self, session_id: str, queue: asyncio.Queue) -> None: ...

session_manager = SessionManager()
```

### 3.2 REST Endpoints

```python
# src/web/api.py
router = APIRouter(prefix="/api")

# Templates
GET  /api/templates                  → list[TemplateSummary]
GET  /api/templates/{slug}           → SessionConfigOut (full YAML parsed)
POST /api/templates                  → TemplateSummary  (write YAML to disk)
PUT  /api/templates/{slug}           → TemplateSummary  (overwrite)
DELETE /api/templates/{slug}         → 204

# Sessions
POST /api/sessions                   → SessionStarted { session_id }
GET  /api/sessions/{id}              → SessionStateOut
POST /api/sessions/{id}/pause        → 200
POST /api/sessions/{id}/resume       → 200
POST /api/sessions/{id}/inject       → 200  (body: { text, channel_id })
POST /api/sessions/{id}/end          → 200

# Streaming
GET  /api/sessions/{id}/stream       → StreamingResponse (SSE, all events)

# Transcripts
GET  /api/transcripts                → list[TranscriptSummary]
GET  /api/transcripts/{id}           → TranscriptDetail
GET  /api/transcripts/search         → list[TranscriptSummary]  (?q=&type=&from=&to=)
GET  /api/transcripts/{id}/export    → FileResponse  (?format=md|json)
```

### 3.3 SSE Streaming

Every event emitted by `EventBus` is serialized to JSON and pushed to all connected
SSE subscribers.

```python
@router.get("/sessions/{session_id}/stream")
async def session_stream(session_id: str, request: Request):
    session = session_manager.get(session_id)
    if not session:
        raise HTTPException(404)

    queue = session_manager.add_sse_subscriber(session_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {event}\n\n"   # event is already model_dump_json()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"       # prevent proxy timeout
        finally:
            session_manager.remove_sse_subscriber(session_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

**Fan-out:** When a session starts, an EventBus subscription serializes every event
to `model_dump_json()` and puts it into each SSE subscriber's queue:

```python
session.bus.stream().subscribe(
    lambda e: [q.put_nowait(e.model_dump_json()) for q in session.sse_queues]
)
```

---

## 4. Frontend Design

### 4.1 Theme and Design Tokens (`src/theme.ts`)

```typescript
import { createTheme } from '@mui/material/styles';

export const AGENT_COLORS = [
  '#4fc3f7',  // 0 light blue
  '#81c784',  // 1 green
  '#ffb74d',  // 2 orange
  '#f06292',  // 3 pink
  '#ba68c8',  // 4 purple
  '#4db6ac',  // 5 teal
];

export const agentColor = (index: number) => AGENT_COLORS[index % AGENT_COLORS.length];

export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#4fc3f7' },
    background: { default: '#0d1117', paper: '#161b22' },
  },
  components: {
    // ... MUI overrides
  },
});

// Semantic tokens as CSS variables (injected via GlobalStyles)
export const semanticTokens = {
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
```

### 4.2 TypeScript Event Types (`src/types/events.ts`)

Mirrors the Python discriminated union exactly — one interface per event type.

```typescript
interface BaseEvent {
  timestamp: string;   // ISO
  turn_number: number;
  session_id: string;
}

export interface MessageEvent extends BaseEvent {
  type: 'MESSAGE';
  agent_id: string;
  agent_name: string;
  model: string;
  channel_id: string;
  recipient_id?: string;
  text: string;
  is_parallel: boolean;
}

export interface MonologueEvent extends BaseEvent {
  type: 'MONOLOGUE';
  agent_id: string;
  agent_name: string;
  text: string;
}

export interface TurnEvent extends BaseEvent {
  type: 'TURN';
  agent_ids: string[];
  is_parallel: boolean;
}

export interface GameStateEvent extends BaseEvent {
  type: 'GAME_STATE';
  updates: Record<string, unknown>;
  full_state: Record<string, unknown>;
}

export interface RuleViolationEvent extends BaseEvent {
  type: 'RULE_VIOLATION';
  agent_id: string;
  rule: string;
  violation_text: string;
}

export interface ChannelCreatedEvent {
  type: 'CHANNEL_CREATED';
  timestamp: string;
  session_id: string;
  channel_id: string;
  channel_type: 'public' | 'team' | 'private';
  members: string[];
}

export interface SessionEndEvent extends BaseEvent {
  type: 'SESSION_END';
  reason: 'max_turns' | 'win_condition' | 'completion_signal' | 'user_ended' | 'error';
  message?: string;
}

export type SessionEvent =
  | MessageEvent | MonologueEvent | TurnEvent | GameStateEvent
  | RuleViolationEvent | ChannelCreatedEvent | SessionEndEvent;
```

---

## 5. Page Designs

### 5.1 LibraryPage

**URL:** `/`
**Purpose:** Home screen — browse and launch session templates.

**Layout:**
```
┌─────────────────────────────────────────────────────────────────────┐
│ 🧠 one-0-one     [Search templates...]              [+ New Template] │
├─────────────────────────────────────────────────────────────────────┤
│ All · Games · Social · Research · Task · Problem-Solve              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
│  │ 20 Questions  🎲  │  │ AI Opinions   💬  │  │ Climate...  📊 │  │
│  │ Classic 20Q game  │  │ Free-form disc... │  │ Multi-model... │  │
│  │ 3 agents  No HITL │  │ 3 agents  No HITL │  │ 4 agents  HITL │  │
│  │      [Launch ▶]   │  │      [Launch ▶]   │  │   [Launch ▶]   │  │
│  └───────────────────┘  └───────────────────┘  └────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Components:**
- `Tabs` — type filter (All / Games / Social / Research / Task / Problem-Solve)
- `TextField` — live search, debounced 300ms, filters by title+description
- `Card` per template — title, description (2-line clamp), `TypeChip`, agent count, HITL badge, Launch button
- FAB `+` — opens `WizardPage` with blank config

**Data:** `useTemplates()` hook → `GET /api/templates`

**URL state:** `?type=games&q=debate` — type tab and search text in query params

### 5.2 WizardPage

**URL:** `/wizard?template={slug}` (edit) or `/wizard` (new)
**Purpose:** Create or modify a session config.

**Layout (Stepper):**
```
Topic → Setting → Agents → Orchestrator → HITL → Review
  ✓         ✓       ✓           ○             ○       ○
```

**Tabs:**

| Step | Components | Notes |
|------|-----------|-------|
| Topic | `TextField` (title), `TextField multiline` (topic) | Required before advancing |
| Setting | `ToggleButtonGroup` (social / research / game / task / problem-solve) | Auto-populates next steps |
| Agents | `DataGrid` + Add/Edit/Delete | Each row: provider, model, name, persona, role, monologue toggle, color preview |
| Orchestrator | `RadioGroup` (python/llm), `Select` (module or provider+model), `TextField` (persona) | python=basic default |
| HITL | `Switch` (enabled), `TextField` (role) | Role input conditionally visible |
| Review | Summary cards for all steps | Validation errors highlighted |

**Actions:**
- `Save template` → `POST /api/templates` → snackbar → redirect to library
- `Run session` → save if needed → `POST /api/sessions` → redirect to `/sessions/{id}`

### 5.3 LiveChatPage

**URL:** `/sessions/{id}`
**Purpose:** Primary operational surface for a running session.

**Layout:**
```
┌──────┬──────────────────────────────────────────────┬──────────┐
│ Nav  │                                              │  Mono-   │
│ Rail │  [Public] [Team: Red] [Private]    👁 🧠 ⏸  │  logue   │
│      ├──────────────────────────────────────────────┤  Drawer  │
│      │                                              │          │
│      │  Nova: I believe the strongest argument...  │  Nova —  │
│      │                                              │  thinking│
│      │  Rex: That premise conflates legal...        │          │
│      │                                              │  I should│
│      │  🔒 Nova → Rex   (private bg)               │  pivot to│
│      │  Don't counter me on the ethics point.      │  economic│
│      │                                              │  angle...│
│      │  ──── [system] Rule violation: Turn 4 ────  │          │
│      ├──────────────────────────────────────────────┤──────────┤
│      │  [Public ▾]  Your message...         [Send] │          │
│      ├──────────────────────────────────────────────┤ Name  St │
│      │ Turn 7/20 ████████░░░░░░░ Nova (thinking...) │ Nova  💭 │
└──────┴──────────────────────────────────────────────┴──────────┘
```

**Regions:**

| Region | Component | Description |
|--------|-----------|-------------|
| Channel tabs | `ChannelTabs` | Tabs + per-tab message list |
| Toolbar | inline | Observer badge, 🧠 toggle, pause, end |
| Monologue drawer | `MonologueDrawer` | Right `<Drawer>`, clears on TURN |
| Agent roster | `AgentRoster` | Collapses to icon-only when drawer open |
| Turn indicator | `TurnIndicator` | Linear progress + current agent name |
| HITL input | `HITLInputBar` | Shown on human's turn only |

**Data flow:**
- Session state: `useSessions(id)` hook → `GET /api/sessions/{id}` (initial load)
- Live events: `useSessionStream(id)` hook → `EventSource /api/sessions/{id}/stream`
- All events dispatched to a local reducer that updates chat logs, monologue, agent status

### 5.4 TranscriptBrowserPage

**URL:** `/transcripts`
**Purpose:** Browse and search saved session transcripts.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│ Transcripts            [🔍 Search...]  [Type ▾] [Date range ▾] │
├─────────────────────────────────────────────────────┤
│ Title              Type    Date        Agents  Turns │
│ ─────────────────────────────────────────────────── │
│ 20 Questions       🎲      2026-02-22  3       18    │
│ AI Ethics Debate   💬      2026-02-21  2       24    │
│ Climate Research   📊      2026-02-20  4       20    │
└─────────────────────────────────────────────────────┘
```

**Components:**
- `DataGrid` — sortable columns, server-side pagination (max 50 rows client-side)
- `TextField` — full-text search, debounced 300ms
- `Select` — type filter
- `DateRangePicker` — date range filter
- Row click → `TranscriptReaderPage`

**URL state:** `?q=&type=&from=&to=&page=&sort=` — all filters in URL

### 5.5 TranscriptReaderPage

**URL:** `/transcripts/{id}`
**Purpose:** Read a saved transcript with speaker formatting.

**Layout:**
```
┌────────────────────────────────────────────────────────────┐
│ ← Back    20 Questions — 2026-02-22      [⬇ md] [⬇ json]  │
├──────────────────────────────────────┬─────────────────────┤
│                                      │ Agents              │
│  Turn 1 — Sphinx [public]            │ ────────────────    │
│  I'm thinking of something in the   │ Sphinx  Anthropic   │
│  "animal" category...                │ Sherlock  OpenAI    │
│                                      │ Watson  Google      │
│  Turn 1 — Sphinx [thinking]          │                     │
│  > The user said it should be        │ Duration: 4m 32s    │
│    challenging...                    │ Turns: 18           │
│                                      │ Setting: game       │
│  Turn 2 — Sherlock [public]          │                     │
│  Is it a mammal?                     │                     │
└──────────────────────────────────────┴─────────────────────┘
```

**Components:**
- Prose reader — renders transcript markdown with per-speaker color
- Metadata sidebar — agents, models, date, duration, turn count
- Export buttons — `GET /api/transcripts/{id}/export?format=md`

---

## 6. Key React Hooks

### 6.1 `useSessionStream`

Subscribes to the SSE stream and dispatches events to a local reducer.

```typescript
// src/hooks/useSessionStream.ts
export function useSessionStream(sessionId: string) {
  const [events, dispatch] = useReducer(sessionEventReducer, initialState);

  useEffect(() => {
    const source = new EventSource(`/api/sessions/${sessionId}/stream`);

    source.onmessage = (e) => {
      const event: SessionEvent = JSON.parse(e.data);
      dispatch({ type: 'EVENT', payload: event });
    };

    source.onerror = () => {
      source.close();
    };

    return () => source.close();
  }, [sessionId]);

  return events;
}
```

### 6.2 Session Event Reducer

Processes each event type into the chat/monologue/state slices:

```typescript
function sessionEventReducer(state: SessionViewState, action: Action): SessionViewState {
  if (action.type !== 'EVENT') return state;
  const event = action.payload;

  switch (event.type) {
    case 'CHANNEL_CREATED':
      return addChannel(state, event);

    case 'TURN':
      return {
        ...state,
        currentTurn: event.turn_number,
        activeAgents: event.agent_ids,
        monologue: '',         // clear monologue on every new turn
        monologueAgent: event.agent_ids[0] ?? '',
      };

    case 'MESSAGE':
      return appendMessage(state, event);   // routed to correct channel bucket

    case 'MONOLOGUE':
      return {
        ...state,
        monologue: state.monologue + event.text,
        monologueAgent: event.agent_name,
      };

    case 'GAME_STATE':
      return { ...state, gameState: event.full_state };

    case 'RULE_VIOLATION':
      return appendSystemMessage(state, event);

    case 'SESSION_END':
      return { ...state, ended: true, endReason: event.reason };

    default:
      return state;
  }
}
```

### 6.3 `useTemplates`

```typescript
// src/hooks/useTemplates.ts
export function useTemplates(filter?: { type?: string; q?: string }) {
  return useQuery({
    queryKey: ['templates', filter],
    queryFn: () => api.get<TemplateSummary[]>('/api/templates', { params: filter }),
    staleTime: 30_000,
  });
}

export function useSaveTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (config: SessionConfigIn) => api.post('/api/templates', config),
    onSuccess: () => client.invalidateQueries({ queryKey: ['templates'] }),
  });
}
```

---

## 7. Component Details

### 7.1 `ChannelTabs`

```typescript
// Tabs bar across top; message list per tab; unread badges
function ChannelTabs({ channels, messages, viewerRole }: ChannelTabsProps) {
  const [activeTab, setActiveTab] = useState('public');
  const [unread, setUnread] = useState<Record<string, number>>({});

  const visibleChannels = channels.filter(ch =>
    isChannelVisible(ch, viewerRole)
  );

  // Track unread counts on inactive tabs
  useEffect(() => {
    messages.forEach(msg => {
      if (msg.channel_id !== activeTab) {
        setUnread(u => ({ ...u, [msg.channel_id]: (u[msg.channel_id] ?? 0) + 1 }));
      }
    });
  }, [messages]);

  return (
    <>
      <Tabs value={activeTab} onChange={(_, v) => { setActiveTab(v); clearUnread(v); }}>
        {visibleChannels.map(ch => (
          <Tab
            key={ch.id}
            value={ch.id}
            label={<TabLabel channel={ch} unread={unread[ch.id] ?? 0} />}
          />
        ))}
      </Tabs>
      {visibleChannels.map(ch => (
        <TabPanel key={ch.id} value={activeTab} channelId={ch.id}>
          <MessageList
            messages={messages.filter(m => m.channel_id === ch.id)}
          />
        </TabPanel>
      ))}
    </>
  );
}
```

### 7.2 `ChatMessage`

Renders a single message with full visual treatment per channel type.

```typescript
function ChatMessage({ event, agentIndex }: { event: MessageEvent; agentIndex: number }) {
  const color = agentColor(agentIndex);
  const isPrivate = event.recipient_id != null;
  const isSystem = event.agent_id === 'system';

  if (isSystem) {
    return (
      <Box sx={{ color: 'var(--orchestrator-color)', fontStyle: 'italic',
                 fontSize: '0.85rem', py: 0.5, borderBottom: '1px solid #333' }}>
        — {event.text} —
      </Box>
    );
  }

  return (
    <Box sx={{
      mb: 1.5,
      ...(isPrivate && {
        background: 'var(--private-bg)',
        borderLeft: '3px solid var(--private-border)',
        pl: 1.5, py: 0.5,
      }),
    }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.25 }}>
        {isPrivate && <LockIcon sx={{ fontSize: 14, color: 'var(--private-border)' }} />}
        <Typography variant="caption" sx={{ color, fontWeight: 700 }}>
          {event.agent_name}
          {isPrivate && ` → ${event.recipient_id}`}
        </Typography>
        {event.is_parallel && (
          <Chip label="parallel" size="small" sx={{ height: 16, fontSize: 10 }} />
        )}
      </Box>
      <Typography variant="body2" sx={{ pl: 0 }}>
        {event.text}
      </Typography>
    </Box>
  );
}
```

### 7.3 `MonologueDrawer`

```typescript
function MonologueDrawer({ open, monologue, agentName, onClose }: MonologueDrawerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as new tokens arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [monologue]);

  return (
    <Drawer
      anchor="right"
      variant="persistent"
      open={open}
      sx={{ width: 320, '& .MuiDrawer-paper': { width: 320 } }}
    >
      <Box sx={{ p: 1.5, borderBottom: '1px solid #333', display: 'flex',
                 alignItems: 'center', gap: 1 }}>
        <PsychologyIcon sx={{ color: 'var(--monologue-text)' }} />
        <Typography variant="caption" sx={{ color: 'var(--monologue-text)' }}>
          {agentName} — thinking…
        </Typography>
        <CircularProgress size={12} sx={{ ml: 'auto' }} />
      </Box>
      <Box
        ref={scrollRef}
        sx={{
          flex: 1, overflow: 'auto',
          background: 'var(--monologue-bg)',
          p: 1.5,
        }}
      >
        <Typography
          variant="body2"
          sx={{ color: 'var(--monologue-text)', whiteSpace: 'pre-wrap',
                fontFamily: 'monospace', fontSize: '0.8rem' }}
        >
          {monologue}
        </Typography>
      </Box>
    </Drawer>
  );
}
```

**Key constraint:** `MonologueDrawer` receives only the `monologue` string from the
reducer — it never receives `MessageEvent` data. It clears (resets to `''`) whenever
a `TurnEvent` fires, enforced in the reducer.

### 7.4 `AgentRoster`

Collapses to icon-only width when Monologue Drawer is open.

```typescript
function AgentRoster({ agents, agentStates, drawerOpen }: AgentRosterProps) {
  const collapsed = drawerOpen;

  return (
    <Box sx={{ width: collapsed ? 48 : 200, transition: 'width 0.2s',
               borderLeft: '1px solid #333', overflow: 'hidden' }}>
      {agents.map((agent, i) => {
        const color = agentColor(i);
        const state = agentStates[agent.id];
        return collapsed ? (
          <Tooltip key={agent.id} title={agent.name} placement="left">
            <Avatar sx={{ bgcolor: color, width: 32, height: 32, m: 0.5,
                          fontSize: 14 }}>
              {agent.name[0]}
            </Avatar>
          </Tooltip>
        ) : (
          <Box key={agent.id} sx={{ display: 'flex', alignItems: 'center',
                                     gap: 1, px: 1, py: 0.75 }}>
            <Avatar sx={{ bgcolor: color, width: 28, height: 28, fontSize: 12 }}>
              {agent.name[0]}
            </Avatar>
            <Box sx={{ flex: 1, overflow: 'hidden' }}>
              <Typography variant="caption" sx={{ color, fontWeight: 600 }} noWrap>
                {agent.name}
              </Typography>
              <Typography variant="caption" sx={{ color: '#888', display: 'block' }} noWrap>
                {agent.role}
              </Typography>
            </Box>
            <AgentStatusIcon status={state?.status} />
          </Box>
        );
      })}
    </Box>
  );
}
```

---

## 8. Navigation and URL Design

```
/                               → LibraryPage      (?type=&q=)
/wizard                         → WizardPage       (new session)
/wizard?template={slug}         → WizardPage       (edit existing)
/sessions/{id}                  → LiveChatPage
/transcripts                    → TranscriptBrowserPage  (?q=&type=&from=&to=&page=&sort=)
/transcripts/{id}               → TranscriptReaderPage
/settings                       → SettingsPage
```

**All filter/pagination state in URL query params** — browser back/forward and
shareable links work for every view.

---

## 9. TUI Parity Checklist

The web UI must maintain feature parity with the TUI on all core workflows:

| Feature | TUI | Web UI |
|---------|-----|--------|
| Template browser | `ListView` with type filter `Tabs` | Card grid with type filter `Tabs` |
| Session wizard | 5-tab `SetupWizardScreen` | 6-step `Stepper` (+ Review step) |
| Launch session | `R` key | Launch button → redirect to `/sessions/{id}` |
| Channel tabs | `ChannelTabs` widget | `Tabs` + per-tab message list |
| Monologue | `MonologuePanel` bottom strip | `MonologueDrawer` right panel |
| Private messages | `🔒` glyph + dimmed | `--private-bg` + lock icon + recipient |
| Observer badge | status indicator in sidebar | `👁 Observing` chip in toolbar |
| Pause / resume | `P` key | Toolbar button |
| Inject message | `I` key | Inject button + dialog |
| End session | `E` key | End button + confirmation dialog |
| Transcript auto-save | Automatic | Automatic (engine-level) |
| Transcript export | `TranscriptModal` | Download buttons in reader |

---

## 10. Violations to Prevent

| Violation | Prevention |
|-----------|-----------|
| Frozen chat | All messages stream via SSE; partial text displayed as tokens arrive |
| Monologue in chat | Reducer routes `MONOLOGUE` events only to `monologue` state slice — `ChannelTabs` only renders `MessageEvent` data |
| Stale monologue | Reducer clears `monologue: ''` on every `TURN` event |
| Channel bleed | `ChannelTabs` filters messages by `channel_id` === current tab; no cross-rendering |
| Observer blindness | Tab visibility computed from `isObserver` flag + team membership in channel config |
| Silent private | `ChatMessage` always applies `--private-bg`, lock icon, and `→ Recipient` when `recipient_id` is set |
| Drawer collision | `AgentRoster` receives `drawerOpen` prop and collapses to icon-only width |
| URL amnesia | All filters use `useSearchParams` — state lives in URL |
| Lost scroll | Auto-scroll paused on scroll-up; resumed on scroll-to-bottom |
| Modal abuse | Snackbar for saves/confirms; Dialog only for destructive (end session, delete template) |
