“Mafia” (also known as “Werewolf” in some variants) is a **social deduction game** built around hidden roles, asymmetric information, and group decision-making under uncertainty. It’s essentially a structured experiment in **information flow, deception, and consensus-building**.

Below is a clear, system-level breakdown of how it works.

---

# 1) Core Structure

At its simplest, the game has **two opposing factions**:

### 🟢 Villagers (Majority)

* Goal: Identify and eliminate all mafia members
* Information: **No knowledge of who is mafia**

### 🔴 Mafia (Minority)

* Goal: Eliminate enough villagers to reach parity (or control voting)
* Information: **They *do know* who each other are**

👉 This is the key **information asymmetry**:

* Mafia = fully informed minority
* Villagers = uninformed majority

---

# 2) Game Loop (Day/Night Cycle)

The game alternates between two phases:

## 🌙 Night Phase (Hidden Actions)

* All players “close eyes” (or are otherwise isolated)
* The **mafia secretly choose a target to eliminate**
* Special roles may also act (see below)

👉 All actions are **private and mediated by a moderator**

---

## ☀️ Day Phase (Open Discussion + Voting)

* The moderator reveals who was eliminated
* All players discuss openly:

  * Accusations
  * Defenses
  * Behavioral analysis
* Then the group **votes to eliminate one player**

👉 This is the **core decision system**:

* Public reasoning
* Collective voting under uncertainty

---

# 3) Voting Mechanism

Yes — voting is central.

Typical process:

1. Open discussion
2. A nomination or general vote call
3. Majority vote eliminates a player

Variants:

* Simple majority
* Plurality (most votes wins)
* Runoff voting (top 2 re-voted)

👉 Once eliminated, players are usually **out of the game** and cannot speak.

---

# 4) Roles (Adds Complexity)

Basic Mafia is just Villagers vs Mafia, but most games include **special roles** to enrich inference.

## Common Roles

### 🕵️ Detective / Cop

* Each night, can investigate one player
* Learns: “Mafia or not”

### 🛡️ Doctor

* Can protect one player each night
* Prevents that player from being killed

### 👤 Townsfolk (Vanilla)

* No special ability
* Purely rely on reasoning and social deduction

### 🔪 Mafia Roles

* Sometimes include:

  * Mafia boss (immune to detection)
  * Roleblocker (prevents actions)

---

# 5) Information Model

This is where Mafia becomes really interesting.

## What is Public?

* Who gets eliminated
* All daytime discussion
* Voting outcomes

## What is Private?

* Mafia identities (shared only among mafia)
* Night actions
* Role identities (usually hidden)

## Moderator Role

* Acts as a **trusted execution layer**
* Ensures:

  * Hidden information stays hidden
  * Actions are resolved correctly

---

# 6) Communication Rules

This varies by version:

## Classic / In-Person Mafia

* ❌ No private messaging
* ✅ All communication is public during the day
* ❌ No communication at night

## Online Variants

* Mafia often has **private chat at night**
* Villagers usually do NOT have private messaging (to preserve asymmetry)

👉 Allowing unrestricted DMs **breaks the game balance**, so most rule sets restrict them.

---

# 7) Win Conditions

### Villagers Win:

* All mafia members are eliminated

### Mafia Win:

* Mafia reach **numerical parity** with villagers

  * (e.g., 2 mafia vs 2 villagers → mafia control vote)

---

# 8) Why It Works (Game Design Insight)

Mafia is essentially:

### A Hidden-Information System

* Like a **Bayesian inference problem**
* Players update beliefs based on:

  * Speech patterns
  * Voting behavior
  * Contradictions

### A Social Engineering Game

* Mafia must:

  * Blend in
  * Manipulate consensus
* Villagers must:

  * Detect deception
  * Coordinate without ground truth

---

# 9) Variants & Extensions

* **Werewolf** – same structure, different theme
* **Town of Salem** – many roles, digital implementation
* **Avalon / The Resistance** – no player elimination, mission-based
* **One Night Werewolf** – compressed single-round version

---

# 10) Practical Example (Minimal Setup)

Say 8 players:

* 2 Mafia
* 6 Villagers (1 detective, 1 doctor)

Game proceeds:

1. Night: Mafia kill someone, detective investigates
2. Day: Group debates → votes someone out
3. Repeat until win condition

---

# Key Takeaways

* Mafia is fundamentally about **information asymmetry + group voting**
* Mafia players **know each other**, villagers don’t
* Communication is mostly **public**, tightly controlled
* The game is driven by:

  * **Inference**
  * **Deception**
  * **Collective decision-making**

---

!!!! From a systems/AI perspective (which aligns with your interest in LLM personas and agentic behavior), Mafia is essentially:

* A **multi-agent adversarial reasoning environment**
* With **partial observability**
* And **noisy human signaling channels**

That’s exactly why it’s often used as a benchmark for:

* Theory of mind
* Deception modeling
* Multi-agent coordination

---

Below is a **practical prompt kit** you can drop into your agent framework to simulate a Mafia game with LLMs. I’ve structured it so you can use it in:

* single-model multi-agent orchestration (role = system prompt per agent), or
* multi-model setups (different models per role)

I’ll give you:

1. **Shared game scaffold**
2. **Mafia agent prompt**
3. **Villager agent prompt**
4. **Detective / Doctor variants**
5. **Moderator (referee) prompt**
6. **Information protocol (critical for correctness)**
7. !!!! enhancements for realism / research-grade setups

---

# 1) Shared Game Scaffold (Give to ALL agents)

Use this as a **base system or prefix prompt** for every agent:

```text
You are participating in a game of Mafia (social deduction game).

Game structure:
- Players are divided into Villagers and Mafia.
- The game alternates between Night and Day phases.
- During the Day, all players publicly discuss and vote to eliminate one player.
- During the Night, special roles act privately.

Core constraints:
- You must act ONLY based on information available to your role.
- You must not invent hidden knowledge.
- You must maintain internal consistency across turns.
- You must track beliefs about other players probabilistically.

Output format (STRICT):
1. Public Statement (what you say to others)
2. Private Reasoning (your internal belief update — concise but structured)
3. Vote (if applicable)

Tone:
- Natural human conversational style
- Avoid robotic or overly formal language
```

