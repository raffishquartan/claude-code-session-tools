# cli/usage-model-pricing Specification

## Purpose

Defines how `claude-code-usage` turns an assistant message's model id and token mix into a dollar
cost, so cost reports reflect Anthropic's published first-party rates and never hide an estimated
or missing price.

## Requirements

### Requirement: Listed models are priced at their published rates
Cost reports SHALL price each token bucket at the following per-million-token rates (Anthropic's
published first-party prices as of 2026-09-29), in the order input / output / 5-minute cache
write / 1-hour cache write / cache read:

| Model ids | Rates (USD per million tokens) |
|---|---|
| `claude-fable-5-1`, `claude-mythos-5-1` | 10 / 50 / 12.50 / 20 / 0.25 |
| `claude-fable-5`, `claude-mythos-5` | 10 / 50 / 12.50 / 20 / 1 |
| `claude-opus-5-5` | 4 / 20 / 5 / 8 / 0.20 |
| `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-opus-4-5` | 5 / 25 / 6.25 / 10 / 0.50 |
| `claude-opus-4-1`, `claude-opus-4` | 15 / 75 / 18.75 / 30 / 1.50 |
| `claude-sonnet-5-5`, `claude-sonnet-5` | 2 / 10 / 2.50 / 4 / 0.20 |
| `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-sonnet-4` | 3 / 15 / 3.75 / 6 / 0.30 |
| `claude-haiku-4-5` | 1 / 5 / 1.25 / 2 / 0.10 |

#### Scenario: Opus 4.7 at the current Opus price
- **WHEN** a message on `claude-opus-4-7` uses 1,000,000 input and 1,000,000 output tokens
- **THEN** its cost is $30 ($5 input + $25 output), not the pre-4.5 Opus price

#### Scenario: Fable 5.1 has a real cost and its own cache-read rate
- **WHEN** a message on `claude-fable-5-1` reads 1,000,000 tokens from the cache
- **THEN** its cost is $0.25, and no pricing warning is logged for it

#### Scenario: Opus 5.5 and Sonnet 5.5
- **WHEN** a message uses 1,000,000 input and 1,000,000 output tokens on `claude-opus-5-5`, or on
  `claude-sonnet-5-5`
- **THEN** the cost is $24 for Opus 5.5 and $12 for Sonnet 5.5

### Requirement: A dated snapshot id is priced as its undated model
A model id consisting of a priced id followed by a hyphen and an eight-digit date SHALL be priced
exactly as the undated id, before any family fallback is considered.

#### Scenario: Dated Opus 4 keeps the Opus 4 price
- **WHEN** the model id is `claude-opus-4-20250514`
- **THEN** it is priced as `claude-opus-4` ($15 input / $75 output per million tokens), not as the
  newest Opus

#### Scenario: Dated Haiku 4.5
- **WHEN** the model id is `claude-haiku-4-5-20251001`
- **THEN** it is priced as `claude-haiku-4-5`

### Requirement: An unlisted id in a known family is priced as that family's newest model, visibly
A model id with no row (directly or as a dated snapshot) that contains `claude-fable-`,
`claude-mythos-`, `claude-opus-`, `claude-sonnet-` or `claude-haiku-` SHALL be priced as that
family's newest listed model (`claude-fable-5-1`, `claude-mythos-5-1`, `claude-opus-5-5`,
`claude-sonnet-5-5`, `claude-haiku-4-5` respectively), and the cost report SHALL log a warning
naming each such id and the model whose price it used.

#### Scenario: A future Opus id
- **WHEN** a report includes messages on `claude-opus-9`
- **THEN** they are priced as `claude-opus-5-5` and a warning names `claude-opus-9` as priced by
  family fallback

#### Scenario: A dated snapshot of an unlisted id
- **WHEN** the model id is `claude-sonnet-5-9-20261001`
- **THEN** it is priced as `claude-sonnet-5-5` by family fallback

### Requirement: A model with no price is reported, not guessed
A model id with no row and no family match SHALL be costed at 0, and the cost report SHALL log a
warning naming every such id. An unlisted Claude 3 id (`claude-3-...`) or an id where the family
word does not follow `claude-` SHALL NOT match a family.

#### Scenario: A non-Claude model
- **WHEN** a report includes messages on `gpt-oss-120b` or `magnum-opus-7b`
- **THEN** those messages cost 0 and a warning names them

#### Scenario: An unlisted Claude 3 model is not given a newer model's price
- **WHEN** a report includes messages on `claude-3-haiku-20240307`
- **THEN** those messages cost 0 and a warning names the id

### Requirement: Synthetic entries cost nothing and raise no warning
Assistant entries whose model is `<synthetic>` SHALL be costed at 0 without any pricing warning.

#### Scenario: A report containing a synthetic stub
- **WHEN** a report includes a `<synthetic>` entry
- **THEN** it contributes $0 and no pricing warning mentions it
