# The grading-jobs message

This is the agreement between the service that SENDS grading jobs (the assessment service)
and the service that READS them (the grader worker). Both must follow it. If you need to
change it, change this file first and raise the version number below.

A submission that is waiting for a grade becomes one job. The grader reads the job, grades
the work against the rubric, and sends the answer back as a message on `grading-results`
(see `grading-results.md`).

## The topic

| | |
|---|---|
| Topic | `grading-jobs` |
| Lanes (partitions) | 6 |
| Messages are kept for | 7 days |
| Where bad messages go | `grading-jobs.dead-letter` (see below) |

All topics are created by the job in `kafka/base/topics-job.yaml`. There are 6 lanes because
grading (an AI call) is the slow step, and the number of lanes is the most grader workers
that can work at the same time.

## The message

**The key** is the submission ID, written as text (a UUID). Jobs for the same submission
always land in the same lane, in order.

**The value** is one JSON object in UTF-8 with exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `schema_version` | number | Must be `1`. Any other number is refused. |
| `submission_id` | UUID text | The submission to grade. Must equal the key. |
| `based_on_version` | whole number | 1 or more. The version of the submission being graded. The grade must come back for this same version. |
| `assignment_id` | UUID text | The assignment it belongs to. |
| `rubric` | text | 1 to 10,000 characters. The assignment's grading instructions, copied in so the grader does not have to ask for them. |
| `student_work` | text | 1 to 20,000 characters. |

An example:

```json
{
  "schema_version": 1,
  "submission_id": "3f2b8c1e-9a4d-4e7b-8c55-0d1f6a7b9e21",
  "based_on_version": 1,
  "assignment_id": "a7d1c5f0-2b6e-4f3a-9d84-5e0c1b7a3f68",
  "rubric": "Grade for clarity and evidence.",
  "student_work": "Photosynthesis turns light into sugar."
}
```

**Text rules** are the same as for the assessment service's own routes: a null character or
anything that cannot be written as valid Unicode is refused, and a text that is only spaces
is blank.

**The limits must fit at both ends.** The sender must never be able to create a job the
reader will refuse. The sender's limits are the assessment service's own limits, and the
reader accepts the same:

| Field | The sender accepts (assessment service) | The reader accepts (grader worker) |
|---|---|---|
| `rubric` | 10,000 characters | 10,000 characters |
| `student_work` | 20,000 characters | 20,000 characters |

If you change a limit in either service, change the other service and this table in the same
piece of work. Each service gets a test that fails if its own limit drifts from this table.
Those tests arrive with the sender and with the reader. Until then this table is the
agreement.

**No extra fields.** A field that is not in the table is a mistake and the message is
refused. To add or change a field, raise `schema_version` to 2 and update this file. The
reader refuses any version it does not know.

**Left out on purpose:** the student's name and anything else that identifies a person.
Student names live only in the assessment service. The grader must never see them. Keep it
that way: grading must not depend on, or leak, who the student is.

## What the sender must do

- Send one job when a submission is waiting for a grade: its newest version is "submitted"
  (a first submission) or "rejected" (an instructor sent it back for a new grade).
- Put the submission ID in both the key and `submission_id`, and the waiting version in
  `based_on_version`.
- Send messages that follow the rules above. The reader will not repair a bad one.
- Sending a job again is harmless. The first grade recorded for a version wins, and the
  assessment service refuses the others as out of date.

## What the reader does

- It reads as one team (a consumer group), so several workers share the lanes, and marks a
  message done only AFTER it has dealt with it, so a crash never loses one.
- **A good job** is graded, and the answer is sent on `grading-results`.
- **A message that can never be graded** is copied to `grading-jobs.dead-letter`, with a
  short reason in a header called `error`, and then marked done so it cannot block the
  messages behind it. This covers: not valid JSON, an unknown `schema_version`, a rule above
  broken, and a key that does not equal `submission_id`.
- **Temporary trouble**, such as the AI service being unreachable, is not marked done. The
  message is tried again.

The reason codes in the `error` header so far are: `invalid_json`, `unknown_schema_version`,
`invalid_field` and `key_mismatch`. More may be added, in this file, when the worker is built.
The reader never puts student text in its logs.

## The dead-letter topic

`grading-jobs.dead-letter` has 1 lane and keeps messages for 30 days, so a person has time
to look. Each message there is an exact copy of the original (same key, same value) plus the
`error` header. Nothing reads it automatically. A person decides what to do with each one.
