/**
 * Example model identifiers shown as autocomplete hints in the agent form.
 * The backend routes by prefix (`openai/`, `anthropic/`, `local/`, `ollama/`)
 * and falls back to OpenAI-compatible routing, so any model id is accepted.
 */
export const MODEL_EXAMPLES = [
  "openai/gpt-4.1",
  "openai/gpt-4.1-mini",
  "anthropic/claude-sonnet-4-5",
  "anthropic/claude-haiku-4-5",
  "local/llama3.1",
  "ollama/llama3.1",
] as const;

export const MODEL_FIELD_HINT =
  "Provider prefix routes the call: openai/, anthropic/, local/ or ollama/. Without a prefix, OpenAI-compatible routing is used.";
