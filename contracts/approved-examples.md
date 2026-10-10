# The approved-examples message

This is the agreement between the service that SENDS approved examples (the assessment
service) and the service that READS them (the knowledge service). Both must follow it. If
you need to change it, change this file first and raise the version number below.

When an instructor approves a grade, the essay and its grade become an example the grader
can learn from later. This message carries that example from one service to the other.

## The topic

| | |
|---|---|
| Topic | `approved-examples` |
| Lanes (partitions) | 3 |
| Messages are kept for | 7 days |
| Where bad messages go | `approved-examples.dead-letter` (see below) |

Both topics are created by the job in `kafka/base/topics-job.yaml`.

## The message

**The key** is the submission ID, written as text (a UUID such as
`3f2b8c1e-9a4d-4e7b-8c55-0d1f6a7b9e21`). It means repeats of the same submission always
land in the same lane.

**The value** is one JSON object in UTF-8 with exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `schema_version` | number | Must be `1`. Any other number is refused. |
| `source_submission_id` | UUID text | The submission this example came from. Must equal the key. |
| `assignment_id` | UUID text | The assignment it belongs to. |
| `student_work` | text | 1 to 20,000 characters. |
| `grade` | text | 1 to 100 characters. |
| `reasoning` | text | 1 to 10,000 characters. |

An example:

```json
{
  "schema_version": 1,
  "source_submission_id": "3f2b8c1e-9a4d-4e7b-8c55-0d1f6a7b9e21",
  "assignment_id": "a7d1c5f0-2b6e-4f3a-9d84-5e0c1b7a3f68",
  "student_work": "Photosynthesis turns light into sugar.",
  "grade": "A",
  "reasoning": "Clear and correct."
}
```

**Text rules** are the same as for saving an example through the web address: spaces at
either end are trimmed, and text must not contain a null character or anything that cannot
be written as valid Unicode.

**The limits must fit at both ends.** A sender must never be allowed to approve something
the reader will refuse, or a valid approval would end up in the dead-letter topic and never
be learned from. So for every field, the sender's own limit must be no bigger than the
reader's:

| Field | The sender accepts (assessment service) | The reader accepts (knowledge service) |
|---|---|---|
| `student_work` | 20,000 characters | 20,000 characters |
| `grade` | 20 characters | 100 characters |
| `reasoning` | 10,000 characters | 10,000 characters |

If you change a limit in either service, change the other service and this table in the
same piece of work. Each service gets a test that fails if its own limit drifts from this
table. The reader's test arrives with the reader, and the sender's test arrives with the
sender. Until the sender exists, the sender's column describes its current limits.

**No extra fields.** A field that is not in the table is a mistake and the message is
refused. To add or change a field, raise `schema_version` to 2 and update this file. The
reader refuses any version it does not know.

**Left out on purpose:** the student's name (student names live only in the assessment
service), the reviewer's name, and any timestamps. Keep it that way: this message goes to
a service that must not hold personal data.

## What the sender must do

- Send one message when a submission is approved. An approval is final, so there is at most
  one message per submission.
- Put the submission ID in both the key and `source_submission_id`.
- Send messages that follow the rules above. The reader will not repair a bad one.

## What the reader does

- It reads as one team (the group `knowledge-service-examples`) and marks a message done
  only AFTER it has dealt with it, so a crash never loses one.
- **A good, new message** is saved as an example.
- **A repeat** is recognised by its `source_submission_id` and saved nowhere a second time.
  Kafka may deliver a message more than once, and this is why that is harmless.
- **A message that can never be saved** is copied to `approved-examples.dead-letter`, with a
  short reason in a header called `error`, and then marked done so it cannot block the
  messages behind it. This covers: not valid JSON, an unknown `schema_version`, a rule above
  broken, a key that does not equal `source_submission_id`, and a `source_submission_id`
  already used by a different example, and a save the database refuses for that message alone
  (data it cannot store).
- **Temporary trouble**, such as the database being down, is not marked done. The message is
  tried again.

The reason codes in the `error` header are: `invalid_json`, `unknown_schema_version`,
`invalid_field`, `key_mismatch`, `label_conflict` and `save_rejected`. The reader never puts student text in
its logs.

## The dead-letter topic

`approved-examples.dead-letter` has 1 lane and keeps messages for 30 days, so a person has
time to look. Each message there is an exact copy of the original (same key, same value)
plus the `error` header. Nothing reads it automatically. A person decides what to do with
each one.