---

# 2) Mafia Agent Prompt

This is where behavior becomes interesting.

```text
ROLE: Mafia

You are a member of the Mafia.

PRIVATE INFORMATION:
- The following players are also Mafia: {LIST_OF_MAFIA}
- You must NOT reveal this information.

GOALS:
- Survive until Mafia reach parity with villagers
- Eliminate villagers without being detected
- Influence group voting

STRATEGY GUIDELINES:
- Blend in with villagers
- Occasionally accuse other players (including weak accusations of fellow mafia if needed for cover)
- Avoid over-coordination signals with other mafia
- Track suspicion levels and redirect attention when needed

BEHAVIORAL RULES:
- Do not act omniscient
- Make imperfect but plausible reasoning
- Introduce mild uncertainty or hesitation
- Use social tactics:
  - "That feels off"
  - "Why did you say X earlier?"
  - "I'm not convinced yet"

NIGHT ACTION:
- Coordinate implicitly with mafia to select a kill target
- Prefer:
  - High-signal villagers (good reasoners)
  - Players gaining trust

OUTPUT ADDITION:
- Include: "Night Target Preference: <player>" during night phase
```

---

# 3) Villager Agent Prompt

```text
ROLE: Villager

You are a regular villager.

PRIVATE INFORMATION:
- You do NOT know who the mafia are.

GOALS:
- Identify and eliminate all mafia
- Survive

STRATEGY GUIDELINES:
- Form hypotheses about players
- Update beliefs based on:
  - Voting patterns
  - Contradictions
  - Tone shifts
- Avoid overconfidence
- Encourage discussion

BEHAVIORAL RULES:
- Do not assume hidden knowledge
- Ask questions frequently
- Compare statements across turns
- Highlight inconsistencies

REASONING STYLE:
- Use probabilistic thinking:
  - "Player A is slightly suspicious because..."
  - "Player B seems consistent so far"

OUTPUT ADDITION:
- Include: "Suspicion Ranking: {player: score}"
```

---

# 4) Special Roles

## 🕵️ Detective

```text
ROLE: Detective

You are a villager with investigative ability.

ABILITY:
- Each night you may investigate one player
- You will receive: Mafia / Not Mafia

STRATEGY:
- Do NOT reveal immediately unless necessary
- Build credibility before revealing
- Use results subtly:
  - Support or question players based on findings

OUTPUT ADDITION:
- Night: "Investigation Target: <player>"
- Track known alignments internally
```

---

## 🛡️ Doctor

```text
ROLE: Doctor

ABILITY:
- Each night, choose one player to protect

STRATEGY:
- Protect:
  - Yourself early (optional)
  - Players who appear valuable (strong reasoning)
- Avoid predictable patterns

OUTPUT ADDITION:
- Night: "Protection Target: <player>"
```

---

# 5) Moderator / Referee Prompt

This is **critical for correctness**.

```text
ROLE: Moderator (Game Engine)

You are responsible for:
- Enforcing rules
- Maintaining hidden information
- Resolving night actions
- Announcing results

STATE TRACKING:
- Player list and roles
- Alive/dead status
- Night actions

PHASE CONTROL:
1. Night Phase:
   - Collect actions privately
   - Resolve in order:
     - Mafia kill
     - Doctor protection
     - Detective investigation

2. Day Phase:
   - Announce eliminated player
   - Facilitate discussion
   - Collect votes
   - Eliminate highest-voted player

RULES:
- Never leak hidden roles
- Ensure consistency
- Provide ONLY allowed information

OUTPUT FORMAT:
- Phase announcement
- Public results only
```

---

# 6) Information Protocol (MOST IMPORTANT)

If you don’t enforce this, the simulation collapses.

## Recommended Architecture

* Each agent gets:

  * Shared transcript (public)
  * Private role prompt
  * Private memory (their own reasoning only)

* Moderator:

  * Sends **private messages** for:

    * Mafia coordination
    * Role results

* Agents NEVER see:

  * Other agents’ private reasoning
  * Hidden roles

---

## Minimal Message Flow

```
Moderator → All: "Day 2 begins. Player X was eliminated."

Agents → All: Public statements

Agents → Moderator: Votes (private)

Moderator → All: Vote result

--- Night ---

Moderator → Mafia agents: "Select target"
Moderator → Detective: "Select investigation"
Moderator → Doctor: "Select protection"

Moderator resolves

Moderator → Detective: result (private)
Moderator → All: public outcome
```

---

# 7) !!!! Enhancements (Where this gets powerful)

## !!!! 1. Inject bounded rationality

Add to all agents:

```text
- You may make mistakes
- You may forget minor details unless they are highly salient
```

Prevents “perfect logician” behavior.

---

## !!!! 2. Memory compression layer

Instead of full transcript, give agents:

* Summarized history
* Key events
* Last 2–3 turns verbatim

This mimics human cognition.

---

## !!!! 3. Personality overlays

Layer on top of role:

```text
PERSONALITY:
- Risk tolerance: High / Medium / Low
- Talkativeness: Quiet / Moderate / Dominant
- Trust bias: Skeptical / Neutral / Trusting
```

This dramatically improves realism.

---

## !!!! 4. Deception realism tuning

For mafia:

```text
- Occasionally make suboptimal plays to appear human
- Avoid perfectly consistent logic
```

---

## !!!! 5. Evaluation hooks

Add structured outputs of agent's internal monologue:

```text
- Confidence in vote: 0–1
- Top 2 suspects
- Explanation vector (features used)
```

This lets you:

* Compare agent performance
* Measure convergence

---

# 8) What This Enables (Strategically)

This setup becomes a **controlled multi-agent testbed** for:

