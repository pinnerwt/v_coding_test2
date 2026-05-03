# Coding test - web agent

## How to run
### Server
```bash
# exports the env vars from .env (notably DEEPSEEK_API_KEY) so the uvicorn process inherits them 
cd task2/
set -a && . ./.env && set +a && AGENT_RESTRICT_GOTO=true uv run uvicorn agent.server:app_factory --factory --host 127.0.0.1 --port 8001
```

Then connect to http://127.0.0.1:8001/

### Cost analysis
```bash
uv run python scripts/cost_report.py --all
```

## Key design decisions
1. React structure: including differnet tool calls (goto, read, click, type, ask user question, done, list_interactives). Conclude with `done` once the agent feels that the information is enough.
2. Benchmark testing: look at the trace and found that the agent 
 - does not have enough ability on certain tasks
 - Or hallucinate the answer before it even see it on the page
 - Return wrong reasoning/tool calls in certain cases
3. read() function: was called very often when the LLM does not know what to do. So I created a caching system with hash that auto-add the offset if the old context has been seen.
4. note() to write down notes for future LLM usage.
5. Wanted to try agentic RAG, chunked the webpage so that we can skip different read/read_grep calls. However, once we use this, we will lose the context on the order of the content. For example, we will not be able to answer "what is the third headline on BBC news". Gave up in the end.
6. Do not handle captcha/anti bot/login walls for now. If we do want to bypass this, we would need a cleaner IP (instead of zeabur common cluster) and a X server in the docker (xvfb), and use a real browser with a MCP or mouse/keyboard control. Did it with my Openclaw but it took a lots of time and lots of tokens (see 7.).
7. Vision: use the vision directly. This includes screenshots different webpage and scroll/click at different coordinates. This is for me the easiest way as a web agent: tool calls are clear and much less. However, the cons in this method is that it simply costs too many tokens. I think this is why Claude was shipped with this.

## Where AI helped me
1. Implement the TDD/e2e tests
2. Implement all the codes. 0 codes were written by me.
3. Created a skill to 
  - restart the server (update the code module after fixes)
  - run latest failed benchmark results
  - identify any hallucination first. However this parts often failed without human in the loop.
  - identify loops in agent.
  - Write down the observations. The fix are often wrongly identified in last try and thus we need to plan further and add more human insight for the design part.
4. Brainstorming on different topics, but felt that it spotted the wrong error in most of time.
5. Also asked AI to search on internet on certain design decisions, for the hallucination part. It suggests gating "goto" or "answer" with what we didn't see and it works fine in benchmarks.

## Lesson from [first trial](https://github.com/pinnerwt/v_coding_test)
1. Planner/loop separation is a huge waste of time.
2. Openspec is the most token consuming during developement cycle.
3. Local qwen3.5-27b was slow and benchmarks took much longer than expected.
4. ci/CLAUDE.md/function calls reused easily.
5. Design pattern reused, while merging planner/loop into the same loop instance.
