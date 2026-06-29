"""Test that the create-job skill exists and has required content."""

from pathlib import Path

from harness.skills import load_catalog_with_skips, _parse_skill_md


def test_create_job_skill_exists():
    """Test that create-job skill is in the catalog."""
    skills_root = Path(__file__).resolve().parents[2] / "harness" / "skills"
    catalog = load_catalog_with_skips([skills_root]).skills

    skill_names = {skill.name for skill in catalog}
    assert "create-job" in skill_names, f"create-job not in catalog. Found: {skill_names}"


def test_create_job_skill_has_required_content():
    """Test that create-job skill body contains all required substrings."""
    skills_root = Path(__file__).resolve().parents[2] / "harness" / "skills"
    skill_md_path = skills_root / "create-job" / "SKILL.md"

    assert skill_md_path.is_file(), f"SKILL.md not found at {skill_md_path}"

    data, body = _parse_skill_md(skill_md_path)

    body_lower = body.lower()

    # The gate CONCEPTS are still documented (their values get defaulted, not
    # interrogated), plus the create_job tool is the create path.
    required_substrings = [
        "timeout",
        "min-cadence",
        "consecutive",
        "create_job",          # the agent tool is the only create path
        "default",             # guess-first: apply safe defaults
        "when to ask",         # the focused-question section (schedule / risky permission)
    ]

    for substring in required_substrings:
        assert substring in body_lower, (
            f"Required substring '{substring}' not found in create-job skill body"
        )

    # The OLD rigid behavior must be GONE — "never create unless every gate is
    # answered" manufactured the gate loop (re-asking all four instead of
    # creating). Match the BEHAVIOR phrase, not a bare "fail closed" (which can
    # legitimately appear when describing the tool's validation) to avoid a
    # spurious failure.
    for banned in ("never create a job unless every gate",
                   "only when all four gates are answered",
                   "if any gate is unanswered"):
        assert banned not in body_lower, (
            f"create-job skill must NOT reinstate rigid gate language "
            f"({banned!r}); use guess-first defaults instead"
        )


def test_create_job_skill_has_description():
    """Test that create-job skill has a description."""
    skills_root = Path(__file__).resolve().parents[2] / "harness" / "skills"
    catalog = load_catalog_with_skips([skills_root]).skills

    skill = next((s for s in catalog if s.name == "create-job"), None)
    assert skill is not None, "create-job skill not found"

    description_lower = skill.description.lower()
    # Should mention cron/scheduled/recurring or creating a job/task
    assert any(
        keyword in description_lower
        for keyword in [
            "cron",
            "scheduled",
            "recurring",
            "job",
            "task",
            "reminder",
        ]
    ), f"Description should mention a cron/scheduled/job concept: {skill.description}"