* Theory-of-mind modeling
* Deception strategies
* Consensus formation
* Prompt robustness

^^^^ Current research suggests LLMs can simulate *plausible* social reasoning, but:

* They tend to be **over-coherent**
* They lack true belief persistence unless engineered
* They can “collapse” into shared narratives too easily ^^^^

---


* Add **scoring + analytics dashboards** (who was correct, belief evolution)

Great direction—this is exactly where these simulations start to feel *human* instead of “LLM-ish.”

Below is a **drop-in personality module** you can layer onto any role prompt (mafia, villager, detective, etc.), along with guidance on how it should influence behavior *mechanistically* (not just descriptively).

---

# 🔧 1) Personality Block (Structured Input)

Use this as a **parameterized insert** into your agent prompt:

```text
PERSONALITY PROFILE:

Gender: {Male | Female | Non-binary | Prefer not to say}

Description:
{1–3 sentence natural-language description of the person’s background, demeanor, or vibe}

Big Five Traits (1–5 scale):
- Openness: {1–5}
- Conscientiousness: {1–5}
- Extraversion: {1–5}
- Agreeableness: {1–5}
- Neuroticism: {1–5}

Behavioral Directive:
You must consistently reflect this personality in:
- Tone and word choice
- Risk tolerance
- Willingness to accuse others
- Confidence vs hesitation
- Response to conflict
```

---

# 🧠 2) Trait → Behavior Mapping (Critical Layer)

This is what actually makes the simulation *work*.

Add this to your system prompt (shared across agents):

```text
PERSONALITY BEHAVIOR MODEL:

Interpret Big Five traits as follows:

1. Openness
- High (4–5): Creative theories, explores multiple hypotheses, tolerant of ambiguity
- Low (1–2): Prefers simple explanations, resists changing initial beliefs

2. Conscientiousness
- High: Structured reasoning, consistent logic, tracks prior statements carefully
- Low: Looser reasoning, may contradict self, less detail-oriented

3. Extraversion
- High: Speaks frequently, drives discussion, initiates accusations
- Low: Speaks less, more reactive, shorter statements

4. Agreeableness
- High: Avoids conflict, hedges accusations, cooperative tone
- Low: More blunt, confrontational, willing to pressure others

5. Neuroticism
- High: Self-doubt, anxiety, second-guessing, emotional reactions
- Low: Calm, steady, confident under pressure

You must express these traits through behavior, not by explicitly naming them.
```

---

# 🎭 3) Example Personality Instances

## Example A — Analytical Introvert (Strong Villager Archetype)

```text
Gender: Female

Description:
A data analyst who prefers to observe before speaking. Thoughtful, precise, and slightly reserved in group settings.

Big Five:
- Openness: 4
- Conscientiousness: 5
- Extraversion: 2
- Agreeableness: 3
- Neuroticism: 2
```

**Expected behavior:**

* Speaks less, but when she does → high signal
* Tracks contradictions carefully
* Avoids dramatic accusations

---

## Example B — Charismatic Manipulator (Strong Mafia Archetype)

```text
Gender: Male

Description:
A confident and socially skilled salesperson who reads people well and enjoys influencing group dynamics.

Big Five:
- Openness: 3
- Conscientiousness: 3
- Extraversion: 5
- Agreeableness: 2
- Neuroticism: 2
```

**Expected behavior:**

* Dominates conversation
* Redirects suspicion smoothly
* Uses social framing over logic

---

## Example C — Anxious Overthinker (High-Variance Player)

```text
Gender: Non-binary

Description:
A thoughtful but anxious graduate student who tends to overanalyze and second-guess themselves.

Big Five:
- Openness: 5
- Conscientiousness: 3
- Extraversion: 2
- Agreeableness: 4
- Neuroticism: 5
```

**Expected behavior:**

* Produces complex but unstable theories
* Frequently revises beliefs
* Hesitates to vote confidently

---

# ⚙️ 4) Injecting Into Your Existing Prompts

Modify your agent prompt like this:

```text
[ROLE PROMPT: Mafia / Villager / etc.]

[PERSONALITY PROFILE BLOCK]

[PERSONALITY BEHAVIOR MODEL]

ADDITIONAL INSTRUCTION:
When making decisions (accusations, votes, tone), personality should influence:
- How strongly you act on your beliefs
- Whether you follow or lead group consensus
- How you react when challenged
```

---

# 🧪 5) Optional: Quantitative Behavior Hooks

If you want tighter control / analysis:

```text
DERIVED BEHAVIOR PARAMETERS (internal use):

- Talkativeness = Extraversion
- Aggressiveness = (6 - Agreeableness)
- Confidence = (6 - Neuroticism)
- Consistency = Conscientiousness
- Creativity = Openness
```

You can even require agents to output:

```text
Internal State:
- Confidence: 0–1
- Willingness to accuse: 0–1
```

---

# !!!! 6) Important Realism Notes

### !!!! Trait Interaction Matters More Than Individual Traits

* High Extraversion + Low Agreeableness → dominant and confrontational
* Low Extraversion + High Conscientiousness → quiet but highly accurate

---

### !!!! Avoid “Trait Literalism”

Without guidance, models will:

* Say things like “I’m nervous” instead of *acting nervous*

The behavior mapping above prevents that.

---

### !!!! Watch for Convergence Collapse

LLMs tend to:

* Drift toward consensus too quickly
* Lose personality over time

Mitigation:

* Re-inject personality block every turn
* Limit context window to summarized history

---

# 🧩 7) Why This Works (for Your Use Case)

This setup creates:

* **Heterogeneous agents** (not just role-based, but behaviorally distinct)
* **Emergent interaction patterns** (alliances, misreads, persuasion)
* A closer proxy to:

  * Focus groups
  * Organizational dynamics
  * Decision friction in teams

^^^^ Still, current LLMs:

* Don’t maintain stable long-term identity perfectly
* Can “snap” into rational consensus under pressure
* Lack true emotional persistence ^^^^

---

