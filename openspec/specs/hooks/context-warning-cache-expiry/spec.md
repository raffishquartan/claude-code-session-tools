# hooks/context-warning-cache-expiry Specification

## Purpose

Defines how the context-window warning tells the user when the prompt cache expires and what the next turn costs if it has, so the choice between compacting now and carrying on is informed.

## Requirements

### Requirement: The warning states when the cache expires
The visible warning line SHALL show, in its time parenthesis, the local time (24-hour `HH:MM`) at which the prompt cache expires, in the form `(<now>, cached till <expiry>)`. The expiry SHALL be the timestamp of the transcript entry that supplied the context size, plus the cache lifetime in use. It is an estimate: the response the warning itself triggers refreshes the cache a few seconds later, so the true expiry is never earlier than shown.

#### Scenario: Expiry crosses midnight
- **WHEN** the last response was at 23:40 local time and the lifetime is one hour
- **THEN** the line reads `cached till 00:40`

#### Scenario: Malformed timestamp
- **WHEN** the entry supplying the usage has an unparseable or missing timestamp
- **THEN** the hook reports a transcript error on stderr and exits 1 without a warning, like any other unreadable transcript

#### Scenario: One-hour cache
- **WHEN** the last API response was at 15:13 local time and the most recent cache write in the transcript used the one-hour lifetime
- **THEN** the line reads `cached till 16:13`

#### Scenario: Five-minute cache
- **WHEN** the last API response was at 15:13 local time and the most recent cache write used the five-minute lifetime
- **THEN** the line reads `cached till 15:18`

#### Scenario: Both lifetimes written in one response
- **WHEN** the most recent cache-writing response wrote both five-minute and one-hour tokens
- **THEN** the one-hour lifetime applies

#### Scenario: A write with no lifetime breakdown
- **WHEN** the most recent cache-writing response recorded a write total but no per-lifetime breakdown
- **THEN** the five-minute lifetime applies, the API's default

#### Scenario: Reads only on the last turn
- **WHEN** the last API response only read the cache and wrote nothing
- **THEN** the lifetime comes from the most recent earlier response that wrote to the cache
- **AND** with no earlier write at all the lifetime is five minutes

### Requirement: The warning states the cost with a cold cache
The cost clause SHALL give both prices: `~$<warm>/turn cache reads, ~$<cold>/turn uncached`. `<cold>` is the cost of writing the whole current context to the cache at the model's cache-write price for the lifetime in use, which is what the next turn pays once the cache has expired. Both amounts are truncated to two decimal places.

#### Scenario: Sonnet 5.5, one-hour cache
- **WHEN** the context is 500,000 tokens, the model is `claude-sonnet-5-5` and the lifetime is one hour
- **THEN** the clause reads `~$0.10/turn cache reads, ~$2.00/turn uncached`

#### Scenario: Five-minute lifetime uses the five-minute write price
- **WHEN** the context is 500,000 tokens, the model is `claude-sonnet-5-5` and the lifetime is five minutes
- **THEN** the uncached figure is `$1.25`

### Requirement: An unknown model uses stated default prices
For a model id missing from the model table, the warning SHALL keep its assumed-values wording and SHALL state that the uncached figure assumes the default cache-write price for the lifetime in use, alongside the default cache-read price it already states.

#### Scenario: Unknown model
- **WHEN** the model id is not in the table, the context is 200,000 tokens and the lifetime is one hour
- **THEN** the message still says the window is assumed
- **AND** it shows `cached till`, says it `assumes the default $10.00/MTok cache-write price`, and shows `~$2.00/turn uncached`
