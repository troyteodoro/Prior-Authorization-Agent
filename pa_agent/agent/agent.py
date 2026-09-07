from google.adk.agents.llm_agent import Agent

from pa_agent.model_pin import PINNED_MODEL

# Still the `adk create` template T-15 replaces. It reads the pin rather than a
# literal of its own (D20) -- the undocumented model that used to sit here was
# a third identifier disagreeing with the other two, measured on nothing.
root_agent = Agent(
    model=PINNED_MODEL,
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='Answer user questions to the best of your knowledge',
)