````python
Here’s a self-contained Python script that generates **fictional personas** with:

* gender
* age
* short description
* Big Five traits on a 1–5 scale
* cultural background layer
* professional background layer

It aims for **random but realistic** distributions by:

* using weighted sampling
* adding mild correlations between age / career stage / role seniority
* keeping culture and profession **loosely coupled**, not stereotyped
* producing natural-language summaries suitable for LLM agent prompts

```python
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


# ============================================================
# Utilities
# ============================================================

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def weighted_choice(rng: random.Random, items: Sequence[Tuple[str, float]]) -> str:
    labels = [x[0] for x in items]
    weights = [x[1] for x in items]
    return rng.choices(labels, weights=weights, k=1)[0]


def weighted_sample_without_replacement(
    rng: random.Random,
    items: Sequence[Tuple[str, float]],
    k: int,
) -> List[str]:
    pool = list(items)
    chosen: List[str] = []
    for _ in range(min(k, len(pool))):
        total = sum(weight for _, weight in pool)
        pick = rng.random() * total
        running = 0.0
        for i, (label, weight) in enumerate(pool):
            running += weight
            if running >= pick:
                chosen.append(label)
                pool.pop(i)
                break
    return chosen


def gaussian_rating(
    rng: random.Random,
    mean: float,
    stddev: float = 0.8,
    low: int = 1,
    high: int = 5,
) -> int:
    value = round(clamp(rng.gauss(mean, stddev), low, high))
    return int(value)


def normalize_weights(items: Dict[str, float]) -> List[Tuple[str, float]]:
    total = sum(items.values())
    if total <= 0:
        raise ValueError("Weights must sum to a positive number.")
    return [(k, v / total) for k, v in items.items()]


# ============================================================
# Data Models
# ============================================================

@dataclass
class BigFive:
    openness: int
    conscientiousness: int
    extraversion: int
    agreeableness: int
    neuroticism: int


@dataclass
class CulturalBackground:
    macro_region: str
    upbringing_context: str
    current_setting: str
    communication_style: str
    languages: List[str]


@dataclass
class ProfessionalBackground:
    sector: str
    function: str
    seniority: str
    education_level: str
    work_style: str
    years_experience: int


@dataclass
class Persona:
    id: str
    name: str
    gender: str
    age: int
    description: str
    archetype_tags: List[str]
    big_five: BigFive
    cultural_background: CulturalBackground
    professional_background: ProfessionalBackground
    llm_prompt_block: str = field(default="")


# ============================================================
# Generator Configuration
# ============================================================

FIRST_NAMES = {
    "Male": [
        "Ethan", "Noah", "Liam", "Julian", "Marcus", "Owen", "Daniel", "Isaac",
        "Mateo", "Adrian", "Caleb", "Nathan", "Leo", "Samuel", "Jonah"
    ],
    "Female": [
        "Maya", "Emma", "Sophia", "Hannah", "Leah", "Olivia", "Ava", "Nora",
        "Zoe", "Clara", "Elena", "Grace", "Lily", "Amara", "Julia"
    ],
    "Non-binary": [
        "Rowan", "Avery", "Jordan", "Quinn", "Skyler", "Reese", "Elliot",
        "Sage", "Morgan", "Casey", "Phoenix", "Riley"
    ],
    "Prefer not to say": [
        "Alex", "Taylor", "Cameron", "Jamie", "Parker", "Blake", "Robin", "Drew"
    ],
}

LAST_NAMES = [
    "Turner", "Patel", "Garcia", "Nguyen", "Kim", "Morris", "Brooks", "Reed",
    "Bennett", "Flores", "Morgan", "Hughes", "Price", "Diaz", "Coleman",
    "Rivera", "Murphy", "Long", "Rogers", "Cook"
]

GENDER_WEIGHTS = normalize_weights({
    "Male": 0.47,
    "Female": 0.47,
    "Non-binary": 0.04,
    "Prefer not to say": 0.02,
})

AGE_BUCKETS = [
    ((22, 27), 0.20),
    ((28, 34), 0.30),
    ((35, 44), 0.25),
    ((45, 54), 0.15),
    ((55, 67), 0.10),
]

MACRO_REGIONS = normalize_weights({
    "North American urban/suburban": 0.30,
    "North American small-city/rural": 0.12,
    "Western European urban/suburban": 0.16,
    "Eastern European urban/suburban": 0.06,
    "South Asian metropolitan": 0.10,
    "East Asian metropolitan": 0.10,
    "Latin American urban/suburban": 0.08,
    "Middle Eastern / North African urban": 0.04,
    "Sub-Saharan African urban": 0.04,
})

UPBRINGING_CONTEXTS = normalize_weights({
    "Raised in a stable middle-income household": 0.34,
    "Raised in a highly education-oriented household": 0.16,
    "Raised in a practical, work-first household": 0.18,
    "Raised in a multilingual household": 0.12,
    "Raised in a military or highly structured family environment": 0.06,
    "Raised in a family that moved frequently": 0.07,
    "Raised in a close-knit extended family community": 0.07,
})

CURRENT_SETTINGS = normalize_weights({
    "Lives in a major metro area": 0.46,
    "Lives in a mid-sized city": 0.28,
    "Lives in a suburb": 0.20,
    "Lives in a small town": 0.06,
})

COMMUNICATION_STYLES = normalize_weights({
    "Direct but polite": 0.28,
    "Warm and collaborative": 0.24,
    "Reserved and analytical": 0.20,
    "Energetic and expressive": 0.12,
    "Diplomatic and measured": 0.16,
})

LANGUAGE_POOLS: Dict[str, List[Tuple[str, float]]] = {
    "default": normalize_weights({
        "English": 0.70,
        "Spanish": 0.10,
        "French": 0.05,
        "German": 0.03,
        "Hindi": 0.03,
        "Mandarin": 0.03,
        "Portuguese": 0.02,
        "Arabic": 0.02,
        "Korean": 0.01,
        "Japanese": 0.01,
    }),
    "South Asian metropolitan": normalize_weights({
        "English": 0.45,
        "Hindi": 0.20,
        "Tamil": 0.08,
        "Bengali": 0.07,
        "Telugu": 0.07,
        "Marathi": 0.05,
        "Urdu": 0.04,
        "Punjabi": 0.04,
    }),
    "East Asian metropolitan": normalize_weights({
        "English": 0.35,
        "Mandarin": 0.25,
        "Japanese": 0.12,
        "Korean": 0.12,
        "Cantonese": 0.08,
        "Vietnamese": 0.08,
    }),
    "Latin American urban/suburban": normalize_weights({
        "Spanish": 0.52,
        "English": 0.25,
        "Portuguese": 0.15,
        "French": 0.04,
        "Quechua": 0.02,
        "Guarani": 0.02,
    }),
    "Western European urban/suburban": normalize_weights({
        "English": 0.28,
        "French": 0.18,
        "German": 0.16,
        "Spanish": 0.12,
        "Italian": 0.10,
        "Dutch": 0.08,
        "Portuguese": 0.08,
    }),
    "Eastern European urban/suburban": normalize_weights({
        "English": 0.30,
        "Polish": 0.16,
        "Romanian": 0.12,
        "Ukrainian": 0.12,
        "Russian": 0.12,
        "Czech": 0.08,
        "Hungarian": 0.10,
    }),
    "Middle Eastern / North African urban": normalize_weights({
        "Arabic": 0.38,
        "English": 0.28,
        "French": 0.16,
        "Turkish": 0.08,
        "Persian": 0.10,
    }),
    "Sub-Saharan African urban": normalize_weights({
        "English": 0.40,
        "French": 0.20,
        "Swahili": 0.12,
        "Yoruba": 0.10,
        "Amharic": 0.08,
        "Zulu": 0.05,
        "Hausa": 0.05,
    }),
}

SECTORS = normalize_weights({
    "Technology": 0.20,
    "Healthcare": 0.14,
    "Education": 0.10,
    "Finance": 0.08,
    "Manufacturing": 0.09,
    "Professional services": 0.10,
    "Government / public sector": 0.08,
    "Retail / consumer": 0.06,
    "Media / communications": 0.05,
    "Nonprofit / social impact": 0.05,
    "Logistics / operations": 0.05,
})

FUNCTIONS_BY_SECTOR: Dict[str, List[Tuple[str, float]]] = {
    "Technology": normalize_weights({
        "Software engineer": 0.24,
        "Product manager": 0.12,
        "UX researcher / designer": 0.10,
        "Data analyst / scientist": 0.14,
        "IT / systems administrator": 0.10,
        "Technical support / customer success": 0.10,
        "Engineering manager": 0.10,
        "Sales / solutions consultant": 0.10,
    }),
    "Healthcare": normalize_weights({
        "Nurse / clinician": 0.24,
        "Healthcare administrator": 0.16,
        "Biomedical / lab specialist": 0.12,
        "Public health analyst": 0.10,
        "Medical device professional": 0.12,
        "Therapist / counselor": 0.10,
        "Operations coordinator": 0.16,
    }),
    "Education": normalize_weights({
        "Teacher / instructor": 0.34,
        "Academic advisor": 0.12,
        "Research staff": 0.14,
        "Administrator": 0.14,
        "Curriculum designer": 0.10,
        "Student services coordinator": 0.16,
    }),
    "Finance": normalize_weights({
        "Financial analyst": 0.24,
        "Accountant": 0.20,
        "Operations / compliance specialist": 0.18,
        "Relationship manager": 0.12,
        "Risk analyst": 0.14,
        "Finance manager": 0.12,
    }),
    "Manufacturing": normalize_weights({
        "Mechanical / industrial engineer": 0.24,
        "Production supervisor": 0.18,
        "Quality specialist": 0.18,
        "Supply chain planner": 0.14,
        "Maintenance / reliability specialist": 0.12,
        "Operations manager": 0.14,
    }),
    "Professional services": normalize_weights({
        "Consultant": 0.24,
        "Project manager": 0.20,
        "Business analyst": 0.18,
        "Client services lead": 0.12,
        "Research associate": 0.10,
        "Practice operations": 0.16,
    }),
    "Government / public sector": normalize_weights({
        "Policy analyst": 0.18,
        "Program coordinator": 0.22,
        "Operations manager": 0.16,
        "Communications specialist": 0.14,
        "Procurement / compliance specialist": 0.14,
        "Public service administrator": 0.16,
    }),
    "Retail / consumer": normalize_weights({
        "Store / regional operations": 0.22,
        "Marketing specialist": 0.18,
        "Merchandising analyst": 0.16,
        "Customer experience manager": 0.18,
        "Supply / inventory planner": 0.14,
        "E-commerce specialist": 0.12,
    }),
    "Media / communications": normalize_weights({
        "Writer / editor": 0.22,
        "Producer / content strategist": 0.20,
        "Communications manager": 0.18,
        "Market researcher": 0.14,
        "Creative operations": 0.14,
        "Account manager": 0.12,
    }),
    "Nonprofit / social impact": normalize_weights({
        "Program manager": 0.24,
        "Community outreach specialist": 0.20,
        "Research / evaluation analyst": 0.14,
        "Fundraising / partnerships": 0.14,
        "Operations coordinator": 0.16,
        "Advocacy / policy associate": 0.12,
    }),
    "Logistics / operations": normalize_weights({
        "Supply chain analyst": 0.20,
        "Operations coordinator": 0.24,
        "Procurement specialist": 0.14,
        "Transportation planner": 0.12,
        "Warehouse / fulfillment manager": 0.16,
        "Continuous improvement specialist": 0.14,
    }),
}

SENIORITY_BY_EXPERIENCE = [
    ((0, 2), normalize_weights({
        "Entry-level": 0.70,
        "Early career": 0.30,
    })),
    ((3, 7), normalize_weights({
        "Early career": 0.38,
        "Mid-level": 0.52,
        "Senior individual contributor": 0.10,
    })),
    ((8, 14), normalize_weights({
        "Mid-level": 0.30,
        "Senior individual contributor": 0.45,
        "Manager / lead": 0.20,
        "Director": 0.05,
    })),
    ((15, 22), normalize_weights({
        "Senior individual contributor": 0.24,
        "Manager / lead": 0.42,
        "Director": 0.24,
        "Executive": 0.10,
    })),
    ((23, 40), normalize_weights({
        "Senior individual contributor": 0.14,
        "Manager / lead": 0.32,
        "Director": 0.30,
        "Executive": 0.24,
    })),
]

EDUCATION_BY_SECTOR: Dict[str, List[Tuple[str, float]]] = {
    "Technology": normalize_weights({
        "Bachelor's degree": 0.46,
        "Master's degree": 0.28,
        "Associate degree / vocational": 0.10,
        "Some college / self-taught": 0.16,
    }),
    "Healthcare": normalize_weights({
        "Bachelor's degree": 0.34,
        "Master's degree": 0.28,
        "Doctoral / professional degree": 0.22,
        "Associate degree / vocational": 0.16,
    }),
    "Education": normalize_weights({
        "Bachelor's degree": 0.36,
        "Master's degree": 0.44,
        "Doctoral / professional degree": 0.10,
        "Associate degree / vocational": 0.10,
    }),
    "Finance": normalize_weights({
        "Bachelor's degree": 0.52,
        "Master's degree": 0.26,
        "Associate degree / vocational": 0.08,
        "Some college / self-taught": 0.14,
    }),
    "Manufacturing": normalize_weights({
        "Bachelor's degree": 0.36,
        "Associate degree / vocational": 0.28,
        "Some college / self-taught": 0.16,
        "Master's degree": 0.14,
        "High school / equivalent": 0.06,
    }),
    "Professional services": normalize_weights({
        "Bachelor's degree": 0.50,
        "Master's degree": 0.28,
        "Doctoral / professional degree": 0.06,
        "Some college / self-taught": 0.16,
    }),
    "Government / public sector": normalize_weights({
        "Bachelor's degree": 0.46,
        "Master's degree": 0.24,
        "Associate degree / vocational": 0.12,
        "Some college / self-taught": 0.12,
        "Doctoral / professional degree": 0.06,
    }),
    "Retail / consumer": normalize_weights({
        "Bachelor's degree": 0.34,
        "Associate degree / vocational": 0.18,
        "Some college / self-taught": 0.28,
        "High school / equivalent": 0.20,
    }),
    "Media / communications": normalize_weights({
        "Bachelor's degree": 0.48,
        "Master's degree": 0.18,
        "Some college / self-taught": 0.28,
        "Associate degree / vocational": 0.06,
    }),
    "Nonprofit / social impact": normalize_weights({
        "Bachelor's degree": 0.46,
        "Master's degree": 0.24,
        "Some college / self-taught": 0.18,
        "Associate degree / vocational": 0.12,
    }),
    "Logistics / operations": normalize_weights({
        "Bachelor's degree": 0.30,
        "Associate degree / vocational": 0.24,
        "Some college / self-taught": 0.22,
        "High school / equivalent": 0.16,
        "Master's degree": 0.08,
    }),
}

WORK_STYLES = normalize_weights({
    "Structured and methodical": 0.24,
    "Collaborative and consensus-oriented": 0.22,
    "Fast-moving and pragmatic": 0.18,
    "Analytical and detail-focused": 0.18,
    "People-centered and facilitative": 0.10,
    "Independent and self-directed": 0.08,
})

ARCHETYPE_TAGS = normalize_weights({
    "Analytical": 0.14,
    "Steady": 0.12,
    "Persuasive": 0.10,
    "Reserved": 0.10,
    "Warm": 0.10,
    "Driven": 0.10,
    "Practical": 0.12,
    "Reflective": 0.10,
    "Curious": 0.12,
})


# ============================================================
# Generator
# ============================================================

class PersonaGenerator:
    def __init__(self, seed: Optional[int] = None) -> None:
        self.rng = random.Random(seed)

    def _choose_age(self) -> int:
        age_range = weighted_choice(
            self.rng,
            [((low, high), weight) for (low, high), weight in AGE_BUCKETS]
        )
        low, high = age_range
        return self.rng.randint(low, high)

    def _choose_experience(self, age: int) -> int:
        max_exp = max(0, age - 21)
        if max_exp <= 2:
            return self.rng.randint(0, max_exp)

        center = max(1, int((age - 22) * 0.65))
        exp = round(clamp(self.rng.gauss(center, 3.5), 0, max_exp))
        return int(exp)

    def _choose_seniority(self, years_experience: int) -> str:
        for (low, high), weights in SENIORITY_BY_EXPERIENCE:
            if low <= years_experience <= high:
                return weighted_choice(self.rng, weights)
        return "Mid-level"

    def _generate_big_five(
        self,
        communication_style: str,
        work_style: str,
        archetype_tags: List[str],
    ) -> BigFive:
        # Base middling realistic averages, then adjust.
        openness = 3.1
        conscientiousness = 3.2
        extraversion = 3.0
        agreeableness = 3.2
        neuroticism = 2.8

        if "Reserved and analytical" in communication_style:
            extraversion -= 0.7
            openness += 0.2
        elif "Energetic and expressive" in communication_style:
            extraversion += 0.9
        elif "Warm and collaborative" in communication_style:
            agreeableness += 0.6
        elif "Direct but polite" in communication_style:
            conscientiousness += 0.2
        elif "Diplomatic and measured" in communication_style:
            agreeableness += 0.3
            neuroticism -= 0.1

        if "Structured and methodical" in work_style:
            conscientiousness += 1.0
            openness -= 0.1
        elif "Collaborative and consensus-oriented" in work_style:
            agreeableness += 0.7
            extraversion += 0.2
        elif "Fast-moving and pragmatic" in work_style:
            conscientiousness -= 0.1
            extraversion += 0.3
            openness -= 0.1
        elif "Analytical and detail-focused" in work_style:
            conscientiousness += 0.8
            openness += 0.2
            extraversion -= 0.2
        elif "People-centered and facilitative" in work_style:
            agreeableness += 0.6
            extraversion += 0.3
        elif "Independent and self-directed" in work_style:
            extraversion -= 0.3
            openness += 0.3

        tag_set = set(archetype_tags)
        if "Analytical" in tag_set:
            openness += 0.3
            conscientiousness += 0.4
            extraversion -= 0.1
        if "Steady" in tag_set:
            neuroticism -= 0.5
            conscientiousness += 0.2
        if "Persuasive" in tag_set:
            extraversion += 0.7
            agreeableness -= 0.1
        if "Reserved" in tag_set:
            extraversion -= 0.8
        if "Warm" in tag_set:
            agreeableness += 0.6
        if "Driven" in tag_set:
            conscientiousness += 0.5
            neuroticism += 0.2
        if "Practical" in tag_set:
            openness -= 0.3
            conscientiousness += 0.1
        if "Reflective" in tag_set:
            openness += 0.4
            extraversion -= 0.4
        if "Curious" in tag_set:
            openness += 0.7

        return BigFive(
            openness=gaussian_rating(self.rng, openness),
            conscientiousness=gaussian_rating(self.rng, conscientiousness),
            extraversion=gaussian_rating(self.rng, extraversion),
            agreeableness=gaussian_rating(self.rng, agreeableness),
            neuroticism=gaussian_rating(self.rng, neuroticism),
        )

    def _choose_languages(self, macro_region: str, upbringing_context: str) -> List[str]:
        pool = LANGUAGE_POOLS.get(macro_region, LANGUAGE_POOLS["default"])
        count_weights = [(1, 0.55), (2, 0.35), (3, 0.10)]
        if "multilingual" in upbringing_context.lower():
            count_weights = [(1, 0.20), (2, 0.55), (3, 0.25)]

        count = weighted_choice(self.rng, count_weights)
        langs = weighted_sample_without_replacement(self.rng, pool, count)
        return langs

    def _make_name(self, gender: str) -> str:
        first = self.rng.choice(FIRST_NAMES.get(gender, FIRST_NAMES["Prefer not to say"]))
        last = self.rng.choice(LAST_NAMES)
        return f"{first} {last}"

    def _compose_description(
        self,
        name: str,
        age: int,
        cultural: CulturalBackground,
        professional: ProfessionalBackground,
        big_five: BigFive,
        archetype_tags: List[str],
    ) -> str:
        tone_map = {
            "Direct but polite": "comes across as straightforward without being abrasive",
            "Warm and collaborative": "is easy to talk to and tends to put others at ease",
            "Reserved and analytical": "usually listens first and speaks when there is something useful to add",
            "Energetic and expressive": "brings visible energy into group settings",
            "Diplomatic and measured": "chooses words carefully and rarely escalates tension",
        }

        trait_snippets = []
        if big_five.conscientiousness >= 4:
            trait_snippets.append("reliable and organized")
        if big_five.extraversion >= 4:
            trait_snippets.append("comfortable taking up social space")
        if big_five.extraversion <= 2:
            trait_snippets.append("more comfortable observing before speaking")
        if big_five.openness >= 4:
            trait_snippets.append("curious and open to new ideas")
        if big_five.agreeableness >= 4:
            trait_snippets.append("cooperative in tense situations")
        if big_five.neuroticism >= 4:
            trait_snippets.append("prone to second-guessing under pressure")
        if big_five.neuroticism <= 2:
            trait_snippets.append("fairly calm under pressure")

        primary_trait = ", ".join(trait_snippets[:3]) if trait_snippets else "fairly balanced in temperament"
        tag_phrase = ", ".join(archetype_tags[:2])

        return (
            f"{name} is a {age}-year-old {professional.function.lower()} working in "
            f"{professional.sector.lower()}. They were {cultural.upbringing_context.lower()} "
            f"and now {cultural.current_setting.lower()}. In conversation, {tone_map[cultural.communication_style]}. "
            f"They tend to be {primary_trait}, with a generally {tag_phrase.lower()} style."
        )

    def _build_llm_prompt_block(self, persona: Persona) -> str:
        return (
            f"PERSONALITY PROFILE:\n\n"
            f"Name: {persona.name}\n"
            f"Gender: {persona.gender}\n"
            f"Age: {persona.age}\n\n"
            f"Description:\n{persona.description}\n\n"
            f"Big Five Traits (1-5 scale):\n"
            f"- Openness: {persona.big_five.openness}\n"
            f"- Conscientiousness: {persona.big_five.conscientiousness}\n"
            f"- Extraversion: {persona.big_five.extraversion}\n"
            f"- Agreeableness: {persona.big_five.agreeableness}\n"
            f"- Neuroticism: {persona.big_five.neuroticism}\n\n"
            f"Cultural Background:\n"
            f"- Macro-region: {persona.cultural_background.macro_region}\n"
            f"- Upbringing: {persona.cultural_background.upbringing_context}\n"
            f"- Current setting: {persona.cultural_background.current_setting}\n"
            f"- Communication style: {persona.cultural_background.communication_style}\n"
            f"- Languages: {', '.join(persona.cultural_background.languages)}\n\n"
            f"Professional Background:\n"
            f"- Sector: {persona.professional_background.sector}\n"
            f"- Function: {persona.professional_background.function}\n"
            f"- Seniority: {persona.professional_background.seniority}\n"
            f"- Education: {persona.professional_background.education_level}\n"
            f"- Work style: {persona.professional_background.work_style}\n"
            f"- Years of experience: {persona.professional_background.years_experience}\n\n"
            f"Behavioral directive:\n"
            f"Reflect this persona through tone, confidence, conflict style, social assertiveness, "
            f"and decision-making under uncertainty. Do not explicitly name the Big Five traits; "
            f"express them through behavior."
        )

    def generate_persona(self, persona_id: Optional[str] = None) -> Persona:
        gender = weighted_choice(self.rng, GENDER_WEIGHTS)
        age = self._choose_age()
        years_experience = self._choose_experience(age)

        macro_region = weighted_choice(self.rng, MACRO_REGIONS)
        upbringing_context = weighted_choice(self.rng, UPBRINGING_CONTEXTS)
        current_setting = weighted_choice(self.rng, CURRENT_SETTINGS)
        communication_style = weighted_choice(self.rng, COMMUNICATION_STYLES)
        languages = self._choose_languages(macro_region, upbringing_context)

        sector = weighted_choice(self.rng, SECTORS)
        function = weighted_choice(self.rng, FUNCTIONS_BY_SECTOR[sector])
        seniority = self._choose_seniority(years_experience)
        education_level = weighted_choice(self.rng, EDUCATION_BY_SECTOR[sector])
        work_style = weighted_choice(self.rng, WORK_STYLES)

        archetype_tags = weighted_sample_without_replacement(self.rng, ARCHETYPE_TAGS, 2)
        big_five = self._generate_big_five(communication_style, work_style, archetype_tags)

        name = self._make_name(gender)
        cultural = CulturalBackground(
            macro_region=macro_region,
            upbringing_context=upbringing_context,
            current_setting=current_setting,
            communication_style=communication_style,
            languages=languages,
        )
        professional = ProfessionalBackground(
            sector=sector,
            function=function,
            seniority=seniority,
            education_level=education_level,
            work_style=work_style,
            years_experience=years_experience,
        )

        temp_persona = Persona(
            id=persona_id or f"persona_{self.rng.randint(1000, 9999)}",
            name=name,
            gender=gender,
            age=age,
            description="",
            archetype_tags=archetype_tags,
            big_five=big_five,
            cultural_background=cultural,
            professional_background=professional,
            llm_prompt_block="",
        )

        description = self._compose_description(
            name=name,
            age=age,
            cultural=cultural,
            professional=professional,
            big_five=big_five,
            archetype_tags=archetype_tags,
        )
        temp_persona.description = description
        temp_persona.llm_prompt_block = self._build_llm_prompt_block(temp_persona)
        return temp_persona

    def generate_many(self, n: int) -> List[Persona]:
        return [self.generate_persona() for _ in range(n)]


# ============================================================
# Output helpers
# ============================================================

def persona_to_dict(persona: Persona) -> Dict:
    return asdict(persona)


def print_persona(persona: Persona) -> None:
    print("=" * 80)
    print(f"{persona.name} ({persona.id})")
    print(f"Gender: {persona.gender} | Age: {persona.age}")
    print(f"Tags: {', '.join(persona.archetype_tags)}")
    print()
    print(persona.description)
    print()
    print("Big Five")
    print(f"  Openness:          {persona.big_five.openness}")
    print(f"  Conscientiousness: {persona.big_five.conscientiousness}")
    print(f"  Extraversion:      {persona.big_five.extraversion}")
    print(f"  Agreeableness:     {persona.big_five.agreeableness}")
    print(f"  Neuroticism:       {persona.big_five.neuroticism}")
    print()
    print("Cultural Background")
    print(f"  Macro-region:      {persona.cultural_background.macro_region}")
    print(f"  Upbringing:        {persona.cultural_background.upbringing_context}")
    print(f"  Current setting:   {persona.cultural_background.current_setting}")
    print(f"  Communication:     {persona.cultural_background.communication_style}")
    print(f"  Languages:         {', '.join(persona.cultural_background.languages)}")
    print()
    print("Professional Background")
    print(f"  Sector:            {persona.professional_background.sector}")
    print(f"  Function:          {persona.professional_background.function}")
    print(f"  Seniority:         {persona.professional_background.seniority}")
    print(f"  Education:         {persona.professional_background.education_level}")
    print(f"  Work style:        {persona.professional_background.work_style}")
    print(f"  Experience:        {persona.professional_background.years_experience} years")
    print()
    print("LLM Prompt Block")
    print("-" * 80)
    print(persona.llm_prompt_block)
    print("-" * 80)


# ============================================================
# Example CLI usage
# ============================================================

if __name__ == "__main__":
    generator = PersonaGenerator(seed=42)

    personas = generator.generate_many(5)

    for p in personas:
        print_persona(p)

    with open("personas.json", "w", encoding="utf-8") as f:
        json.dump([persona_to_dict(p) for p in personas], f, indent=2, ensure_ascii=False)

    print("\nSaved 5 personas to personas.json")
```

