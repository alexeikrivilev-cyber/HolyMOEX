# ArenaGo Integration Contract

Текст описания — русский. Программные сущности — английский.

## Provider

`provider`: `arena_go`

## Endpoints

| `request_type` | Method | Path |
|---|---|---|
| `submit_order` | `POST` | `/submit_order` |
| `get_trades` | `GET` | `/trades/{portfolio}` |
| `get_positions` | `GET` | `/positions/{portfolio}` |
| `get_bots` | `GET` | `/bots` |

## Auth

Primary token source: `SANDBOX_API_KEY`.

Local/dev fallback: `ARENA_GO_TOKEN`.

```http
Authorization: ${resolved_arena_go_token}
```

`resolved_arena_go_token` is selected by runtime from `SANDBOX_API_KEY` first, then `ARENA_GO_TOKEN` only as a local/dev fallback. The raw token must not be logged; logs and audit may contain only the token source or a masked value.

## Submit order payload

```json
{
  "direction": "B | S",
  "secid": "string",
  "quantity": 0,
  "bot": "string"
}
```

## Error mapping

| Raw error | `error_code` |
|---|---|
| `ERROR: MARKET CLOSED` | `market_closed` |
| `ERROR: NOT VALID SECID` | `invalid_instrument` |
| `ERROR: INSUFFICIENT CASH` | `insufficient_cash` |
| `ERROR: BOT {bot_name} HAS REACHED DAILY TRADE LIMIT` | `daily_trade_limit_reached` |
