"""Agent roster (SPEC.md section 6): Planner, Collector, Keeper, Verifier, Analyst, Writer, Critic.

Each module exposes one or more run_<agent>(ctx, ...) -> AgentResult entry points called by runner.py in a fixed
order. Generator-backed roles are exactly Planner, Writer and the Critic second opinion; the rest are tool-only.
"""
