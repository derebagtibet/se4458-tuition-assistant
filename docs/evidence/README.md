# Execution evidence — 21 November 2026

| File | Observed result |
|---|---|
| [unit-tests.log](unit-tests.log) | Automated REST, MCP and agent tests; no cloud or live LLM required |
| [gateway-demo.log](gateway-demo.log) | Real local Kong → FastAPI → PostgreSQL: Swagger/OpenAPI 200, protected 401/200, cross-student 403, student quota 200/200/200/429, independent student quota, simulated payment 201, duplicate 409, CSV partial success and paging |
| [mcp-retrieval-demo.log](mcp-retrieval-demo.log) | Three queries over real MCP Streamable HTTP at localhost:8002, including policy section references |
| [gemini-key-check.log](gemini-key-check.log) | Successful real Gemini generation after funding the project and selecting the available `gemini-3.8-flash` model; no credentials in the log |
| [agent-mcp-only.log](agent-mcp-only.log) | Real Gemini selected only `search_tuition_policies`; answer cited Policy §2.1 |
| [agent-rest-only.log](agent-rest-only.log) | Real Gemini selected only `get_my_tuition`; gateway returned the logged-in student's 6000 USD overall balance and term breakdown |
| [agent-combined.log](agent-combined.log) | Real Gemini selected both live tuition and MCP policy tools; answer cited installment rules and explained approval is required |
| [agent-payment-confirmed.log](agent-payment-confirmed.log) | Real Gemini selected `pay_tuition`; the agent showed the confirmation summary (S1001, 2026-FALL, 500.00 USD, 3000 → 2500), the student typed `yes`, and the Pay API via Kong returned `SUCCESS` with remaining balance 2500 USD. Recorded with `scripts/record_payment_demo.bat` |
| [cloud-gateway-demo.log](cloud-gateway-demo.log) | **Azure:** the same gateway checks against the deployed Kong (Swagger/OpenAPI 200, 401/200, 403, 200/200/200/429, independent quota, payment 201, duplicate 409, CSV, paging). `Overall result: PASSED` |
| [cloud-mcp-retrieval-demo.log](cloud-mcp-retrieval-demo.log) | **Azure:** three retrieval queries over MCP Streamable HTTP against the deployed MCP server (§2.1, §8.2/§3.4, §4.1) |
| [cloud-agent-demo.log](cloud-agent-demo.log) | **Azure:** 5-question agent demo against the cloud gateway and MCP: MCP-only, REST-only, installments, confirmed payment (`yes` → `SUCCESS`, 3000 → 2500) and refusal for another student's balance. In this session the installment question reused the balance from the previous answer and called only MCP; see `cloud-agent-combined.log` for a fresh-session run. Traces in [agent-cloud/](agent-cloud/) |
| [cloud-agent-combined.log](cloud-agent-combined.log) | **Azure, combined MCP + REST in a fresh session:** in one step Gemini selected both `get_my_tuition` (Kong → API → PostgreSQL, live balance 5500 USD after the earlier payment) and `search_tuition_policies` (MCP, §3.1); the answer combines both and cites §3.1, §3.3, §3.4 |
| [agent/](agent/) | Complete JSON tool selections, inputs, results and final answers for the three live read scenarios and the confirmed payment (`20261121-161102-86d423.json`) |
| [agent-live-attempt.log](agent-live-attempt.log) | Historical failed attempt (HTTP 402), resolved after funding the project; retained as troubleshooting history |

Still needed: the demo video and GitHub publication. The live agent payment confirmation is recorded above; the three read scenarios made no payment calls.

Reproduce from the project root after installing dependencies:

```bash
python scripts/run_tests.py
docker compose up --build -d
python scripts/gateway_demo.py
python scripts/mcp_demo.py
```

The first two Python scripts save their logs automatically. Capture `mcp_demo.py` stdout as UTF-8 when refreshing its log. Gemini quota and real function calling are now verified. Run `python -m tuition_agent.cli --username s1001 --trace-dir ../docs/evidence/agent --demo` from `agent/`, enter the demo password, then confirm the displayed payment yourself. Save the terminal output and video; the agent saves complete tool/result/answer JSON traces. Tokens and API keys must not appear in evidence.
