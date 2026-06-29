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

    required_substrings = [
        "timeout",
        "min-cadence",
        "consecutive",
        "fail closed",
        "create_job",          # the agent tool (replaces the old harness/create_job ext-method ref)
    ]

    for substring in required_substrings:
        assert substring in body_lower, (
            f"Required substring '{substring}' not found in create-job skill body"
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
