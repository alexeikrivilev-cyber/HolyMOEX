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

```http
Authorization: ${ARENA_GO_TOKEN}
```

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
