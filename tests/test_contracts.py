# In plain English: tests for the written message contracts in contracts/. A contract is
# only useful if it says what the code will rely on, so these tests read each contract and
# check it names its topics, its format version and exactly the fields we agreed, with the
# agreed limits. They need no Kubernetes and no network.
import re
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"

# contract file -> (topic, fields in the message value in order, limits as text to find in each row)
EXPECTED = {
    "grading-jobs.md": (
        "grading-jobs",
        {
            "schema_version": "1",
            "submission_id": "",
            "based_on_version": "1",
            "assignment_id": "",
            "rubric": "10,000",
            "student_work": "20,000",
            "feedback": "10,000",
        },
    ),
    "grading-results.md": (
        "grading-results",
        {
            "schema_version": "1",
            "submission_id": "",
            "based_on_version": "1",
            "ai_grade": "20",
            "ai_reasoning": "10,000",
        },
    ),
}


# In plain English: the rows of the first table under "The value": the message's fields.
def value_rows(text):
    rows = {}
    for line in text.split("**The value**")[1].splitlines():
        match = re.match(r"\| `(\w+)` \| (.*?) \| (.*?) \|", line)
        if match:
            rows[match.group(1)] = match.group(3)
        elif rows and not line.startswith("|"):
            break
    return rows


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_contract_names_its_topic_and_its_dead_letter_topic(name):
    topic = EXPECTED[name][0]
    text = (CONTRACTS / name).read_text(encoding="utf-8")
    assert f"`{topic}`" in text
    assert f"`{topic}.dead-letter`" in text


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_contract_lists_exactly_the_agreed_fields_with_their_limits(name):
    fields = EXPECTED[name][1]
    rows = value_rows((CONTRACTS / name).read_text(encoding="utf-8"))
    assert list(rows) == list(fields), "fields differ from the agreed list"
    for field, limit in fields.items():
        assert limit in rows[field], f"{field}: the row should mention {limit}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_contract_says_the_student_name_is_left_out_and_extra_fields_are_refused(name):
    text = (CONTRACTS / name).read_text(encoding="utf-8").lower()
    assert "student's name" in text
    assert "no extra fields" in text
    assert "schema_version" in text
    assert "both ends" in text or "both sides" in text


def test_the_results_contract_matches_what_the_grade_route_accepts():
    # POST /submissions/{id}/grades takes based_on_version, ai_grade (1-20) and ai_reasoning
    # (1-10,000). The results message carries exactly those, plus the version and the ID.
    rows = value_rows((CONTRACTS / "grading-results.md").read_text(encoding="utf-8"))
    assert {"based_on_version", "ai_grade", "ai_reasoning"} <= set(rows)


# A job sent again after an instructor rejected a grade must carry the instructor's reason,
# or the grader sees exactly the same input as before and the submission loops forever
# between graded and rejected. The field is optional: a first-time job has no feedback.
def test_the_jobs_contract_carries_the_instructors_feedback_on_a_regrade_and_says_it_is_optional():
    text = (CONTRACTS / "grading-jobs.md").read_text(encoding="utf-8")
    row = value_rows(text)["feedback"]
    assert "optional" in row.lower()
    assert "rejected" in row.lower()
    assert "left out" in row.lower() or "absent" in row.lower()
    # The reviewer's name is still not shared.
    assert "reviewer" in text.lower()


# The results route trims spaces from the grade only. It keeps the reasoning exactly as
# written. The contract must say so, not claim that both are trimmed.
def test_the_results_contract_describes_the_trimming_rules_truthfully():
    text = (CONTRACTS / "grading-results.md").read_text(encoding="utf-8")
    assert "exactly as written" in text
    assert "spaces at either end are trimmed" not in text


# The consumers put a reason code in the dead-letter header. Each code a consumer can produce must
# be written in its contract, or a person reading the dead-letter topic meets a code nobody
# explained.
@pytest.mark.parametrize(
    "name, codes",
    [
        ("approved-examples.md", ["invalid_json", "unknown_schema_version", "invalid_field", "key_mismatch", "label_conflict", "save_rejected"]),
        ("grading-results.md", ["invalid_json", "unknown_schema_version", "invalid_field", "key_mismatch", "unknown_submission", "unknown_version", "record_rejected"]),
        ("grading-jobs.md", ["invalid_json", "unknown_schema_version", "invalid_field", "key_mismatch"]),
    ],
)
def test_every_reason_code_the_readers_use_is_written_in_the_contract(name, codes):
    text = (CONTRACTS / name).read_text(encoding="utf-8")
    for code in codes:
        assert f"`{code}`" in text, f"{name} does not explain the reason code {code}"
