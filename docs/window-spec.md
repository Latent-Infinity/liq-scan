# `WindowSpec`

The window controls *which* slice of the time series the engine
inspects when scoring a symbol. Three kinds are supported, declared
as a Pydantic discriminated union so the same shape works for both
the CLI parser (`parse_window_spec(...)`) and a programmatic
`ScanQuery(...)` call.

## Kinds

### `TradingMinutesWindow(n)`

Last `n` *trading* minutes ending at `as_of` — skips weekends and
holidays via the NYSE calendar (`liq.data.calendar`).

CLI shorthand: `--window trading_minutes:390` (one regular session).

### `CalendarWindow(duration)`

Wall-clock duration subtraction. No calendar awareness — useful for
intraday windows that don't care about session boundaries.

CLI shorthand: `--window calendar:2h` (units: `s|m|h|d`).

### `SessionsWindow(n)`

Open of session `N-1` back through `as_of`. `n=1` is "this session
from open to `as_of`."

CLI shorthand: `--window sessions:3` (last three trading days).

## Validation

* `n > 0` for trading-minutes / sessions.
* `duration > 0` for calendar.

## Resolution

Every kind exposes `.resolve(as_of) -> (start, end)`. The engine
calls it once per query to get the half-open `[start, end)` bounds
for `read_multi`.