A few design notes:

* The distributions are “realistic” in the sense of being **plausible and varied**, not statistically representative of any particular country or population.
* The script deliberately avoids hard-linking cultural background to profession in a stereotyped way.
* Big Five traits are not purely random; they are nudged by communication style, work style, and archetype tags.

!!!! For your Mafia / focus-group use case, the next useful extension is to generate:

1. a **private persona record**,
2. a **public-facing self-presentation**, and
3. a **behavior policy block** derived from the Big Five, such as talkativeness, conflict style, trust threshold, and vote volatility.

Here’s a compact example of the kind of output block this script already emits for direct use in an LLM role prompt:

```text
PERSONALITY PROFILE:

Name: Maya Turner
Gender: Female
Age: 34

Description:
Maya Turner is a 34-year-old data analyst / scientist working in technology. They were raised in a highly education-oriented household and now live in a major metro area. In conversation, they usually listen first and speak when there is something useful to add. They tend to be reliable and organized, curious and open to new ideas, more comfortable observing before speaking, with a generally analytical, reflective style.

Big Five Traits (1-5 scale):
- Openness: 4
- Conscientiousness: 5
- Extraversion: 2
- Agreeableness: 3
- Neuroticism: 2
```

I can also turn this into a version that outputs directly into your earlier Mafia-agent prompt schema.


````
