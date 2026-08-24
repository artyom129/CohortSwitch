# Evaluation semantics

## Context

Every request includes a non-empty `subject_key` and an optional attributes object. Attributes are evaluated in memory and are not automatically persisted. CohortSwitch does not log the attributes object.

## Conditions and operators

Conditions address one top-level attribute. Missing values are distinct from JSON `null`. `exists` and `not_exists` test presence; other operators return false for a missing attribute.

Supported operators:

- equality: `equals`, `not_equals`
- membership: `in`, `not_in`, `contains`
- strings: `starts_with`, `ends_with`
- presence: `exists`, `not_exists`
- numeric: `greater_than`, `greater_or_equal`, `less_than`, `less_or_equal`
- versions: `semver_equal`, `semver_greater`, `semver_less`

Numeric comparison refuses booleans. Semantic versions use normalized PEP 440 parsing and return false for invalid inputs.

## Priority

The kill switch runs first. Enabled rules then run by ascending numeric priority. Conditions inside a rule use AND semantics. The first matching rule returns a direct variation or delegates to that rule's rollout. If no rule matches, the engine evaluates the flag-level progressive rollout, then the flag-level allocation, then the default variation.

## Bucket algorithm

The stable hash input is:

```text
namespace, project_id, environment_id, flag_key, subject_key, salt
```

Each component is prefixed with its byte length and fed to BLAKE2b. The first eight digest bytes become an unsigned integer and map modulo 10,000. Built-in `hash()` and random sampling are never used.

## Percentage rollout

API inputs use basis-point percentages that must sum to 10,000. They compile into cumulative exclusive end boundaries. A 5%/95% split becomes `enabled < 500` and `disabled < 10000`.

## Progressive rollout

Each stage has an activation timestamp, enabled percentage, served variation, and fallback variation. Evaluation selects the latest stage whose timestamp is not in the future and applies the same stable bucket. Before the first stage, the first stage's fallback variation is returned.

## Reason codes

- `flag_disabled`: kill switch returned the default variation
- `targeting_match`: a rule returned a direct variation
- `percentage_rollout`: a rule or flag allocation selected a bucket
- `progressive_rollout`: an active time-based stage selected a bucket
- `default`: no rule or rollout applied

