# Roadmap (draft)

Priorities and ordering are proposals; adjust to taste.

## Done (v0.1)

- Single Finnhub upstream connection managed by lifespan, with reconnect and backoff
- Ref-counted symbol subscriptions and browser connection fan-out
- In-memory rule engine (above, below, percent change) with signed, retried webhooks
- Redis latest-price cache; TimescaleDB persistence
- React dashboard: subscribe, live ticker, rule form and list
- Offline pytest suite and GitHub Actions CI

## Next - hardening

- Run the full stack end to end against real Finnhub and Docker Compose; fix what surfaces
- Authentication for the REST and WebSocket API (currently open)
- Webhook safety: block private/internal destination URLs (SSRF), cap rule counts
- Make TimescaleDB tick table a hypertable with retention and compression policy
- Structured logging and basic metrics (tick rate, webhook latency and failures)

## Later - features

- Rule cooldowns and re-arm controls; rule edit (not just create/delete)
- Volatility rules beyond simple percent change
- Price history charts on the dashboard from stored ticks
- Webhook delivery log and manual replay
- Alternative notification channels (email, Slack)

## Later - platform

- CD: build and push images, deploy on tag
- Optional Alpaca data provider behind the same client interface
- Multi-instance support (shared subscription state, since Finnhub allows one upstream connection)
- Frontend end-to-end tests
