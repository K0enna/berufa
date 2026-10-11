"""Pure shared interview headings and bounded preparation prompts."""

REQUIRED_LIVE_HEADINGS = (
    "Call objective",
    "Opening",
    "Proof anchors",
    "Panel map",
    "Questions",
    "Close and logistics",
)
REQUIRED_PREP_HEADINGS = (
    "Round hypothesis",
    "Requirement-to-evidence map",
    "Story bank",
    "Risk and gap handling",
    "Panel questions",
    "Logistics",
)

ROUND_QUESTIONS = {
    "recruiter": (
        "Which requirements are essential for progression to the next round?",
        "What are the confirmed work model, travel expectations and compensation structure?",
    ),
    "hiring_manager": (
        "What must this role change in its first six to twelve months?",
        "Which decisions would I own, and which depend on other teams?",
    ),
    "functional": (
        "Which real problem best represents the day-to-day scope of this role?",
        "How will you evaluate the tradeoffs, reasoning and results in this discussion?",
    ),
    "panel": (
        "Where do the participating functions depend on this role most?",
        "How are conflicting priorities and decision rights resolved across the panel's teams?",
    ),
    "final": (
        "Which expectations or concerns still need to be resolved before a decision?",
        "What resources, decision rights and success measures would be committed to this mandate?",
    ),
    "unspecified": (
        "What is the purpose and format of this round?",
        "Who will participate, and which evidence would be most useful to discuss?",
    ),
}
