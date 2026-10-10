# The grading-results message

This is the agreement between the service that SENDS grades (the grader worker) and the
service that READS them (the assessment service). Both must follow it. If you need to change
it, change this file first and raise the version number below.

When the grader has graded a submission (see `grading-jobs.md`), the grade and its reasoning
come back as this message. The assessment service records it as the submission's next version.

## The topic

| | |
|---|---|
| Topic | `grading-results` |
| Lanes (partitions) | 3 |
| Messages are kept for | 7 days |
| Where bad messages go | `grading-results.dead-letter` (see below) |

All topics are created by the job in `kafka/base/topics-job.yaml`. Recording a grade is
quick, so 3 lanes are plenty.

## The message

**The key** is the submission ID, written as text (a UUID).

**The value** is one JSON object in UTF-8 with exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `schema_version` | number | Must be `1`. Any other number is refused. |
| `submission_id` | UUID text | The submission that was graded. Must equal the key. |
| `based_on_version` | whole number | 1 or more. The version the job asked to grade, copied back unchanged. |
| `ai_grade` | text | 1 to 20 characters. |
| `ai_reasoning` | text | 1 to 10,000 characters. |

An example:

```json
{
  "schema_version": 1,
  "submission_id": "3f2b8c1e-9a4d-4e7b-8c55-0d1f6a7b9e21",
  "based_on_version": 1,
  "ai_grade": "A",
  "ai_reasoning": "Clear and correct."
}
```

These are the same fields, with the same limits, that the assessment service's own route
`POST /submissions/{id}/grades` accepts. **Text rules** are the same too, and they differ
between the two texts: `ai_grade` has its spaces at either end trimmed, while `ai_reasoning`
is kept exactly as written. Neither may be blank, and neither may contain a null character
or anything that cannot be written as valid Unicode.

**The limits must fit at both ends.** The grader must never produce a grade the assessment
service will refuse:

| Field | The sender accepts (grader worker) | The reader accepts (assessment service) |
|---|---|---|
| `ai_grade` | 20 characters | 20 characters |
| `ai_reasoning` | 10,000 characters | 10,000 characters |

If you change a limit in either service, change the other service and this table in the same
piece of work. Each service gets a test that fails if its own limit drifts from this table.
Those tests arrive with the grader worker and with the reader. Until then this table is the
agreement.

**No extra fields.** A field that is not in the table is a mistake and the message is
refused. To add or change a field, raise `schema_version` to 2 and update this file. The
reader refuses any version it does not know.

**Left out on purpose:** the student's name, the essay and the rubric. The assessment service
already holds them. A result carries only the grade and why.

## What the sender must do

- Send one result per job it graded, with the job's `submission_id` and `based_on_version`
  copied back unchanged.
- Put the submission ID in both the key and `submission_id`.
- Send messages that follow the rules above. The reader will not repair a bad one.

## What the reader does

- It reads as one team (a consumer group) and marks a message done only AFTER it has dealt
  with it, so a crash never loses one.
- **A good result for a submission that is waiting** is recorded as the next version.
- **A repeat** is recognised: the assessment service answers a grade it already has with the
  same grade, and nothing is added. Kafka may deliver a message more than once, and this is
  why that is harmless.
- **An out-of-date result** (the submission has moved on, or is no longer waiting for a grade,
  for example because another grade for that version was recorded first) is a normal race,
  not an error. It is dropped, and only its submission ID and the word "outdated" are logged.
- **A message that can never be recorded** is copied to `grading-results.dead-letter`, with a
  short reason in a header called `error`, and then marked done so it cannot block the
  messages behind it. This covers: not valid JSON, an unknown `schema_version`, a rule above
  broken, a key that does not equal `submission_id`, a submission that does not exist, and a
  save the database refuses for that message alone (data it cannot store).
- **Temporary trouble**, such as the database being down, is not marked done. The message is
  tried again.

The reason codes in the `error` header are: `invalid_json`, `unknown_schema_version`,
`invalid_field`, `key_mismatch`, `unknown_submission` and `record_rejected`. The reader never
puts student text or reasoning in its logs.

## The dead-letter topic

`grading-results.dead-letter` has 1 lane and keeps messages for 30 days, so a person has time
to look. Each message there is an exact copy of the original (same key, same value) plus the
`error` header. Nothing reads it automatically. A person decides what to do with each one.
